"""Persistent catalog snapshots accessed through the shared buffer pool."""

import json
import struct
from pathlib import Path
from contextlib import closing

from buffer_pool import BufferPool
from disk_manager import DiskManager
from heap_file import HeapFile
from slotted_page import PAGE_SIZE
from record_codec import decode_record, encode_record


CATALOG_HEADER = struct.Struct("<4sIIHH")
CATALOG_MAGIC = b"CAT1"
CATALOG_PAYLOAD = PAGE_SIZE - CATALOG_HEADER.size
NO_PAGE = 0xFFFFFFFF


def validate_identifier(name):
    if not isinstance(name, str) or not name:
        raise ValueError("identifier must be a nonempty string")
    if not (name[0].isascii() and (name[0].isalpha() or name[0] == "_")):
        raise ValueError(f"invalid identifier {name!r}")
    if not all(c.isascii() and (c.isalnum() or c == "_") for c in name):
        raise ValueError(f"invalid identifier {name!r}")


def validate_schema(schema):
    if not schema:
        raise ValueError("a table needs at least one column")
    result = []
    names = set()
    for column in schema:
        if not isinstance(column, (list, tuple)) or len(column) < 2:
            raise ValueError("invalid column definition")
        name, kind, *options = column
        validate_identifier(name)
        if name in names:
            raise ValueError(f"duplicate column {name!r}")
        names.add(name)
        if kind == "INT" and not options:
            result.append([name, kind])
        elif (
            kind == "VARCHAR" and len(options) == 1
            and type(options[0]) is int and 1 <= options[0] <= 255
        ):
            result.append([name, kind, options[0]])
        else:
            raise ValueError(f"invalid type for column {name!r}")
    return result


def parse_schema(text):
    schema = []
    for definition in text.split(","):
        name, separator, spelling = definition.strip().partition(":")
        if not separator:
            raise ValueError(f"missing ':' in column definition {definition!r}")
        spelling = spelling.strip().upper()
        if spelling == "INT":
            schema.append([name.strip(), "INT"])
        elif spelling.startswith("VARCHAR(") and spelling.endswith(")"):
            schema.append([name.strip(), "VARCHAR", int(spelling[8:-1])])
        else:
            raise ValueError(f"unsupported type {spelling!r}")
    return validate_schema(schema)


class Catalog:
    def __init__(self, directory, frames=8, policy="lru"):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        if frames < 2:
            raise ValueError("catalog and heap operations require at least two frames")
        self.disk = DiskManager(self.directory / "catalog.db")
        self.pool = BufferPool(self.disk, frames, policy)

    def _snapshots(self):
        for page_id in range(self.disk.num_pages()):
            frame = self.pool.fetch_page(page_id)
            try:
                magic, stored_id, next_id, used, flags = CATALOG_HEADER.unpack_from(frame.data)
                if magic != CATALOG_MAGIC or stored_id != page_id or used > CATALOG_PAYLOAD:
                    raise ValueError(f"invalid catalog page {page_id}")
                if not flags & 1:
                    continue
                payload = bytearray(frame.data[CATALOG_HEADER.size:CATALOG_HEADER.size + used])
            finally:
                self.pool.unpin_page(page_id)
            visited = {page_id}
            while next_id != NO_PAGE:
                if next_id in visited:
                    raise ValueError("cycle in catalog snapshot")
                visited.add(next_id)
                frame = self.pool.fetch_page(next_id)
                try:
                    magic, stored_id, following, used, flags = CATALOG_HEADER.unpack_from(frame.data)
                    if (
                        magic != CATALOG_MAGIC or stored_id != next_id
                        or used > CATALOG_PAYLOAD or flags != 0
                    ):
                        raise ValueError(f"invalid catalog continuation page {next_id}")
                    payload.extend(frame.data[CATALOG_HEADER.size:CATALOG_HEADER.size + used])
                    next_id = following
                finally:
                    self.pool.unpin_page(frame.page_id)
            yield json.loads(payload)

    def get_table(self, name):
        validate_identifier(name)
        latest = None
        for info in self._snapshots():
            if info["name"] == name:
                latest = info
        if latest is None:
            raise ValueError(f"unknown table {name!r}")
        return latest

    def list_tables(self):
        names = {}
        for info in self._snapshots():
            names[info["name"]] = None
        return list(names)

    def save_table(self, info):
        """Append continuation pages before publishing the snapshot root."""
        payload = json.dumps(info, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        next_id = NO_PAGE
        for offset in reversed(range(0, len(payload), CATALOG_PAYLOAD)):
            frame = self.pool.new_page()
            try:
                chunk = payload[offset:offset + CATALOG_PAYLOAD]
                CATALOG_HEADER.pack_into(
                    frame.data, 0, CATALOG_MAGIC, frame.page_id, next_id,
                    len(chunk), int(offset == 0),
                )
                frame.data[CATALOG_HEADER.size:CATALOG_HEADER.size + len(chunk)] = chunk
                next_id = frame.page_id
            finally:
                self.pool.unpin_page(frame.page_id)
            self.pool.flush_all()

    def create_table(self, name, schema):
        validate_identifier(name)
        schema = validate_schema(schema)
        if name == "catalog":
            raise ValueError("table name 'catalog' is reserved")
        if name in self.list_tables():
            raise ValueError(f"table {name!r} already exists")
        path = self.directory / f"{name}.db"
        if path.exists():
            raise ValueError(f"table file already exists: {path.name}")
        HeapFile(self.pool.for_file(DiskManager(path)))
        self.pool.flush_all()
        info = {
            "name": name, "schema": schema, "file": path.name, "first_page": 0,
            "num_pages": 0, "num_tuples": 0, "statistics": {},
        }
        self.save_table(info)
        return info

    def open_heap(self, info):
        path = self.directory / info["file"]
        if not path.is_file():
            raise ValueError(f"missing data file for table {info['name']!r}")
        return HeapFile(self.pool.for_file(DiskManager(path)))

    def open_table(self, name):
        return Table(self, name)

    def close(self):
        self.pool.flush_all()


class Table:
    """Tuple access for a named table with persisted catalog counts."""

    def __init__(self, catalog, name):
        self.catalog = catalog
        self.name = name
        self.info = catalog.get_table(name)
        self.schema = self.info["schema"]
        self.heap = catalog.open_heap(self.info)

    def insert(self, values):
        record = encode_record(self.schema, values)
        if len(record) > PAGE_SIZE - 12:
            raise ValueError(f"table {self.name}: record does not fit with its slot")
        info = self.catalog.get_table(self.name)
        rid = self.heap.insert(record)
        info["statistics"] = {}
        info["num_tuples"] += 1
        info["num_pages"] = self.heap.num_data_pages()
        self.catalog.pool.flush_all()
        self.catalog.save_table(info)
        return rid

    def get(self, rid):
        return decode_record(self.schema, self.heap.get(rid))

    def delete(self, rid):
        info = self.catalog.get_table(self.name)
        self.heap.delete(rid)
        info["statistics"] = {}
        info["num_tuples"] -= 1
        self.catalog.pool.flush_all()
        self.catalog.save_table(info)

    def scan(self):
        with closing(self.heap.scan()) as rows:
            for rid, record in rows:
                yield rid, decode_record(self.schema, record)
