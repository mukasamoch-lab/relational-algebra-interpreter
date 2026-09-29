"""Assignment cases 1–17: scanning, precedence, nesting, and syntax failures."""

import unittest

from ra import QueryError, draw_tree, parse, tokenize


class AssignmentParserCases(unittest.TestCase):
    def test_01_02_whitespace_equivalence(self):
        tight = parse("select[x1=3](R)")
        spaced = parse("select[ x1 = 3 ](R)")
        self.assertEqual(draw_tree(tight), draw_tree(spaced))

    def test_03_greater_or_equal_is_one_token(self):
        tokens = tokenize("select[Age>=30](R)")
        self.assertEqual([t.kind for t in tokens].count(">="), 1)
        self.assertNotIn(">", [t.kind for t in tokens])

    def test_04_negative_number(self):
        tokens = tokenize("select[Age>-30](R)")
        self.assertEqual([(t.kind, t.value) for t in tokens if t.kind in (">", "NUMBER")],
                         [(">", ">"), ("NUMBER", -30)])

    def test_05_06_07_quoted_punctuation_and_doubled_quote(self):
        for source, expected in [
            ("select[Name='Bob)'](R)", "Bob)"),
            ("select[Name='a,b'](R)", "a,b"),
            ("select[Name='O''Brien'](R)", "O'Brien"),
        ]:
            with self.subTest(source=source):
                tokens = tokenize(source)
                self.assertEqual([t.value for t in tokens if t.kind == "STRING"], [expected])
                self.assertEqual(parse(source).kind, "Select")

    def test_08_keyword_can_name_attribute(self):
        self.assertEqual(parse("select[union=3](R)").children[0].children[0].label, "union")
        self.assertEqual(parse("select[not=3](R)").children[0].children[0].label, "not")

    def test_09_unterminated_string(self):
        with self.assertRaises(QueryError) as caught:
            parse("select[Name='Bob](R)")
        self.assertEqual(caught.exception.category, "Lexical")
        self.assertEqual(caught.exception.position, 12)

    def test_10_union_minus_groups_left(self):
        tree = parse("A union B minus C")
        self.assertEqual(tree.kind, "Minus")
        self.assertEqual(tree.children[0].kind, "Union")

    def test_11_minus_groups_left(self):
        tree = parse("A minus B minus C")
        self.assertEqual(tree.kind, "Minus")
        self.assertEqual(tree.children[0].kind, "Minus")

    def test_12_not_and_or_precedence(self):
        condition = parse("select[not (a=1 and b=2) or c>3](R)").children[0]
        self.assertEqual(condition.kind, "Or")
        self.assertEqual(condition.children[0].kind, "Not")
        self.assertEqual(condition.children[0].children[0].kind, "And")

    def test_13_and_before_or(self):
        condition = parse("select[a=1 and b=2 or c=3](R)").children[0]
        self.assertEqual(condition.kind, "Or")
        self.assertEqual(condition.children[0].kind, "And")

    def test_14_three_unary_levels(self):
        tree = parse("project[Name](select[Age>30](select[DID='D1'](Employees)))")
        self.assertEqual(tree.kind, "Project")
        self.assertEqual(tree.children[0].kind, "Select")
        self.assertEqual(tree.children[0].children[1].kind, "Select")
        self.assertEqual(tree.children[0].children[1].children[1].label, "Employees")

    def test_15_parentheses_override_precedence(self):
        tree = parse("(A union B) minus (C intersect D)")
        self.assertEqual((tree.kind, tree.children[0].kind, tree.children[1].kind),
                         ("Minus", "Union", "Intersect"))

    def test_16_missing_parenthesis_has_position(self):
        text = "select[Age>30](R"
        with self.assertRaises(QueryError) as caught:
            parse(text)
        self.assertEqual(caught.exception.category, "Syntax")
        self.assertEqual(caught.exception.position, len(text))
        self.assertIn("')'", caught.exception.message)

    def test_17_empty_projection_is_syntax_error(self):
        with self.assertRaises(QueryError) as caught:
            parse("project[](R)")
        self.assertEqual(caught.exception.category, "Syntax")

    def test_tree_display_is_nonexecuting(self):
        self.assertIn("Relation(Unknown)", draw_tree(parse("select[x=1](Unknown)")))

    def test_extra_input_rejected(self):
        with self.assertRaises(QueryError):
            parse("A union B leftover")


if __name__ == "__main__":
    unittest.main()
