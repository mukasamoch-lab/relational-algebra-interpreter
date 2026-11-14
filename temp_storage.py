"""M1 temporary page files used by external operators.

Pages contain a four-byte (used_bytes, record_count) header followed by
two-byte record lengths and encoded records. The remaining bytes are zero.
Page output streams fragments straight to the disk manager, so no second
copy of an entire run is needed while its input frames remain pinned.
"""

from dataclasses import dataclass
import struct
from tempfile import TemporaryDirectory

from disk_manager import DiskManager
from slotted_page import PAGE_SIZE
from record_codec import FIXED_TYPES


HEADER = struct.Struct("<HH")
LENGTH = struct.Struct("<H")


class EncodedTuple:
    """Borrow a single decoded tuple and emit field fragments without a row copy."""

    def __init__(self, schema, row):
        self.schema = schema
        self.row = row

    def __len__(self):
        return sum(
            struct.calcsize(FIXED_TYPES[column[1]]) if column[1] in FIXED_TYPES else 2 + len(value.encode("utf-8"))
            for column, value in zip(self.schema, self.row)
        )

    def release_tuple(self):
        self.row = None

    def fragments(self):
        for column, value in zip(self.schema, self.row):
            if column[1] in FIXED_TYPES:
                yield struct.pack(FIXED_TYPES[column[1]], value)
            else:
                encoded = value.encode("utf-8")
                yield LENGTH.pack(len(encoded))
                yield encoded


@dataclass
class Run:
    disk: DiskManager
    pages: int
    rows: int


def page_records(frame):
    used, count = HEADER.unpack_from(frame.data)
    if used < HEADER.size or used > PAGE_SIZE:
        raise ValueError("invalid temporary page header")
    offset = HEADER.size
    for _ in range(count):
        if offset + LENGTH.size > used:
            raise ValueError("invalid temporary record length")
        (length,) = LENGTH.unpack_from(frame.data, offset)
        offset += LENGTH.size
        if offset + length > used:
            raise ValueError("temporary record extends past page")
        yield memoryview(frame.data)[offset:offset + length]
        offset += length
    if offset != used:
        raise ValueError("temporary page has unexpected trailing record bytes")


class TemporaryRuns:
    def __init__(self, pool):
        self.pool = pool
        self.directory = TemporaryDirectory(prefix="ra-sort-")
        self.files = []
        self.serial = 0

    def new_run(self):
        disk = DiskManager(f"{self.directory.name}/run-{self.serial}.db")
        self.serial += 1
        self.pool.for_file(disk)
        self.files.append(disk)
        return Run(disk, 0, 0)

    def write(self, records):
        run = self.new_run()
        disk = run.disk
        iterator = iter(records)
        pending = next(iterator, None)
        rows = 0
        pages = 0
        while pending is not None:
            page_id = disk.allocate_page()
            pending, count = disk.write_record_page(page_id, iterator, pending)
            pages += 1
            rows += count
        return Run(disk, pages, rows)

    def remove(self, run):
        if run.disk in self.files:
            self.pool.discard_file(run.disk)
            run.disk.remove()
            self.files.remove(run.disk)

    def close(self):
        for disk in list(self.files):
            self.pool.discard_file(disk)
            disk.remove()
            self.files.remove(disk)
        self.directory.cleanup()


class RunCursor:
    """One pinned frame per input run, with encoded records borrowed from it."""

    def __init__(self, run, pool):
        self.run = run
        self.pool = pool.for_file(run.disk)
        self.page_id = 0
        self.frame = None
        self.records = None
        self.slot = 0

    def position(self):
        return self.page_id, self.slot - 1

    def seek(self, position):
        self.close()
        self.page_id, slot = position
        self.slot = 0
        for _ in range(slot):
            self.next()
        return self.next()

    def next(self):
        while self.page_id < self.run.pages:
            if self.frame is None:
                self.frame = self.pool.fetch_page(self.page_id)
                self.records = iter(page_records(self.frame))
            record = next(self.records, None)
            if record is not None:
                self.slot += 1
                return record
            self.pool.unpin_page(self.page_id)
            self.frame = None
            self.records = None
            self.page_id += 1
            self.slot = 0
        return None

    def close(self):
        if self.frame is not None:
            self.pool.unpin_page(self.page_id)
            self.frame = None
            self.records = None


class PartitionWriter:
    """Append to one partition using a page offset and no buffered tuples."""

    def __init__(self, run):
        self.run = run
        self.used = PAGE_SIZE
        self.count = 0

    def append(self, record):
        length = len(record)
        if length + HEADER.size + LENGTH.size > PAGE_SIZE:
            raise ValueError("record cannot fit in a temporary partition page")
        if self.used + LENGTH.size + length > PAGE_SIZE:
            self.finish_page()
            self.run.disk.allocate_page()
            self.run.pages += 1
            self.used = HEADER.size
        offset = (self.run.pages - 1) * PAGE_SIZE + self.used
        self.run.disk.write_temp_fragment(offset, LENGTH.pack(length))
        offset += LENGTH.size
        for fragment in record.fragments():
            self.run.disk.write_temp_fragment(offset, fragment)
            offset += len(fragment)
        record.release_tuple()
        self.used += LENGTH.size + length
        self.count += 1
        self.run.rows += 1

    def finish_page(self):
        if self.count:
            self.run.disk.write_temp_fragment(
                (self.run.pages - 1) * PAGE_SIZE, HEADER.pack(self.used, self.count),
                completes_page=True,
            )
            self.count = 0
