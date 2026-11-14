"""M1 regression cases through storage APIs and CLI entry points."""

from contextlib import closing, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest

from buffer_pool import BufferPool
from catalog import Catalog, parse_schema
from disk_manager import DiskManager
from heap_file import HeapFile
from record_codec import decode_record, encode_record
from slotted_page import SlottedPage
from storage import data_page_count, load_csv, main, scan_table, trace


class StorageTests(unittest.TestCase):
    def test_record_and_page_examples(self):
        schema = [["a", "INT"], ["b", "VARCHAR", 5], ["c", "INT"]]
        record = encode_record(schema, [42, "bob", 7])
        self.assertEqual(record.hex(" "), "2a 00 00 00 03 00 62 6f 62 07 00 00 00")
        self.assertEqual(decode_record(schema, record), (42, "bob", 7))
        page = SlottedPage(0)
        expected = [(4083, 4071), (4063, 4047), (4050, 4030)]
        for index, (length, values) in enumerate(zip([13, 20, 13], expected)):
            self.assertEqual(page.insert(bytes(length)), index)
            self.assertEqual(struct.unpack_from("<H", page.data, 6)[0], values[0])
            self.assertEqual(page.free_bytes, values[1])
        page.delete(1)
        self.assertEqual(page.free_bytes, 4030)
        self.assertEqual(struct.unpack_from("<HH", page.data, 12), (0, 0))
        with self.assertRaisesRegex(ValueError, "deleted"):
            page.get(1)

    def test_utf8_byte_limits_and_signed_ints(self):
        schema = [["a", "INT"], ["b", "VARCHAR", 2]]
        self.assertEqual(decode_record(schema, encode_record(schema, [-2**31, "é"])),
                         (-2**31, "é"))
        with self.assertRaises(ValueError):
            encode_record(schema, [2**31, "x"])
        with self.assertRaises(ValueError):
            encode_record(schema, [0, "éé"])

    def test_published_policy_example(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "trace.txt"
            source.write_text("1\n2\n3\n1\n4\n2\n")
            for policy, totals in (("lru", (1, 5, 2)), ("clock", (2, 4, 1))):
                result = trace(source, 3, policy)
                self.assertEqual(tuple(result[key] for key in ("hits", "misses", "evictions")), totals)
                self.assertEqual(result, trace(source, 3, policy))

    def test_pins_dirty_eviction_and_repeated_flush(self):
        with TemporaryDirectory() as folder:
            for policy in ("lru", "clock"):
                disk = DiskManager(Path(folder) / f"{policy}.db")
                pool = BufferPool(disk, 1, policy)
                frame = pool.new_page()
                with self.assertRaisesRegex(RuntimeError, "no unpinned"):
                    pool.new_page()
                self.assertEqual(disk.num_pages(), 1)
                disk.allocate_page()
                with self.assertRaisesRegex(RuntimeError, "no unpinned"):
                    pool.fetch_page(1)
                frame.data[:5] = b"hello"
                pool.unpin_page(0)
                with self.assertRaises(ValueError):
                    pool.unpin_page(0)
                pool.fetch_page(1)
                pool.unpin_page(1)
                pool.flush_all()
                pool.flush_all()
                self.assertEqual(disk.writes, 1)
                self.assertEqual(disk.read_page(0)[:5], b"hello")

    def test_shared_pool_keeps_files_separate(self):
        with TemporaryDirectory() as folder:
            first_disk = DiskManager(Path(folder) / "first.db")
            second_disk = DiskManager(Path(folder) / "second.db")
            root = BufferPool(first_disk, 2)
            second = root.for_file(second_disk)
            a = root.new_page()
            b = second.new_page()
            a.data[0] = 10
            b.data[0] = 20
            root.unpin_page(0)
            second.unpin_page(0)
            root.flush_all()
            self.assertEqual(len(root.frames), 2)
            self.assertEqual(first_disk.read_page(0)[0], 10)
            self.assertEqual(second_disk.read_page(0)[0], 20)

    def test_catalog_reopen_and_large_metadata(self):
        with TemporaryDirectory() as folder:
            catalog = Catalog(folder, frames=2)
            info = catalog.create_table("R", parse_schema("a:INT,b:VARCHAR(20)"))
            info["statistics"] = {"future": "x" * 10000}
            catalog.save_table(info)
            catalog.close()
            catalog = Catalog(folder, frames=2)
            self.assertEqual(catalog.list_tables(), ["R"])
            self.assertEqual(catalog.get_table("R"), info)
            with self.assertRaisesRegex(ValueError, "already exists"):
                catalog.create_table("R", [["a", "INT"]])
            catalog.close()

    def test_csv_partial_error_counts_and_duplicates(self):
        with TemporaryDirectory() as folder:
            catalog = Catalog(folder, frames=2)
            catalog.create_table("R", [["a", "INT"], ["b", "VARCHAR", 20]])
            source = Path(folder) / "input.csv"
            source.write_text('1,"a,b"\n1,"a,b"\nbad,x\n')
            with self.assertRaisesRegex(ValueError, "line 3, column a"):
                load_csv(catalog, "R", source)
            self.assertEqual(catalog.get_table("R")["num_tuples"], 2)
            out = io.StringIO()
            scan_table(catalog, "R", None, True, out)
            self.assertEqual(json.loads(out.getvalue())["rows"], [[1, "a,b"], [1, "a,b"]])
            limited = io.StringIO()
            scan_table(catalog, "R", 1, True, limited)
            self.assertEqual(len(json.loads(limited.getvalue())["rows"]), 1)
            self.assertTrue(all(frame.pin_count == 0 for frame in catalog.pool.frames.values()))
            catalog.close()

    def test_multi_directory_scan_reopen_delete_all_and_early_close(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "heap.db"
            disk = DiskManager(path)
            pool = BufferPool(disk, 2)
            heap = HeapFile(pool)
            # Each record takes one data page, crossing 681 directory entries.
            ids = [heap.insert(b"x" * 4084) for _ in range(683)]
            pool.flush_all()
            pool = BufferPool(DiskManager(path), 2)
            heap = HeapFile(pool)
            self.assertEqual(data_page_count(heap), 683)
            with closing(heap.scan()) as scan:
                self.assertEqual(next(scan), (ids[0], b"x" * 4084))
            self.assertTrue(all(frame.pin_count == 0 for frame in pool.frames.values()))
            self.assertEqual(sum(1 for _ in heap.scan()), 683)
            for rid in ids:
                heap.delete(rid)
            self.assertEqual(list(heap.scan()), [])
            self.assertTrue(all(frame.pin_count == 0 for frame in pool.frames.values()))

    def test_capacity_and_slot_nonreuse(self):
        for length, expected in ((12, 255), (13, 240), (202, 19)):
            page = SlottedPage(1)
            inserted = 0
            while page.insert(b"x" * length) is not None:
                inserted += 1
            self.assertEqual(inserted, expected)
            page.delete(0)
            self.assertIsNone(page.insert(b"x" * length))

    def test_named_table_tuple_access_updates_catalog_counts(self):
        with TemporaryDirectory() as folder:
            catalog = Catalog(folder, frames=2)
            catalog.create_table("R", [["a", "INT"]])
            table = catalog.open_table("R")
            first = table.insert((42,))
            second = table.insert((42,))
            self.assertEqual(table.get(first), (42,))
            self.assertEqual(catalog.get_table("R")["num_tuples"], 2)
            table.delete(first)
            self.assertEqual(list(table.scan()), [(second, (42,))])
            self.assertEqual(catalog.get_table("R")["num_tuples"], 1)
            table.delete(second)
            self.assertEqual(list(table.scan()), [])
            self.assertEqual(catalog.get_table("R")["num_tuples"], 0)
            catalog.close()

    def test_cli_entrypoints_and_json(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "input.csv"
            source.write_text("42,bob,7\n43,amy,9\n")
            commands = [
                ["create-table", "--name", "R", "--schema", "a:INT,b:VARCHAR(20),c:INT"],
                ["load", "--table", "R", "--file", str(source)],
                ["scan", "--table", "R", "--frames", "2", "--json"],
                ["layout", "--table", "R", "--page", "1"],
            ]
            results = []
            for command in commands:
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(command + ["--db", folder]), 0)
                results.append(json.loads(output.getvalue()))
            self.assertEqual(results[2]["rows"], [[42, "bob", 7], [43, "amy", 9]])
            self.assertEqual(results[3]["slots"][0]["offset"], 4083)
            error = io.StringIO()
            with redirect_stderr(error):
                status = main(["scan", "--db", folder, "--table", "Unknown", "--json"])
            self.assertEqual(status, 1)
            self.assertIn("unknown table", error.getvalue())
