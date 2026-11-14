"""Project 2 library boundary regression tests."""

import unittest

from ra_engine import Column, Relation, compile_condition, evaluate
from ra_lexer import QueryError
from ra_parser import parse, parse_condition
from ra_tree import tree_from_json, tree_to_json


class LibraryTests(unittest.TestCase):
    def test_condition_parses_and_evaluates_without_query(self):
        condition = parse_condition("R.b = S.b and not (R.b < 0)")
        columns = (Column("b", "R", "number"), Column("b", "S", "number"))
        predicate = compile_condition(condition, columns)
        self.assertTrue(predicate((3, 3)))
        self.assertFalse(predicate((3, 4)))
        self.assertFalse(predicate((-1, -1)))
        for suffix in (" extra", " EOF"):
            with self.subTest(suffix=suffix), self.assertRaises(QueryError):
                parse_condition("R.b = S.b" + suffix)

    def test_every_node_kind_round_trips_through_json(self):
        trees = (
            parse("distinct(project[R.a](select[R.a >= 0](R)))"),
            parse("rename[T](R) join[T.a = S.a] S"),
            parse("A union B intersect C minus D times E"),
            parse_condition("R.a != 3 and R.a > -1 or not (R.b <= 'O''Brien') and R.a < 4"),
        )
        kinds = set()
        for tree in trees:
            with self.subTest(tree=tree):
                self.assertEqual(tree_from_json(tree_to_json(tree)), tree)
            pending = [tree]
            while pending:
                node = pending.pop()
                kinds.add(node.kind)
                pending.extend(node.children)
        self.assertEqual(kinds, {
            "Relation", "Select", "Project", "Rename", "Distinct", "Times",
            "Join", "Union", "Intersect", "Minus", "Or", "And", "Not",
            "Eq", "Ne", "Lt", "Le", "Gt", "Ge", "Attr", "Num", "Str",
        })

    def test_operators_can_read_one_shot_tuple_sources(self):
        def source(name):
            return Relation(name, (Column("a", name, "number"),), iter([(1,), (2,)]))

        result = evaluate(parse("project[R.a](R join[R.a = S.a] S)"), source)
        self.assertEqual(result.rows, [(1,), (2,)])
