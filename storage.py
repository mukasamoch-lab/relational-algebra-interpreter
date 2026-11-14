"""Module 1 commands. JSON scan output is streamed without collecting rows."""

import argparse
from contextlib import closing
import csv
import json
from pathlib import Path
import struct
import sys
from tempfile import TemporaryDirectory

from buffer_pool import BufferPool
from catalog import Catalog, parse_schema
from directory_page import DirectoryPage, NO_NEXT_PAGE
from disk_manager import DiskManager
from record_codec import decode_record, encode_record
from slotted_page import PAGE_SIZE, SlottedPage


def data_page_count(heap):
    return heap.num_data_pages()


def load_csv(catalog, name, filename):
    info = catalog.get_table(name)
    heap = catalog.open_heap(info)
    inserted = 0
    try:
        with open(filename, encoding="utf-8", newline="") as source:
            reader = csv.reader(source, strict=True)
            try:
                for fields in reader:
                    line = reader.line_num
                    if len(fields) != len(info["schema"]):
                        raise ValueError(
                            f"table {name}, line {line}: expected "
                            f"{len(info['schema'])} columns, got {len(fields)}"
                        )
                    values = []
                    for column, text in zip(info["schema"], fields):
                        try:
                            value = int(text) if column[1] == "INT" else text
                            encode_record([column], [value])
                        except (ValueError, UnicodeError) as error:
                            raise ValueError(
                                f"table {name}, line {line}, column {column[0]}: {error}"
                            ) from None
                        values.append(value)
                    record = encode_record(info["schema"], values)
                    if len(record) > PAGE_SIZE - 12:
                        raise ValueError(
                            f"table {name}, line {line}: record of {len(record)} bytes "
                            "cannot fit a page with its slot"
                        )
                    heap.insert(record)
                    inserted += 1
            except csv.Error as error:
                raise ValueError(
                    f"table {name}, line {reader.line_num}: invalid CSV: {error}"
                ) from None
    finally:
        # A later invalid row leaves earlier valid rows and their counts intact.
        if inserted:
            info["statistics"] = {}
            info["num_tuples"] += inserted
            info["num_pages"] = data_page_count(heap)
            catalog.pool.flush_all()
            catalog.save_table(info)
    return inserted


def scan_table(catalog, table, limit, as_json, output=None):
    output = sys.stdout if output is None else output
    info = catalog.get_table(table)
    heap = catalog.open_heap(info)
    if as_json:
        output.write('{"table":' + json.dumps(table))
        output.write(',"schema":' + json.dumps(info["schema"]) + ',"rows":[')
    count = 0
    with closing(heap.scan()) as rows:
        while limit is None or count < limit:
            item = next(rows, None)
            if item is None:
                break
            _, record = item
            row = decode_record(info["schema"], record)
            if as_json:
                output.write(("," if count else "") + json.dumps(row, ensure_ascii=False))
            else:
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    if as_json:
        output.write('],"stats":' + json.dumps(catalog.pool.stats()) + "}\n")


def trace(filename, frames, policy):
    with open(filename, encoding="utf-8") as source:
        maximum = -1
        for line_number, line in enumerate(source, 1):
            try:
                page_id = int(line.strip())
                if page_id < 0:
                    raise ValueError()
            except ValueError:
                raise ValueError(f"trace line {line_number}: expected nonnegative page id") from None
            maximum = max(maximum, page_id)
    with TemporaryDirectory() as folder:
        disk = DiskManager(Path(folder) / "trace.db")
        disk.reserve_pages(maximum + 1)
        pool = BufferPool(disk, frames, policy)
        with open(filename, encoding="utf-8") as source:
            for line in source:
                page_id = int(line.strip())
                pool.fetch_page(page_id)
                pool.unpin_page(page_id)
        return {"policy": policy, "frames": frames, **pool.stats()}


def layout(catalog, table, page_id):
    info = catalog.get_table(table)
    heap = catalog.open_heap(info)
    directory_ids = set()
    current = 0
    while current != NO_NEXT_PAGE:
        directory_ids.add(current)
        frame = heap.pool.fetch_page(current)
        try:
            current = DirectoryPage(frame.data).next_page_id
        finally:
            heap.pool.unpin_page(frame.page_id)
    frame = heap.pool.fetch_page(page_id)
    try:
        if page_id in directory_ids:
            directory = DirectoryPage(frame.data)
            return {
                "page_id": page_id, "kind": "directory", "header_bytes": 10,
                "next_dir_page": directory.next_page_id,
                "entry_count": directory.entry_count,
                "entries": [
                    {"byte_offset": 10 + 6 * index, "data_page_id": data_id, "free_bytes": free}
                    for index, (data_id, free) in enumerate(directory.entries())
                ],
            }
        _, count, end = struct.unpack_from("<IHH", frame.data)
        slots = []
        for index in range(count):
            offset, length = struct.unpack_from("<HH", frame.data, 8 + 4 * index)
            slots.append({
                "slot": index, "byte_offset": 8 + 4 * index,
                "offset": offset, "length": length,
            })
        return {
            "page_id": page_id, "kind": "data", "slot_count": count,
            "free_end": end, "free_bytes": SlottedPage(page_id, frame.data).free_bytes,
            "slots": slots,
        }
    finally:
        heap.pool.unpin_page(page_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Module 1 storage engine")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("create-table", "load", "scan", "layout", "trace"):
        command = commands.add_parser(name)
        command.add_argument("--db", required=True)
        command.add_argument("--frames", type=int, default=8)
        command.add_argument("--policy", choices=("lru", "clock"), default="lru")
        command.add_argument("--json", action="store_true")
        if name == "create-table":
            command.add_argument("--name", required=True)
            command.add_argument("--schema", required=True)
        elif name in ("load", "scan", "layout"):
            command.add_argument("--table", required=True)
        if name in ("load", "trace"):
            command.add_argument("--file", required=True)
        if name == "scan":
            command.add_argument("--limit", type=int)
        if name == "layout":
            command.add_argument("--page", type=int, required=True)
    args = parser.parse_args(argv)
    catalog = None
    try:
        if args.command == "trace":
            print(json.dumps(trace(args.file, args.frames, args.policy)))
            return 0
        if args.command == "scan" and args.limit is not None and args.limit < 0:
            raise ValueError("limit must be nonnegative")
        catalog = Catalog(args.db, args.frames, args.policy)
        if args.command == "create-table":
            info = catalog.create_table(args.name, parse_schema(args.schema))
            print(json.dumps({"created": info["name"]}))
        elif args.command == "load":
            count = load_csv(catalog, args.table, args.file)
            print(json.dumps({"table": args.table, "loaded": count}))
        elif args.command == "scan":
            scan_table(catalog, args.table, args.limit, args.json)
        elif args.command == "layout":
            print(json.dumps(layout(catalog, args.table, args.page), indent=2))
        return 0
    except (OSError, ValueError, IndexError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    finally:
        if catalog is not None:
            catalog.close()


if __name__ == "__main__":
    raise SystemExit(main())
