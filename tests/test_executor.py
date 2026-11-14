"""Execution pipeline regressions using persisted heap tables."""

import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from catalog import Catalog
from executor import build_plan, execute, validate_plan
from storage import load_csv


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        catalog = Catalog(self.folder, frames=4)
        catalog.create_table("R", [["a", "INT"], ["b", "VARCHAR", 20], ["c", "INT"]])
        source = self.folder / "input.csv"
        source.write_text("42,bob,7\n43,amy,9\n43,amy,9\n")
        load_csv(catalog, "R", source)
        catalog.close()
        self.catalog = Catalog(self.folder, frames=4)
        self.addCleanup(self.catalog.close)

    def run_plan(self, plan):
        root = build_plan(plan, self.catalog)
        output = io.StringIO()
        execute(root, output, True)
        self.assertTrue(all(frame.pin_count == 0 for frame in self.catalog.pool.frames.values()))
        return root, json.loads(output.getvalue())

    def test_pipeline_and_own_counters(self):
        root, result = self.run_plan({
            "op": "limit", "n": 1, "child": {
                "op": "project", "columns": ["R.b", "R.c"], "child": {
                    "op": "filter", "condition": "R.a > 42 and R.c < 10",
                    "child": {"op": "seqscan", "table": "R"},
                },
            },
        })
        self.assertEqual(result["rows"], [["amy", 9]])
        self.assertEqual(result["totals"]["comparisons"], 2)
        self.assertEqual(root.child.child.child.counts["tuples_out"], 2)
        self.assertEqual(root.counts["page_reads"], 0)
        self.assertEqual(root.child.child.child.counts["page_reads"], 2)

    def test_projection_keeps_duplicates(self):
        _, result = self.run_plan({
            "op": "project", "columns": ["R.b"],
            "child": {"op": "seqscan", "table": "R"},
        })
        self.assertEqual(result["rows"], [["bob"], ["amy"], ["amy"]])

    def test_zero_limit_does_not_read_table_pages(self):
        root = build_plan({
            "op": "limit", "n": 0, "child": {"op": "seqscan", "table": "R"},
        }, self.catalog)
        reads = self.catalog.pool.stats()["reads"]
        output = io.StringIO()
        execute(root, output, True)
        self.assertEqual(json.loads(output.getvalue())["rows"], [])
        self.assertEqual(self.catalog.pool.stats()["reads"], reads)
        self.assertEqual(root.child.counts["tuples_out"], 0)

    def test_limit_before_end_unpins_and_stops_pulling(self):
        root, result = self.run_plan({
            "op": "limit", "n": 1, "child": {"op": "seqscan", "table": "R"},
        })
        self.assertEqual(result["rows"], [[42, "bob", 7]])
        self.assertEqual(root.child.counts["tuples_out"], 1)

    def test_invalid_plan_names_its_node(self):
        cases = [
            ({"op": "filter", "condition": 5, "child": {}}, "filter"),
            ({"op": "project", "child": {}}, "project"),
            ({"op": "limit", "n": True, "child": {}}, "limit"),
            ({"op": "unknown"}, "unknown"),
        ]
        for plan, op in cases:
            with self.subTest(plan=plan), self.assertRaisesRegex(ValueError, f"Node '{op}'"):
                validate_plan(plan)

    def test_unknown_names_and_type_errors_before_open(self):
        bad_plans = [
            {"op": "project", "columns": ["R.missing"], "child": {"op": "seqscan", "table": "R"}},
            {"op": "filter", "condition": "R.a = '42'", "child": {"op": "seqscan", "table": "R"}},
            {"op": "seqscan", "table": "Missing"},
        ]
        for plan in bad_plans:
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                build_plan(plan, self.catalog)
        self.assertTrue(all(frame.pin_count == 0 for frame in self.catalog.pool.frames.values()))

    def test_empty_filter_and_restart(self):
        root = build_plan({
            "op": "filter", "condition": "R.a < 0",
            "child": {"op": "seqscan", "table": "R"},
        }, self.catalog)
        results = []
        for _ in range(2):
            output = io.StringIO()
            execute(root, output, True)
            results.append(json.loads(output.getvalue()))
        self.assertEqual(results[0]["rows"], [])
        self.assertEqual(results[1]["totals"]["comparisons"], 3)
        self.assertTrue(all(frame.pin_count == 0 for frame in self.catalog.pool.frames.values()))
