"""Reproduce M1 capacity, scan, and supplied trace measurements."""

import argparse
from contextlib import closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from buffer_pool import BufferPool
from catalog import Catalog
from disk_manager import DiskManager
from heap_file import HeapFile
from record_codec import encode_record
from storage import data_page_count, load_csv, trace


def measurements(trace_files=()):
    results = {"capacity": [], "scan": [], "policies": []}
    with TemporaryDirectory() as folder:
        directory = Path(folder)
        catalog = Catalog(directory / "db", frames=2)
        fixtures = (
            ("Ints", [["a", "INT"], ["b", "INT"], ["c", "INT"]], [1, 2, 3]),
            ("Short", [["a", "INT"], ["b", "VARCHAR", 5], ["c", "INT"]], [42, "bob", 7]),
            ("Long", [["a", "VARCHAR", 200]], ["x" * 200]),
        )
        import csv
        for name, schema, values in fixtures:
            size = len(encode_record(schema, values))
            expected = 4088 // (size + 4)
            catalog.create_table(name, schema)
            source = directory / f"{name}.csv"
            with source.open("w", newline="", encoding="utf-8") as output:
                csv.writer(output).writerows([values] * (expected + 1))
            load_csv(catalog, name, source)
            heap = catalog.open_heap(catalog.get_table(name))
            measured = 0
            with closing(heap.scan()) as rows:
                for (page_id, _), record in rows:
                    if page_id != 1:
                        break
                    measured += 1
            results["capacity"].append({
                "schema": schema, "record_bytes": size, "computed": expected,
                "measured_first_page": measured,
            })
        catalog.close()
        disk = DiskManager(directory / "scan.db")
        pool = BufferPool(disk, 2)
        heap = HeapFile(pool)
        for _ in range(500):
            heap.insert(b"x" * 4084)
        pool.flush_all()
        assert data_page_count(heap) == 500
        for frames in (2, 8, 64):
            disk = DiskManager(directory / "scan.db")
            pool = BufferPool(disk, frames)
            heap = HeapFile(pool)
            rows = sum(1 for _ in heap.scan())
            results["scan"].append({"frames": frames, "rows": rows, **pool.stats()})
        for policy in ("lru", "clock"):
            for filename in trace_files:
                for frames in (4, 16, 64, 256):
                    result = trace(filename, frames, policy)
                    count = result["hits"] + result["misses"]
                    result["hit_rate"] = result["hits"] / count if count else 0
                    result["trace"] = Path(filename).name
                    results["policies"].append(result)
        sequential = directory / "sequential.txt"
        sequential.write_text("".join(f"{page}\n" for _ in range(10) for page in range(80)))
        results["sequential_check"] = trace(sequential, 64, "lru")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--traces", nargs="*", default=[])
    args = parser.parse_args()
    print(json.dumps(measurements(args.traces), indent=2))
