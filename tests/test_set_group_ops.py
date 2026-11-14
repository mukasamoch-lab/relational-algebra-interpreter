from collections import Counter
from io import StringIO
import json

from executor import build_plan, execute
from test_joins import JoinFixture


class SetGroupTests(JoinFixture):
    def scan(self, name):
        return {"op": "seqscan", "table": name}

    def group(self, mode, keys, aggregates, child=None):
        return {"op": "group_by", "mode": mode, "keys": keys, "aggregates": aggregates,
                "child": child or self.scan("R")}

    def test_aggregates_and_sort_of_derived_columns(self):
        self.table("R", [(1, 7, "é"), (1, 9, "a"), (2, 4, "b")],
                   [["g", "INT"], ["n", "INT"], ["s", "VARCHAR", 5]])
        aggregates = [["count", "*"], ["sum", "R.n"], ["avg", "R.n"],
                      ["min", "R.s"], ["max", "R.s"]]
        for mode in ("sort", "hash"):
            plan = self.group(mode, ["R.g"], aggregates)
            _, bag = self.run_join(plan)
            self.assertEqual(bag, Counter([(1, 2, 16, 8.0, "a", "é"), (2, 1, 4, 4.0, "b", "b")]))
            root = build_plan({"op": "sort", "keys": [["avg_R_n", "desc"]], "child": plan},
                              self.catalog)
            output = StringIO()
            execute(root, output, True)
            self.assertIn("8.0000", output.getvalue())
            self.assertEqual(json.loads(output.getvalue())["rows"][0][0], 1)
            _, filtered = self.run_join({"op": "filter", "condition": "avg_R_n > 4.5",
                                         "child": plan})
            self.assertEqual(filtered, Counter({(1, 2, 16, 8.0, "a", "é"): 1}))

    def test_empty_aggregation(self):
        self.table("R", [], [["a", "INT"]])
        for mode in ("sort", "hash"):
            self.assertEqual(self.run_join(self.group(mode, [], [["count", "*"]]))[1],
                             Counter({(0,): 1}))
            self.assertFalse(self.run_join(self.group(mode, ["R.a"], [["sum", "R.a"]]))[1])
            with self.assertRaisesRegex(ValueError, "undefined"):
                self.run_join(self.group(mode, [], [["avg", "R.a"]]))

    def test_spilling_hash_group_and_distinct(self):
        schema = [["g", "INT"], ["s", "VARCHAR", 255]]
        values = [(i % 17, "x" * 250) for i in range(600)]
        self.table("R", values, schema)
        for mode in ("sort", "hash"):
            _, groups = self.run_join(self.group(mode, ["R.g"], [["count", "*"]]))
            self.assertEqual(groups, Counter((key, count) for key, count in Counter(
                row[0] for row in values).items()))
            _, rows = self.run_join({"op": "distinct", "mode": mode, "child": self.scan("R")})
            self.assertEqual(rows, Counter(set(values)))

    def test_sets_across_multiple_pages(self):
        left = [(i % 31, "x" * 200) for i in range(250)]
        right = [(10 + i % 31, "x" * 200) for i in range(350)]
        self.table("R", left, [["a", "INT"], ["b", "VARCHAR", 200]])
        self.table("S", right, [["x", "INT"], ["y", "VARCHAR", 255]])
        expected = {
            "union_all": Counter(left + right),
            "union": Counter(set(left) | set(right)),
            "intersect": Counter(set(left) & set(right)),
            "except": Counter(set(left) - set(right)),
        }
        for op, bag in expected.items():
            root, result = self.run_join({"op": op, "left": self.scan("R"), "right": self.scan("S")})
            self.assertEqual(result, bag)
            self.assertEqual(root.schema, [["R.a", "INT"], ["R.b", "VARCHAR", 255]])

    def test_union_all_opens_right_sort_lazily(self):
        self.table("R", [(1,), (2,)], [["a", "INT"]])
        self.table("S", [(3,), (4,)], [["a", "INT"]])
        plan = {"op": "union_all", "left": self.scan("R"),
                "right": {"op": "sort", "keys": [["S.a", "desc"]], "child": self.scan("S")}}
        root, rows = self.run_join({"op": "limit", "n": 1, "child": plan})
        self.assertEqual(rows, Counter({(1,): 1}))
        self.assertEqual(root.child.inputs[1].counts["tuples_out"], 0)
        self.assertEqual(self.run_join(plan)[1], Counter({(1,): 1, (2,): 1, (3,): 1, (4,): 1}))
