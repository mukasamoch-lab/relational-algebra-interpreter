"""Handwritten scanner and recursive descent parser for the course SQL subset."""

from dataclasses import dataclass


FUNCTIONS = {"COUNT", "SUM", "AVG", "MIN", "MAX"}
KEYWORDS = set("""
SELECT DISTINCT FROM WHERE GROUP BY HAVING ORDER ASC DESC LIMIT
JOIN ON AS UNION ALL INTERSECT EXCEPT NOT AND OR
CREATE TABLE INSERT INTO VALUES INT VARCHAR
""".split()) | FUNCTIONS


@dataclass(frozen=True)
class Token:
    kind: str
    value: object
    line: int
    column: int


class SQLError(ValueError):
    def __init__(self, category, message, token):
        super().__init__(f"error: {category}: {message} at line {token.line}, column {token.column}")


def scan(text):
    index = 0
    line = column = 1

    def take():
        nonlocal index, line, column
        char = text[index]
        index += 1
        if char == "\n":
            line += 1
            column = 1
        else:
            column += 1
        return char

    while index < len(text):
        char = text[index]
        if char.isspace():
            take()
            continue
        if text.startswith("--", index):
            while index < len(text) and text[index] != "\n":
                take()
            continue
        start, row, col = index, line, column
        if char == "'":
            take()
            parts = []
            while index < len(text):
                char = take()
                if char != "'":
                    parts.append(char)
                elif index < len(text) and text[index] == "'":
                    parts.append(take())
                else:
                    break
            else:
                raise SQLError("lexical", "unterminated string", Token("STRING", "", row, col))
            yield Token("STRING", "".join(parts), row, col)
        elif char.isascii() and (char.isalpha() or char == "_"):
            take()
            while index < len(text):
                char = text[index]
                if not (char.isascii() and (char.isalnum() or char == "_")):
                    break
                take()
            word = text[start:index]
            yield Token(word.upper() if word.upper() in KEYWORDS else "IDENT", word, row, col)
        elif char in "0123456789" or (
            char in "+-" and text[index + 1:index + 2] in tuple("0123456789")
        ):
            take()
            while index < len(text) and text[index] in "0123456789":
                take()
            try:
                value = int(text[start:index])
            except ValueError:
                raise SQLError("lexical", "integer literal too long", Token("INTVAL", "", row, col)) from None
            yield Token("INTVAL", value, row, col)
        elif text[index:index + 2] in ("<=", ">=", "<>"):
            symbol = take() + take()
            yield Token(symbol, symbol, row, col)
        elif char in "(),.*;=<>":
            yield Token(char, take(), row, col)
        else:
            raise SQLError("lexical", f"unexpected character {char!r}", Token("", char, row, col))
    yield Token("EOF", "", line, column)


@dataclass
class Expr:
    kind: str
    value: object
    token: Token
    args: tuple = ()


class Parser:
    def __init__(self, text):
        self.tokens = iter(scan(text))
        self.token = next(self.tokens)

    def accept(self, kind):
        if self.token.kind == kind:
            token = self.token
            self.token = next(self.tokens, token)
            return token
        return None

    def require(self, kind):
        token = self.accept(kind)
        if token is None:
            raise SQLError("syntax", f"expected {kind}, found {self.token.kind}", self.token)
        return token

    def separated(self, parse_item):
        items = [parse_item()]
        while self.accept(","):
            items.append(parse_item())
        return items

    def statements(self):
        result = [self.statement()]
        while self.accept(";") and self.token.kind != "EOF":
            result.append(self.statement())
        self.require("EOF")
        return result

    def statement(self):
        if self.accept("CREATE"):
            self.require("TABLE")
            table = self.require("IDENT")
            self.require("(")
            columns = self.separated(self.coldef)
            self.require(")")
            return {"kind": "create_table", "table": table, "schema": columns}
        if self.accept("INSERT"):
            self.require("INTO")
            table = self.require("IDENT")
            self.require("VALUES")
            self.require("(")
            values = self.separated(self.literal)
            self.require(")")
            return {"kind": "insert", "table": table, "values": values}
        return self.query()

    def coldef(self):
        column = self.require("IDENT")
        if self.accept("INT"):
            return column, "INT"
        self.require("VARCHAR")
        self.require("(")
        length = self.require("INTVAL")
        self.require(")")
        return column, "VARCHAR", length

    def query(self):
        left = self.select()
        while self.token.kind in ("UNION", "INTERSECT", "EXCEPT"):
            token = self.require(self.token.kind)
            op = token.kind.lower()
            if op == "union" and self.accept("ALL"):
                op = "union_all"
            left = {"kind": op, "left": left, "right": self.select(), "token": token}
        return left

    def select(self):
        if self.accept("("):
            query = self.query()
            self.require(")")
            return query
        token = self.require("SELECT")
        distinct = bool(self.accept("DISTINCT"))
        items = None if self.accept("*") else self.separated(self.select_item)
        self.require("FROM")
        sources = [(self.source(), None)]
        if self.accept(","):
            sources.extend((item, None) for item in self.separated(self.source))
        else:
            while self.accept("JOIN"):
                source = self.source()
                self.require("ON")
                sources.append((source, self.condition()))
        where = self.condition() if self.accept("WHERE") else None
        keys = having = None
        if self.accept("GROUP"):
            self.require("BY")
            keys = self.separated(self.column)
            if self.accept("HAVING"):
                having = self.condition()
        order = []
        if self.accept("ORDER"):
            self.require("BY")
            order = self.separated(self.order_item)
        limit = self.require("INTVAL") if self.accept("LIMIT") else None
        return dict(kind="select", token=token, distinct=distinct, items=items,
                    sources=sources, where=where, keys=keys, having=having,
                    order=order, limit=limit)

    def source(self):
        if self.accept("("):
            query = self.query()
            self.require(")")
            self.require("AS")
            alias = self.require("IDENT")
            return None, query, alias
        table = self.require("IDENT")
        alias = None
        if self.accept("AS") or self.token.kind == "IDENT":
            alias = self.require("IDENT")
        return table, None, alias

    def select_item(self):
        expression = self.aggregate() if self.token.kind in FUNCTIONS else self.column()
        return expression, self.require("IDENT") if self.accept("AS") else None

    def order_item(self):
        expression = self.column()
        direction = "desc" if self.accept("DESC") else "asc"
        if direction == "asc":
            self.accept("ASC")
        return expression, direction

    def column(self):
        first = self.require("IDENT")
        name = first.value
        if self.accept("."):
            name += "." + self.require("IDENT").value
        return Expr("column", name, first)

    def literal(self):
        token = self.accept("STRING") or self.require("INTVAL")
        return Expr("literal", token.value, token)

    def aggregate(self):
        function = self.require(self.token.kind)
        self.require("(")
        star = self.accept("*")
        argument = Expr("star", "*", star) if star else self.column()
        self.require(")")
        return Expr("aggregate", function.kind.lower(), function, (argument,))

    def operand(self):
        if self.token.kind in FUNCTIONS:
            return self.aggregate()
        if self.token.kind == "IDENT":
            return self.column()
        return self.literal()

    def condition(self):
        left = self.conjunction()
        while (token := self.accept("OR")) is not None:
            left = Expr("or", "or", token, (left, self.conjunction()))
        return left

    def conjunction(self):
        left = self.negation()
        while (token := self.accept("AND")) is not None:
            left = Expr("and", "and", token, (left, self.negation()))
        return left

    def negation(self):
        if (token := self.accept("NOT")) is not None:
            return Expr("not", "not", token, (self.negation(),))
        if self.accept("("):
            result = self.condition()
            self.require(")")
            return result
        left = self.operand()
        if self.token.kind not in ("=", "<>", "<", "<=", ">", ">="):
            raise SQLError("syntax", "expected a comparison operator", self.token)
        token = self.require(self.token.kind)
        return Expr("comparison", token.value, token, (left, self.operand()))
