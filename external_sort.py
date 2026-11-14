"""Bounded external merge sort over M1 temporary page files."""

from contextlib import contextmanager
from functools import cmp_to_key
import heapq
import struct

from executor import Operator, node_error, nodes
from record_codec import FIXED_TYPES, decode_record
from temp_storage import EncodedTuple, RunCursor, TemporaryRuns, page_records


class Head:
    def __init__(self, record, source, compare):
        self.record = record
        self.source = source
        self.compare = compare

    def __lt__(self, other):
        order = self.compare(self.record, other.record)
        return order < 0 or (order == 0 and self.source < other.source)


class Sort(Operator):
    def __init__(self, child, keys, catalog):
        super().__init__("sort", child.schema, child)
        self.pool = catalog.pool
        self.keys = keys
        names = [column[0] for column in self.schema]
        self.key_indices = []
        for name, direction in keys:
            if name not in names:
                node_error("sort", f"unknown column '{name}'")
            self.key_indices.append((names.index(name), direction))
        self.temporary = None
        self.output = None
        self.held_frames = []
        self.passes = 0
        self.initial_runs = 0
        self.input_pages = 0
        self.peak_frames = 0

    def _keys(self, record):
        result = {}
        offset = 0
        wanted = {index for index, _ in self.key_indices}
        for index, column in enumerate(self.schema):
            if column[1] in FIXED_TYPES:
                fmt = FIXED_TYPES[column[1]]
                if index in wanted:
                    result[index] = struct.unpack_from(fmt, record, offset)[0]
                offset += struct.calcsize(fmt)
            else:
                (length,) = struct.unpack_from("<H", record, offset)
                offset += 2
                if index in wanted:
                    result[index] = record[offset:offset + length]
                offset += length
        return result

    def compare(self, left, right, *, count=True):
        left_keys = self._keys(left)
        right_keys = self._keys(right)
        for index, direction in self.key_indices:
            if count:
                self.counts["comparisons"] += 1
            a, b = left_keys[index], right_keys[index]
            if self.schema[index][1] == "VARCHAR":
                order = self._compare_utf8(a, b)
            else:
                order = (a > b) - (a < b)
            if order:
                return -order if direction == "desc" else order
        return 0

    @staticmethod
    def _compare_utf8(left, right):
        for a, b in zip(left, right):
            if a != b:
                return (a > b) - (a < b)
        return (len(left) > len(right)) - (len(left) < len(right))

    @contextmanager
    def _io(self):
        before = self.pool.stats()
        child_before = self._child_io()
        try:
            yield
        finally:
            after = self.pool.stats()
            child_after = self._child_io()
            for key in ("page_reads", "page_writes"):
                metric = "reads" if key == "page_reads" else "writes"
                self.counts[key] += (
                    after[metric] - before[metric] - child_after[key] + child_before[key]
                )
            self.peak_frames = max(self.peak_frames, self.pool.peak_accounted_frames)

    def _child_io(self):
        return {
            key: sum(operator.counts[key] for operator in nodes(self.child))
            for key in ("page_reads", "page_writes")
        }

    def _input_records(self):
        while True:
            row = self.child.next()
            if row is None:
                break
            yield EncodedTuple(self.schema, row)
            row = None

    def open(self):
        run_capacity = self.pool.capacity - max(self.pool.reservations, default=0)
        if run_capacity < 3:
            node_error("sort", "external merge sort requires at least three frames")
        self.passes = 0
        self.initial_runs = 0
        self.input_pages = 0
        self.peak_frames = 0
        super().open()
        try:
            self.temporary = TemporaryRuns(self.pool)
            with self._io():
                # This preparatory spool permits pass 0 to use all B frames
                # after the child's pins and working state have been released.
                try:
                    with self.pool.working_memory(1):
                        source = self.temporary.write(self._input_records())
                finally:
                    self.child.close()
                self.input_pages = source.pages
                if not source.rows:
                    self.temporary.remove(source)
                    self.output = iter(())
                    return
                runs = []
                for start in range(0, source.pages, run_capacity):
                    references = []
                    try:
                        for page_id in range(start, min(start + run_capacity, source.pages)):
                            frame = self.pool.fetch_page(page_id, disk=source.disk)
                            self.held_frames.append(frame)
                            references.extend(page_records(frame))
                        references.sort(key=cmp_to_key(self.compare))
                        self.initial_runs += 1
                        if source.pages < run_capacity:
                            # The only run streams directly from its existing
                            # input frames; pass 0 writes no sorted output file.
                            self.passes = 1
                            self.output = iter(references)
                            return
                        runs.append(self.temporary.write(iter(references)))
                    finally:
                        if source.pages >= run_capacity:
                            references.clear()
                            self._release_frames()
                            frame = None
                self.temporary.remove(source)
                self.passes = 1
                fan_in = self.pool.capacity - 1
                while len(runs) > fan_in:
                    next_runs = []
                    for start in range(0, len(runs), fan_in):
                        group = runs[start:start + fan_in]
                        if len(group) == 1:
                            next_runs.append(group[0])
                        else:
                            merged = self._merge(group)
                            try:
                                next_runs.append(self.temporary.write(merged))
                            finally:
                                merged.close()
                            for run in group:
                                self.temporary.remove(run)
                    runs = next_runs
                    self.passes += 1
                self.output = self._merge(runs)
                self.passes += 1
        except BaseException:
            self.close()
            raise

    def _release_frames(self):
        for frame in self.held_frames:
            self.pool.unpin_page(frame.page_id, disk=frame.disk)
        self.held_frames.clear()

    def _merge(self, runs):
        cursors = [RunCursor(run, self.pool) for run in runs]
        heads = []
        try:
            for index, cursor in enumerate(cursors):
                record = cursor.next()
                if record is not None:
                    heapq.heappush(heads, Head(record, index, self.compare))
            while heads:
                head = heapq.heappop(heads)
                yield head.record
                record = cursors[head.source].next()
                if record is not None:
                    heapq.heappush(heads, Head(record, head.source, self.compare))
        finally:
            heads.clear()
            for cursor in cursors:
                cursor.close()

    def _next(self):
        with self._io(), self.pool.working_memory(1):
            record = next(self.output, None)
            if record is None:
                return None
            return decode_record(self.schema, record)

    def close(self):
        if self.output is not None:
            close = getattr(self.output, "close", None)
            if close is not None:
                close()
            self.output = None
        self._release_frames()
        if self.temporary is not None:
            self.temporary.close()
            self.temporary = None
        super().close()

    def counters(self):
        return {
            **super().counters(), "passes": self.passes,
            "initial_runs": self.initial_runs, "input_pages": self.input_pages,
            "peak_frames": self.peak_frames,
        }
