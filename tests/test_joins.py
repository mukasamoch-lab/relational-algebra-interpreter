from collections import Counter
from tempfile import TemporaryDirectory
import unittest

from catalog import Catalog
from executor import JOINS, build_plan, totals
from record_codec import encode_record


class JoinFixture(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.catalog = Catalog(folder.name, frames=4)
        self.addCleanup(self.catalog.close)

    def table(self, name, rows, schema):
        info = self.catalog.create_table(name, schema)
        heap = self.catalog.open_heap(info)
        for row in rows:
            heap.insert(encode_record(schema, row))
        info.update(num_pages=heap.num_data_pages(), num_tuples=len(rows))
        self.catalog.pool.flush_all()
        self.catalog.save_table(info)

    def plan(self, op, condition="R.a = S.a"):
        return {"op": op, "condition": condition,
                "left": {"op": "seqscan", "table": "R"},
                "right": {"op": "seqscan", "table": "S"}}

    def run_join(self, plan):
        root = build_plan(plan, self.catalog)
        before = self.catalog.pool.stats()
        root.open()
        rows = []
        try:
            while (row := root.next()) is not None:
                rows.append(row)
        finally:
            root.close()
        after = self.catalog.pool.stats()
        self.assertEqual(totals(root)["page_reads"], after["reads"] - before["reads"])
        self.assertEqual(totals(root)["page_writes"], after["writes"] - before["writes"])
        self.assertTrue(all(not f.pin_count for f in self.catalog.pool.frames.values()))
        self.assertTrue(all(not d.path.name.startswith("run-")
                            for d in self.catalog.pool.disks.values()))
        self.assertLessEqual(self.catalog.pool.peak_accounted_frames, 4)
        return root, Counter(rows)


class JoinTests(JoinFixture):
    def test_all_joins_preserve_duplicates(self):
        left = [(1,), (2,), (2,), (3,)]
        right = [(2,), (2,), (4,)]
        self.table("R", left, [["a", "INT"]])
        self.table("S", right, [["a", "INT"]])
        for op in sorted(JOINS):
            with self.subTest(op=op):
                root, rows = self.run_join(self.plan(op, "S.a = R.a"))
                self.assertEqual(rows, Counter({(2, 2): 4}))
                if op == "nested_loop_join":
                    self.assertEqual(root.counts["comparisons"], 12)

    def test_duplicate_groups_span_frames_and_hash_overflows(self):
        schema = [["a", "INT"]] + [[f"x{i}", "VARCHAR", 255] for i in range(5)]
        left = [(2, *("x" * 255 for _ in range(5)))] * 10
        right = [(2, *("y" * 255 for _ in range(5)))] * 13
        self.table("R", left, schema)
        self.table("S", right, schema)
        for op in sorted(JOINS):
            with self.subTest(op=op):
                root, rows = self.run_join(self.plan(op))
                self.assertEqual(rows, Counter({left[0] + right[0]: 130}))
                if op == "hash_join":
                    self.assertEqual(root.overflow_partitions, 1)

    def test_empty_and_theta_join(self):
        self.table("R", [(1,), (2,)], [["a", "INT"]])
        self.table("S", [], [["a", "INT"]])
        for op in sorted(JOINS):
            self.assertFalse(self.run_join(self.plan(op))[1])
        root, rows = self.run_join(self.plan("nested_loop_join", "R.a < 2"))
        self.assertFalse(rows)
        self.assertEqual(root.counts["comparisons"], 0)
        with self.assertRaisesRegex(ValueError, "requires equality"):
            build_plan(self.plan("hash_join", "R.a < S.a"), self.catalog)

    def test_theta_join_and_early_limit(self):
        self.table("R", [(1,), (2,)], [["a", "INT"]])
        self.table("S", [(2,), (3,)], [["a", "INT"]])
        root, rows = self.run_join(self.plan("nested_loop_join", "R.a < S.a"))
        self.assertEqual(rows, Counter({(1, 2): 1, (1, 3): 1, (2, 3): 1}))
        self.assertEqual(root.counts["comparisons"], 4)
        for op in sorted(JOINS):
            plan = {"op": "limit", "n": 1, "child": self.plan(op)}
            self.assertEqual(self.run_join(plan)[1], Counter({(2, 2): 1}))
