from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from catalog import Catalog
from executor import build_plan, execute
from sql_compiler import compile_sql
from sql_frontend import main
from sql_parser import SQLError


def physical(node):
    """Translate the operators exercised by this integration fixture."""
    translated = dict(node)
    op = node["op"]
    translated["op"] = {
        "scan": "seqscan", "select": "filter", "join": "nested_loop_join",
        "cross": "nested_loop_join",
    }.get(op, op)
    if op in ("group_by", "distinct"):
        translated["mode"] = "sort"
    if op == "cross":
        translated["condition"] = "1 = 1"
    for key in ("child", "left", "right"):
        if key in translated:
            translated[key] = physical(translated[key])
    return translated


class SQLTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)
        self.catalog = Catalog(self.path, frames=4)
        self.addCleanup(self.catalog.close)
        for table, schema in (
            ("Emp", [["eid", "INT"], ["name", "VARCHAR", 30], ["age", "INT"],
                     ["mgr", "INT"], ["did", "INT"], ["salary", "INT"], ["city", "VARCHAR", 15]]),
            ("Dept", [["did", "INT"], ["title", "VARCHAR", 30], ["city", "VARCHAR", 20]]),
            ("Office", [["did", "INT"], ["oid", "INT"]]),
            ("Region", [["oid", "INT"], ["rid", "INT"]]),
        ):
            self.catalog.create_table(table, schema)

    def compile(self, query):
        return compile_sql(query, self.catalog)

    def rows(self, sql):
        root = build_plan(physical(self.compile(sql)), self.catalog)
        output = StringIO()
        execute(root, output, True)
        return json.loads(output.getvalue())["rows"]

    def test_01_self_join(self):
        plan = self.compile("SELECT e.name, m.name FROM Emp e JOIN Emp m ON e.mgr = m.eid")
        join = plan["child"]
        self.assertEqual(join["condition"], "e.mgr = m.eid")
        for side, alias in (("left", "e"), ("right", "m")):
            self.assertEqual(join[side], {"op": "rename", "to": alias,
                                         "child": {"op": "scan", "table": "Emp"}})

    def test_02_star_qualifies_shared_names(self):
        plan = self.compile("SELECT * FROM Emp, Dept, Office")
        self.assertEqual([c for c in plan["columns"] if c.endswith(".did")],
                         ["Emp.did", "Dept.did", "Office.did"])
        self.assertEqual(len(plan["columns"]), 12)

    def test_03_ambiguous_column(self):
        with self.assertRaisesRegex(SQLError, r"ambiguity:.*Emp.did, Dept.did"):
            self.compile("SELECT did FROM Emp, Dept")

    def test_04_union_and_union_all(self):
        sql = "SELECT city FROM Emp UNION {}SELECT city FROM Dept"
        self.assertEqual(self.compile(sql.format(""))["op"], "union")
        self.assertEqual(self.compile(sql.format("ALL "))["op"], "union_all")

    def test_05_where_and_having(self):
        plan = self.compile("""
            SELECT did, COUNT(*) AS n, AVG(salary) AS avg_sal
            FROM Emp WHERE age > 25 GROUP BY did HAVING COUNT(*) > 5
            ORDER BY n DESC LIMIT 10
        """)
        self.assertEqual((plan["op"], plan["n"]), ("limit", 10))
        self.assertEqual(plan["child"]["keys"], [["n", "desc"]])
        having = plan["child"]["child"]["child"]
        self.assertEqual(having["condition"], "n > 5")
        self.assertEqual(having["child"]["aggregates"], [
            ["count", "*", "n"], ["avg", "Emp.salary", "avg_sal"],
        ])
        self.assertEqual(having["child"]["child"]["condition"], "Emp.age > 25")

    def test_06_non_grouped_column(self):
        with self.assertRaisesRegex(SQLError, "aggregate:.*not grouped"):
            self.compile("SELECT name, COUNT(*) FROM Emp GROUP BY did")

    def test_07_aggregate_in_where(self):
        with self.assertRaisesRegex(SQLError, "aggregate:.*before grouping"):
            self.compile("SELECT name FROM Emp WHERE COUNT(*) > 1")

    def test_08_subquery_join(self):
        sql = ("SELECT t.n, d.title FROM (SELECT did, COUNT(*) AS n FROM Emp GROUP BY did) AS t "
               "JOIN Dept d ON t.did = d.did")
        plan = self.compile(sql)
        self.assertEqual(plan["columns"], ["t.n", "d.title"])
        self.assertEqual(plan["child"]["left"]["to"], "t")
        self.assertEqual(plan["child"]["left"]["child"]["child"]["op"], "group_by")

    def test_09_hidden_order_key_runs(self):
        table = self.catalog.open_table("Emp")
        table.insert((1, "Young", 20, 1, 2, 100, "Ottawa"))
        table.insert((2, "Older", 50, 1, 2, 100, "Ottawa"))
        self.assertEqual(self.rows("SELECT name FROM Emp ORDER BY age DESC"), [["Older"], ["Young"]])

    def test_10_comparison_type(self):
        with self.assertRaisesRegex(SQLError, "type:.*INT and VARCHAR"):
            self.compile("SELECT name FROM Emp WHERE age > '30'")

    def test_11_unknown_table_and_column(self):
        with self.assertRaisesRegex(SQLError, "unknown table"):
            self.compile("SELECT name FROM Missing")
        with self.assertRaisesRegex(SQLError, "unknown column"):
            self.compile("SELECT missing FROM Emp")

    def test_12_duplicate_alias(self):
        with self.assertRaisesRegex(SQLError, "duplicate alias"):
            self.compile("SELECT * FROM Emp, Emp")

    def test_13_empty_global_count_runs(self):
        self.assertEqual(self.rows("SELECT COUNT(*) FROM Emp"), [[0]])

    def test_14_union_type_mismatch(self):
        with self.assertRaisesRegex(SQLError, "schema:.*matching"):
            self.compile("SELECT age FROM Emp UNION SELECT title FROM Dept")

    def test_15_four_way_joins(self):
        comma = self.compile("SELECT * FROM Emp, Dept, Office, Region WHERE "
                             "Emp.did = Dept.did AND Dept.did = Office.did AND Office.oid = Region.oid")
        explicit = self.compile("SELECT * FROM Emp JOIN Dept ON Emp.did = Dept.did "
                                "JOIN Office ON Dept.did = Office.did JOIN Region ON Office.oid = Region.oid")
        self.assertEqual(comma["columns"], explicit["columns"])
        self.assertEqual(comma["child"]["op"], "select")
        cross, join = comma["child"]["child"], explicit["child"]
        for _ in range(3):
            self.assertEqual((cross["op"], join["op"]), ("cross", "join"))
            self.assertEqual(cross["right"], join["right"])
            cross, join = cross["left"], join["left"]
        self.assertEqual(cross, join)

    def test_precedence_escaping_and_statement_files(self):
        plan = self.compile("sElEcT name FROM Emp -- note\nWHERE NOT (age < -1 OR name = 'O''Neil') AND age <> +2")
        self.assertEqual(plan["child"]["condition"], "not (Emp.age < -1 or Emp.name = 'O''Neil') and Emp.age != 2")
        plan = self.compile("SELECT city FROM Emp UNION SELECT city FROM Dept EXCEPT SELECT city FROM Emp")
        self.assertEqual((plan["op"], plan["left"]["op"]), ("except", "union"))
        sql = "CREATE TABLE _New (_k INT, s VARCHAR(5)); INSERT INTO _New VALUES (2, 'a'); SELECT _k FROM _New;"
        self.assertEqual([p["op"] for p in self.compile(sql)], ["create_table", "insert", "project"])
        self.assertNotIn("_New", self.catalog.list_tables())

    def test_alias_and_group_distinct(self):
        plan = self.compile("SELECT name AS employee FROM Emp ORDER BY employee")
        self.assertEqual(plan["keys"], [["employee", "asc"]])
        self.assertEqual(plan["child"]["aliases"], ["employee"])
        self.assertEqual(self.compile("SELECT DISTINCT did FROM Emp GROUP BY did")["op"], "project")

    def test_cli_check_error_position_and_parse(self):
        source = self.path / "input.sql"
        source.write_text("SELECT name FROM Emp\nWHERE @", encoding="utf-8")
        error = StringIO()
        with redirect_stderr(error):
            self.assertEqual(main(["check", "--db", str(self.path), "--file", str(source)]), 1)
        self.assertIn("error: lexical:", error.getvalue())
        self.assertIn("line 2, column 7", error.getvalue())
        with redirect_stdout(StringIO()) as output:
            self.assertEqual(main(["parse", "--query", "SELECT name FROM Unknown", "--explain"]), 0)
        self.assertEqual(json.loads(output.getvalue())[0]["kind"], "select")
