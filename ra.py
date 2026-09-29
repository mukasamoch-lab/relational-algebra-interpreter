"""Handwritten scanner, recursive descent parser, and tree printer for RA queries."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import sys


class QueryError(Exception):
    """A user-facing lexical or syntax error with a zero-based source offset."""

    def __init__(self, category: str, message: str, position: int):
        self.category = category
        self.message = message
        self.position = position
        super().__init__(f"{category} error at position {position + 1}: {message}")


@dataclass(frozen=True)
class Token:
    kind: str
    value: str | int | float
    position: int


def tokenize(source: str) -> list[Token]:
    """Scan query text one character at a time; no regex or split-based parsing."""
    tokens: list[Token] = []
    i = 0
    while i < len(source):
        ch = source[i]
        start = i
        if ch.isspace():
            i += 1
            continue
        if ch == "/" and source[i:i + 2] == "//":
            while i < len(source) and source[i] not in "\r\n":
                i += 1
            continue
        if ch == "'":
            i += 1
            parts: list[str] = []
            while True:
                if i >= len(source) or source[i] in "\r\n":
                    raise QueryError("Lexical", "unterminated quoted string", start)
                if source[i] == "'":
                    if i + 1 < len(source) and source[i + 1] == "'":
                        parts.append("'")
                        i += 2
                        continue
                    i += 1
                    break
                parts.append(source[i])
                i += 1
            tokens.append(Token("STRING", "".join(parts), start))
            continue
        if ch.isascii() and ch.isalpha():
            i += 1
            while i < len(source) and source[i].isascii() and (
                source[i].isalnum() or source[i] == "_"
            ):
                i += 1
            tokens.append(Token("IDENT", source[start:i], start))
            continue
        if ch.isascii() and (ch.isdigit() or (
            ch == "-" and i + 1 < len(source) and source[i + 1].isdigit()
        )):
            if ch == "-":
                i += 1
            while i < len(source) and source[i].isascii() and source[i].isdigit():
                i += 1
            if i < len(source) and source[i] == ".":
                if i + 1 >= len(source) or not source[i + 1].isascii() or not source[i + 1].isdigit():
                    raise QueryError("Lexical", "expected a digit after decimal point", i)
                i += 1
                while i < len(source) and source[i].isascii() and source[i].isdigit():
                    i += 1
            spelling = source[start:i]
            tokens.append(Token("NUMBER", float(spelling) if "." in spelling else int(spelling), start))
            continue
        if source[i:i + 2] in (">=", "<=", "!="):
            tokens.append(Token(source[i:i + 2], source[i:i + 2], i))
            i += 2
            continue
        if ch in "()[],.=<>":
            tokens.append(Token(ch, ch, i))
            i += 1
            continue
        raise QueryError("Lexical", f"unexpected character {ch!r}", i)
    tokens.append(Token("EOF", "", len(source)))
    return tokens


@dataclass(frozen=True)
class Node:
    kind: str
    label: str
    children: tuple[Node, ...] = ()
    position: int = 0


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.index = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def take(self) -> Token:
        token = self.current
        self.index += 1
        return token

    def accept(self, spelling: str) -> Token | None:
        token = self.current
        if token.kind == spelling or (token.kind == "IDENT" and token.value == spelling):
            return self.take()
        return None

    def expect(self, spelling: str) -> Token:
        token = self.accept(spelling)
        if token is None:
            raise QueryError("Syntax", f"expected {spelling!r}, found {self.current.value!r}", self.current.position)
        return token

    def identifier(self) -> Token:
        if self.current.kind != "IDENT":
            raise QueryError("Syntax", f"expected an identifier, found {self.current.value!r}", self.current.position)
        return self.take()

    def parse(self) -> Node:
        result = self.expression()
        self.expect("EOF")
        return result

    def expression(self) -> Node:
        left = self.intersect_expr()
        while self.current.kind == "IDENT" and self.current.value in ("union", "minus"):
            op = self.take()
            right = self.intersect_expr()
            left = Node(op.value.title(), "", (left, right), op.position)
        return left

    def intersect_expr(self) -> Node:
        left = self.product_expr()
        while self.current.kind == "IDENT" and self.current.value == "intersect":
            op = self.take()
            right = self.product_expr()
            left = Node("Intersect", "", (left, right), op.position)
        return left

    def product_expr(self) -> Node:
        left = self.primary()
        while self.current.kind == "IDENT" and self.current.value in ("times", "join"):
            op = self.take()
            condition = None
            if op.value == "join":
                self.expect("[")
                condition = self.condition()
                self.expect("]")
            right = self.primary()
            left = Node("Times", "", (left, right), op.position) if condition is None else Node(
                "Join", "", (left, condition, right), op.position
            )
        return left

    def primary(self) -> Node:
        if self.accept("("):
            result = self.expression()
            self.expect(")")
            return result
        word = self.identifier()
        if word.value in ("select", "project", "rename") and self.current.kind == "[":
            self.take()
            if word.value == "select":
                parameter = self.condition()
                self.expect("]")
                self.expect("(")
                relation = self.expression()
                self.expect(")")
                return Node("Select", "", (parameter, relation), word.position)
            if word.value == "project":
                attrs = [self.attribute_ref()]
                while self.accept(","):
                    attrs.append(self.attribute_ref())
                self.expect("]")
                self.expect("(")
                relation = self.expression()
                self.expect(")")
                return Node("Project", ", ".join(attrs), (relation,), word.position)
            name = self.identifier()
            self.expect("]")
            self.expect("(")
            relation = self.expression()
            self.expect(")")
            return Node("Rename", str(name.value), (relation,), word.position)
        return Node("Relation", str(word.value), position=word.position)

    def attribute_ref(self) -> str:
        first = str(self.identifier().value)
        if self.accept("."):
            return first + "." + str(self.identifier().value)
        return first

    def condition(self) -> Node:
        return self.or_condition()

    def or_condition(self) -> Node:
        left = self.and_condition()
        while self.current.kind == "IDENT" and self.current.value == "or":
            op = self.take()
            left = Node("Or", "", (left, self.and_condition()), op.position)
        return left

    def and_condition(self) -> Node:
        left = self.not_condition()
        while self.current.kind == "IDENT" and self.current.value == "and":
            op = self.take()
            left = Node("And", "", (left, self.not_condition()), op.position)
        return left

    def not_condition(self) -> Node:
        # A word spelled 'not' can be an attribute before a comparison operator.
        next_kind = self.tokens[self.index + 1].kind if self.index + 1 < len(self.tokens) else "EOF"
        op = None if next_kind in ("=", "!=", "<", "<=", ">", ">=", ".") else self.accept("not")
        if op:
            return Node("Not", "", (self.not_condition(),), op.position)
        if self.accept("("):
            result = self.condition()
            self.expect(")")
            return result
        return self.comparison()

    def comparison(self) -> Node:
        left = self.operand()
        op = self.current
        if op.kind not in ("=", "!=", "<", "<=", ">", ">="):
            raise QueryError("Syntax", f"expected a comparison operator, found {op.value!r}", op.position)
        self.take()
        right = self.operand()
        names = {"=": "Eq", "!=": "Ne", "<": "Lt", "<=": "Le", ">": "Gt", ">=": "Ge"}
        return Node(names[op.kind], "", (left, right), op.position)

    def operand(self) -> Node:
        token = self.current
        if token.kind in ("NUMBER", "STRING"):
            self.take()
            return Node("Num" if token.kind == "NUMBER" else "Str",
                        str(token.value), position=token.position)
        if token.kind == "IDENT":
            return Node("Attr", self.attribute_ref(), position=token.position)
        raise QueryError("Syntax", f"expected a number, string, or attribute, found {token.value!r}", token.position)


def parse(source: str) -> Node:
    return Parser(tokenize(source)).parse()


def _name(node: Node) -> str:
    if node.kind == "Str":
        return f"Str({node.label!r})"
    if node.kind in ("Relation", "Attr", "Num", "Str"):
        return f"{node.kind}({node.label})"
    if node.kind == "Project":
        return f"Project(attrs=[{node.label}])"
    if node.kind == "Rename":
        return f"Rename(name={node.label})"
    return node.kind


def draw_tree(node: Node) -> str:
    """Return an ASCII rendering that does not require Unicode terminal support."""
    lines = [_name(node)]

    def visit(current: Node, prefix: str) -> None:
        for index, child in enumerate(current.children):
            last = index == len(current.children) - 1
            lines.append(prefix + ("`-- " if last else "|-- ") + _name(child))
            visit(child, prefix + ("    " if last else "|   "))

    visit(node, "")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description="Relational algebra query parser")
    cli.add_argument("--tree", metavar="QUERY", help="print a parse tree without executing")
    cli.add_argument("--data", metavar="FILE", help="relation definitions to load")
    cli.add_argument("--stats", action="store_true", help="print condition evaluation counters")
    cli.add_argument("query", nargs="?", help="query to execute against --data")
    args = cli.parse_args(argv)
    try:
        if args.tree is not None:
            print(draw_tree(parse(args.tree)))
        elif args.query is not None and args.data is not None:
            from ra_data import load_relations
            from ra_engine import Counters, display, evaluate

            with open(args.data, encoding="utf-8") as stream:
                catalog = load_relations(stream.read())
            counts = Counters()
            print(display(evaluate(parse(args.query), catalog, counts)))
            if args.stats:
                print(f"select_evaluations={counts.select_evaluations}")
                print(f"join_comparisons={counts.join_comparisons}")
                for entry in counts.operators:
                    print(f"{entry.operator} at position {entry.position + 1}: {entry.evaluations} evaluations")
        else:
            cli.error("use --tree QUERY or --data FILE QUERY")
    except QueryError as error:
        print(error, file=sys.stderr)
        return 1
    except OSError as error:
        print(f"File error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
