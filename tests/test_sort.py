"""External sort regressions through real persisted tables and physical plans."""

from contextlib import redirect_stderr
import io
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from catalog import Catalog
from executor import build_plan, execute, main, totals, validate_plan
from record_codec import encode_record
from storage import data_page_count
from temp_storage import TemporaryRuns


class SortTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.catalog = Catalog(self.temp.name, frames=4)
        self.addCleanup(self.catalog.close)

    def add_rows(self, schema, values, name="R"):
        info = self.catalog.create_table(name, schema)
        heap = self.catalog.open_heap(info)
        for row in values:
            heap.insert(encode_record(schema, row))
        info["num_tuples"] = len(values)
        info["num_pages"] = data_page_count(heap)
        self.catalog.pool.flush_all()
        self.catalog.save_table(info)
        return info

    def sort_plan(self, keys, table="R"):
        return {"op": "sort", "keys": keys, "child": {"op": "seqscan", "table": table}}

    def assert_clean(self):
        self.assertTrue(all(frame.pin_count == 0 for frame in self.catalog.pool.frames.values()))
        self.assertTrue(all(not disk.path.name.startswith("run-")
                            for disk in self.catalog.pool.disks.values()))

    def test_small_multikey_utf8_and_duplicates(self):
        values = [(3, "é"), (-1, "z"), (3, "a"), (3, "é"), (-1, "é"), (3, "")]
        self.add_rows([["a", "INT"], ["b", "VARCHAR", 20]], values)
        root = build_plan(self.sort_plan([["R.a", "asc"], ["R.b", "desc"]]), self.catalog)
        output = io.StringIO()
        execute(root, output, True)
        result = json.loads(output.getvalue())
        expected = sorted(values, key=lambda row: row[1].encode("utf-8"), reverse=True)
        expected.sort(key=lambda row: row[0])
        self.assertEqual(result["rows"], [list(row) for row in expected])
        self.assertEqual(root.passes, 1)
        self.assertEqual(root.initial_runs, 1)
        self.assert_clean()

    def test_eighty_pages_at_four_frames(self):
        schema = [["a", "INT"]] + [[f"p{i}", "VARCHAR", 255] for i in range(15)]
        schema.append(["tail", "VARCHAR", 223])
        values = [((index * 37) % 80, *("x" * 255 for _ in range(15)), "y" * 223)
                  for index in range(80)]
        info = self.add_rows(schema, values)
        self.assertEqual(info["num_pages"], 80)
        # Cold pool so I/O and peak frames describe this query.
        self.catalog.close()
        self.catalog = Catalog(self.temp.name, frames=4)
        self.addCleanup(self.catalog.close)
        root = build_plan(self.sort_plan([["R.a", "asc"]]), self.catalog)
        root.open()
        temporary_path = Path(root.temporary.directory.name)
        actual = []
        try:
            while True:
                row = root.next()
                if row is None:
                    break
                actual.append(row[0])
        finally:
            root.close()
        self.assertEqual(actual, list(range(80)))
        self.assertEqual(root.input_pages, 80)
        self.assertEqual(root.initial_runs, 20)
        self.assertEqual(root.passes, 1 + math.ceil(math.log(20, 3)))
        self.assertEqual(root.peak_frames, 4)
        self.assertEqual(root.counts["page_reads"], 312)
        self.assertEqual(root.counts["page_writes"], 312)
        self.assertFalse(temporary_path.exists())
        self.assert_clean()

    def test_limit_closes_sort_before_full_output(self):
        self.add_rows([["a", "INT"]], [(3,), (1,), (2,)])
        root = build_plan({
            "op": "limit", "n": 1,
            "child": self.sort_plan([["R.a", "asc"]]),
        }, self.catalog)
        output = io.StringIO()
        execute(root, output, True)
        self.assertEqual(json.loads(output.getvalue())["rows"], [[1]])
        self.assertEqual(root.child.counts["tuples_out"], 1)
        self.assert_clean()

    def test_empty_sort(self):
        self.add_rows([["a", "INT"]], [])
        root = build_plan(self.sort_plan([["R.a", "asc"]]), self.catalog)
        output = io.StringIO()
        execute(root, output, True)
        self.assertEqual(json.loads(output.getvalue())["rows"], [])
        self.assertEqual(root.counts["comparisons"], 0)
        self.assertEqual(root.passes, 0)
        self.assert_clean()

    def test_sort_after_filter_and_project(self):
        self.add_rows([["a", "INT"], ["b", "VARCHAR", 10]], [(1, "x"), (2, "z"), (3, "a")])
        plan = self.sort_plan([["R.b", "asc"]])
        plan["child"] = {
            "op": "project", "columns": ["R.b"], "child": {
                "op": "filter", "condition": "R.a > 1",
                "child": {"op": "seqscan", "table": "R"},
            },
        }
        root = build_plan(plan, self.catalog)
        output = io.StringIO()
        execute(root, output, True)
        self.assertEqual(json.loads(output.getvalue())["rows"], [["a"], ["z"]])
        self.assertEqual(root.child.child.counts["comparisons"], 3)
        self.assert_clean()

    def test_invalid_sort_and_unknown_keys(self):
        self.add_rows([["a", "INT"]], [(1,)])
        for keys in ([], [["R.a", "up"]], [["R.a"]], ["R.a"]):
            with self.subTest(keys=keys), self.assertRaisesRegex(ValueError, "Node 'sort'"):
                validate_plan(self.sort_plan(keys))
        with self.assertRaisesRegex(ValueError, "unknown column"):
            build_plan(self.sort_plan([["R.bad", "asc"]]), self.catalog)
        self.assert_clean()

    def test_restart_preserves_counters_and_results(self):
        self.add_rows([["a", "INT"]], [(2,), (1,), (1,)])
        results = []
        for _ in range(2):
            root = build_plan(self.sort_plan([["R.a", "desc"]]), self.catalog)
            output = io.StringIO()
            execute(root, output, True)
            results.append(json.loads(output.getvalue()))
            self.assert_clean()
        self.assertEqual(results[0]["rows"], [[2], [1], [1]])
        self.assertEqual(results[0]["totals"]["comparisons"],
                         results[1]["totals"]["comparisons"])

    def test_external_duplicates_clock_and_early_close(self):
        schema = [["a", "INT"], ["b", "VARCHAR", 200]]
        values = [(index % 17, chr(97 + index % 5) * 190) for index in range(900)]
        self.add_rows(schema, values)
        self.catalog.close()
        self.catalog = Catalog(self.temp.name, frames=4, policy="clock")
        self.addCleanup(self.catalog.close)
        plan = self.sort_plan([["R.a", "desc"], ["R.b", "asc"]])
        root = build_plan(plan, self.catalog)
        output = io.StringIO()
        execute(root, output, True)
        expected = sorted(values, key=lambda row: (-row[0], row[1].encode("utf-8")))
        self.assertEqual(json.loads(output.getvalue())["rows"], [list(row) for row in expected])
        self.assertLessEqual(root.peak_frames, 4)
        self.assert_clean()
        root = build_plan({"op": "limit", "n": 1, "child": plan}, self.catalog)
        output = io.StringIO()
        execute(root, output, True)
        self.assertEqual(json.loads(output.getvalue())["rows"], [list(expected[0])])
        self.assert_clean()

    def test_nested_sorts_share_one_budget(self):
        schema = [["a", "INT"], ["b", "VARCHAR", 200]]
        values = [(index % 17, "x" * 190) for index in range(450)]
        self.add_rows(schema, values)
        inner = self.sort_plan([["R.a", "desc"]])
        root = build_plan({"op": "sort", "keys": [["R.a", "asc"]], "child": inner},
                          self.catalog)
        baseline = self.catalog.pool.stats()
        output = io.StringIO()
        execute(root, output, True)
        after = self.catalog.pool.stats()
        self.assertEqual([row[0] for row in json.loads(output.getvalue())["rows"]],
                         sorted(row[0] for row in values))
        counters = totals(root)
        self.assertEqual(counters["page_reads"], after["reads"] - baseline["reads"])
        self.assertEqual(counters["page_writes"], after["writes"] - baseline["writes"])
        self.assertLessEqual(self.catalog.pool.peak_accounted_frames, 4)
        self.assert_clean()

    def test_sort_comparison_failure_releases_everything(self):
        self.add_rows([["a", "INT"]], [(3,), (2,), (1,)])
        root = build_plan(self.sort_plan([["R.a", "asc"]]), self.catalog)
        with patch.object(root, "compare", side_effect=RuntimeError("injected failure")):
            with self.assertRaisesRegex(RuntimeError, "injected failure"):
                root.open()
        self.assert_clean()

    def test_four_page_boundary_has_an_accounted_output_workspace(self):
        schema = [["a", "INT"]] + [[f"p{i}", "VARCHAR", 255] for i in range(15)]
        schema.append(["tail", "VARCHAR", 223])
        values = [(key, *("x" * 255 for _ in range(15)), "y" * 223)
                  for key in (4, 3, 2, 1)]
        self.add_rows(schema, values)
        root = build_plan(self.sort_plan([["R.a", "asc"]]), self.catalog)
        output = io.StringIO()
        execute(root, output, True)
        self.assertEqual([row[0] for row in json.loads(output.getvalue())["rows"]],
                         [1, 2, 3, 4])
        self.assertEqual(root.passes, 2)
        self.assertLessEqual(root.peak_frames, 4)
        self.assert_clean()

    def test_malformed_json_error_location(self):
        source = Path(self.temp.name) / "bad.json"
        source.write_text('{"op":')
        errors = io.StringIO()
        with redirect_stderr(errors):
            result = main(["run", "--db", self.temp.name, "--plan", str(source), "--json"])
        self.assertEqual(result, 1)
        self.assertIn("JSON syntax at line", errors.getvalue())
