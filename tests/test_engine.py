"""Assignment cases 18–25 and small semantic checks."""

import unittest

from ra import QueryError, parse
from ra_data import load_relations
from ra_engine import Counters, display, evaluate


DATA = """// definitions can contain comments
Employees (EID, Name, Age, DID) = {
  E1, John, 32, D1
  E2, Alice, 28, D2
  E3, Bob, 29, D1
}
Emp(EID, MgrID, DID) = {
  E1, E2, D1
  E2, E2, D2
}
Dept(DID, Label) = {
  D1, Sales
  D2, Tech
}
R(A, B) = {
  1, 1
  2, 1
}
S(B, C) = {
  1, 3
}
"""


class AssignmentSemanticCases(unittest.TestCase):
    def test_nested_operator_counts_are_individual(self):
        catalog = load_relations("R(a) = {\n1\n2\n}\nS(a) = {\n1\n}\n")
        counts = Counters()
        evaluate(parse("select[a=1](select[a>0](R))"), catalog, counts)
        evaluate(parse("R join[R.a=S.a] S"), catalog, counts)
        self.assertEqual(counts.select_evaluations, 4)
        self.assertEqual(counts.join_comparisons, 2)
        self.assertEqual([(item.operator, item.evaluations) for item in counts.operators],
                         [("select", 2), ("select", 2), ("join", 2)])

    def setUp(self):
        self.catalog = load_relations(DATA)

    def run_query(self, text, counters=None):
        return evaluate(parse(text), self.catalog, counters)

    def test_18_attribute_against_attribute(self):
        self.assertEqual(self.run_query("select[A=B](R)").rows, [(1, 1)])

    def test_19_theta_join_preserves_both_did_columns(self):
        result = self.run_query("Emp join[Emp.DID=Dept.DID] Dept")
        self.assertIn("Emp.DID", [c.qualified for c in result.columns])
        self.assertIn("Dept.DID", [c.qualified for c in result.columns])
        self.assertEqual(len(result.rows), 2)

    def test_20_rename_self_join(self):
        result = self.run_query("rename[E2](Emp) join[Emp.MgrID=E2.EID] Emp")
        self.assertEqual(len(result.rows), 2)
        self.assertIn("E2.EID", [c.qualified for c in result.columns])

    def test_21_union_incompatible_schema(self):
        with self.assertRaises(QueryError) as caught:
            self.run_query("R union S")
        self.assertEqual(caught.exception.category, "Schema")

    def test_22_mixed_comparison_type(self):
        with self.assertRaises(QueryError) as caught:
            self.run_query("select[Age>'30'](Employees)")
        self.assertEqual(caught.exception.category, "Type")

    def test_23_projection_deduplicates(self):
        result = self.run_query("project[DID](Employees)")
        self.assertEqual(result.rows, [("D1",), ("D2",)])

    def test_24_repeated_projection_attribute_errors(self):
        with self.assertRaises(QueryError) as caught:
            self.run_query("project[Name, Name](Employees)")
        self.assertEqual(caught.exception.category, "Schema")

    def test_25_empty_result_still_has_schema(self):
        result = self.run_query("select[Age>100](Employees)")
        self.assertEqual(result.rows, [])
        self.assertIn("Employees.Age", display(result))

    def test_join_counter_counts_every_pair(self):
        counts = Counters()
        self.run_query("Emp join[Emp.DID=Dept.DID] Dept", counts)
        self.assertEqual(counts.join_comparisons, 4)

    def test_select_counter_counts_rows(self):
        counts = Counters()
        self.run_query("select[Age>30](Employees)", counts)
        self.assertEqual(counts.select_evaluations, 3)

    def test_loader_dedup_and_quoted_values(self):
        catalog = load_relations("R(a,b) = {\n1, 'O''Brien'\n1, 'O''Brien'\n}\n")
        self.assertEqual(catalog["R"].rows, [(1, "O'Brien")])
        self.assertEqual(evaluate(parse("select[b='O''Brien'](R)"), catalog).rows,
                         [(1, "O'Brien")])

    def test_duplicate_definition_attribute(self):
        with self.assertRaises(QueryError) as caught:
            load_relations("R(a,a) = {}\n")
        self.assertEqual(caught.exception.category, "Schema")

    def test_unqualified_join_column_is_ambiguous(self):
        with self.assertRaises(QueryError) as caught:
            self.run_query("Emp join[DID=Dept.DID] Dept")
        self.assertEqual(caught.exception.category, "Name")

    def test_empty_relation_type_check_uses_schema_when_known(self):
        result = self.run_query("select[Age>100](Employees)")
        with self.assertRaises(QueryError) as caught:
            evaluate(parse("select[Age='30'](Empty)"), {"Empty": result})
        self.assertEqual(caught.exception.category, "Type")

    def test_product_collision_needs_rename(self):
        with self.assertRaises(QueryError) as caught:
            self.run_query("Emp times Emp")
        self.assertEqual(caught.exception.category, "Schema")


if __name__ == "__main__":
    unittest.main()
