"""Recursive descent parser for relational algebra and conditions."""

from ra_lexer import QueryError, Token, tokenize
from ra_tree import Node


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
        if token.kind == spelling or (
            spelling != "EOF" and token.kind == "IDENT" and token.value == spelling
        ):
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
        if word.value == "distinct" and self.accept("("):
            relation = self.expression()
            self.expect(")")
            return Node("Distinct", "", (relation,), word.position)
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


def parse_condition(source: str) -> Node:
    parser = Parser(tokenize(source, multiline_strings=True))
    condition = parser.condition()
    parser.expect("EOF")
    return condition
