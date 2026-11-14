from contextlib import closing
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from catalog import Catalog
from engine import statements
from opt import measure
from optimizer import Optimizer
from optimizer_stats import analyze
from sql_compiler import compile_sql
from sql_frontend import run_statement


class OptimizerTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.catalog = Catalog(folder.name, frames=4)
        self.addCleanup(self.catalog.close)
        demo = Path(__file__).resolve().parents[1] / "sql_pipeline_demo.sql"
        for plan in compile_sql(demo.read_text(encoding="utf-8"), self.catalog)[:-1]:
            run_statement(plan, self.catalog)

    def run_sql(self, sql, disabled=()):
        logical = compile_sql(sql, self.catalog)
        plan = Optimizer(self.catalog).optimize(logical, disabled)
        with closing(StringIO()) as output:
            result = measure(plan, self.catalog, output)
            return plan.plan, [json.loads(line) for line in output.getvalue().splitlines()], result

    def test_analyze_exact_idempotent_and_invalidated_by_insert(self):
        stats = analyze(self.catalog, "Players", buckets=3)
        self.assertEqual((stats["n_tuples"], stats["n_pages"], stats["width"]), (4, 1, 17.75))
        team = stats["columns"]["team_id"]
        self.assertEqual((team["ndv"], team["min"], team["max"]), (3, 1, 3))
        self.assertEqual([bucket["count"] for bucket in team["hist"]], [2, 1, 1])
        pages = self.catalog.disk.num_pages()
        self.assertEqual(analyze(self.catalog, "Players", buckets=3), stats)
        self.assertEqual(self.catalog.disk.num_pages(), pages)
        self.catalog.open_table("Players").insert([5, "Eve", 2, 23])
        self.assertEqual(self.catalog.get_table("Players")["statistics"], {})

    def test_comma_and_on_same_plan_and_result(self):
        analyze(self.catalog, "Players")
        analyze(self.catalog, "Teams")
        on = "SELECT p.name FROM Players p JOIN Teams t ON p.team_id = t.id WHERE p.points >= 15"
        comma = "SELECT p.name FROM Players p, Teams t WHERE p.team_id = t.id AND p.points >= 15"
        left, rows, result = self.run_sql(on)
        right, second, _ = self.run_sql(comma)
        self.assertEqual(left, right)
        self.assertCountEqual(rows, [["Amy"], ["Bob"]])
        self.assertCountEqual(rows, second)
        self.assertEqual(result["tuples_out"], 2)

    def test_disabled_pushdown_keeps_single_sided_on_predicate(self):
        sql = ("SELECT p.name FROM Players p JOIN Teams t "
               "ON p.team_id = t.id AND p.points >= 15")
        for disabled in ((), (1,), (2,), (3,), (4,), (5,), (6,), (7,)):
            with self.subTest(disabled=disabled):
                _, rows, _ = self.run_sql(sql, disabled)
                self.assertCountEqual(rows, [["Amy"], ["Bob"]])

    def test_global_aggregate_and_multiline_literal(self):
        self.catalog.open_table("Teams").insert([3, "West\nDivision"])
        _, rows, _ = self.run_sql("SELECT id FROM Teams WHERE name = 'West\nDivision'")
        self.assertEqual(rows, [[3]])
        _, rows, _ = self.run_sql("SELECT COUNT(*) AS n FROM Players WHERE id > 100")
        self.assertEqual(rows, [[0]])

    def test_shell_statement_delimiters(self):
        source = ["-- comment;\n", "INSERT INTO Teams VALUES (3, 'a;''b');\n",
                  "SELECT *\n", "FROM Teams; -- comment;\n"]
        actual = [item.strip() for item in statements(source) if item.strip()]
        self.assertEqual(actual, ["INSERT INTO Teams VALUES (3, 'a;''b')", "SELECT *\nFROM Teams"])
