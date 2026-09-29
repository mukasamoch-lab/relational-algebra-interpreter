"""In-memory set relations and bottom-up evaluation of the RA parse tree."""

from __future__ import annotations

from dataclasses import dataclass, field

from ra import Node, QueryError


@dataclass(frozen=True)
class Column:
    name: str
    qualifier: str
    type_name: str | None

    @property
    def qualified(self) -> str:
        return f"{self.qualifier}.{self.name}" if self.qualifier else self.name


@dataclass
class Relation:
    name: str
    columns: tuple[Column, ...]
    rows: list[tuple[int | float | str, ...]]


def equal_tuple(left: tuple, right: tuple) -> bool:
    """Our explicit tuple equality: equal arity and equal typed values at each position."""
    if len(left) != len(right):
        return False
    for a, b in zip(left, right):
        if isinstance(a, str) != isinstance(b, str) or a != b:
            return False
    return True


def contains_row(rows: list[tuple], candidate: tuple) -> bool:
    for existing in rows:
        if equal_tuple(existing, candidate):
            return True
    return False


def add_unique(rows: list[tuple], candidate: tuple,
               buckets: dict[int, list[tuple]] | None = None) -> None:
    """Use hash only to find candidates; our equal_tuple decides duplication."""
    if buckets is None:
        if not contains_row(rows, candidate):
            rows.append(candidate)
        return
    bucket = buckets.setdefault(hash(candidate), [])
    for existing in bucket:
        if equal_tuple(existing, candidate):
            return
    bucket.append(candidate)
    rows.append(candidate)


def value_type(value: object) -> str:
    return "string" if isinstance(value, str) else "number"


def resolve(columns: tuple[Column, ...], reference: str, position: int) -> int:
    matches = [i for i, col in enumerate(columns)
               if col.qualified == reference or ("." not in reference and col.name == reference)]
    if not matches:
        raise QueryError("Name", f"unknown attribute {reference!r}", position)
    if len(matches) > 1:
        raise QueryError("Name", f"ambiguous attribute {reference!r}; qualify it", position)
    return matches[0]


def check_condition(node: Node, columns: tuple[Column, ...]) -> None:
    """Resolve names and known column types even when the relation has no rows."""
    if node.kind in ("And", "Or", "Not"):
        for child in node.children:
            check_condition(child, columns)
    elif node.kind in ("Eq", "Ne", "Lt", "Le", "Gt", "Ge"):
        types = []
        for child in node.children:
            if child.kind == "Attr":
                types.append(columns[resolve(columns, child.label, child.position)].type_name)
            else:
                types.append("number" if child.kind == "Num" else "string")
        if types[0] is not None and types[1] is not None and types[0] != types[1]:
            raise QueryError("Type", "cannot compare a number with a string", node.position)


def compile_condition(node: Node, columns: tuple[Column, ...]):
    """Resolve attribute positions once; the returned function tests each row/pair."""
    check_condition(node, columns)
    if node.kind == "And":
        left = compile_condition(node.children[0], columns)
        right = compile_condition(node.children[1], columns)
        return lambda row: left(row) and right(row)
    if node.kind == "Or":
        left = compile_condition(node.children[0], columns)
        right = compile_condition(node.children[1], columns)
        return lambda row: left(row) or right(row)
    if node.kind == "Not":
        child = compile_condition(node.children[0], columns)
        return lambda row: not child(row)

    def read_operand(child: Node):
        if child.kind == "Attr":
            index = resolve(columns, child.label, child.position)
            return lambda row: row[index]
        if child.kind == "Str":
            value = child.label
        else:
            value = float(child.label) if "." in child.label else int(child.label)
        return lambda row: value

    read_left = read_operand(node.children[0])
    read_right = read_operand(node.children[1])
    operation = node.kind

    def test(row: tuple) -> bool:
        left = read_left(row)
        right = read_right(row)
        if value_type(left) != value_type(right):
            raise QueryError("Type", "cannot compare a number with a string", node.position)
        if operation == "Eq":
            return left == right
        if operation == "Ne":
            return left != right
        if operation == "Lt":
            return left < right
        if operation == "Le":
            return left <= right
        if operation == "Gt":
            return left > right
        return left >= right

    return test


def compatible(left: Relation, right: Relation, position: int) -> None:
    if len(left.columns) != len(right.columns):
        raise QueryError("Schema", "different numbers of attributes", position)
    for a, b in zip(left.columns, right.columns):
        if a.name != b.name:
            raise QueryError("Schema", "attribute names or order differ", position)
        if a.type_name is not None and b.type_name is not None and a.type_name != b.type_name:
            raise QueryError("Schema", f"incompatible types for {a.name!r}", position)


def product_columns(left: Relation, right: Relation, position: int) -> tuple[Column, ...]:
    columns = left.columns + right.columns
    labels = [c.qualified for c in columns]
    if len(labels) != len(set(labels)):
        raise QueryError("Schema", "qualified attribute names collide; rename an input", position)
    return columns


@dataclass
class OperatorCount:
    operator: str
    position: int
    evaluations: int = 0


@dataclass
class Counters:
    select_evaluations: int = 0
    join_comparisons: int = 0
    operators: list[OperatorCount] = field(default_factory=list)

    def register(self, operator: str, position: int) -> OperatorCount:
        entry = OperatorCount(operator, position)
        self.operators.append(entry)
        return entry


def evaluate(tree: Node, catalog: dict[str, Relation], counters: Counters | None = None) -> Relation:
    if counters is None:
        counters = Counters()
    kind = tree.kind
    if kind == "Relation":
        if tree.label not in catalog:
            raise QueryError("Name", f"unknown relation {tree.label!r}", tree.position)
        return catalog[tree.label]
    if kind in ("Select", "Project", "Rename"):
        child = evaluate(tree.children[-1], catalog, counters)
        if kind == "Rename":
            return Relation(tree.label, tuple(Column(c.name, tree.label, c.type_name) for c in child.columns), child.rows.copy())
        if kind == "Project":
            refs = [part.strip() for part in tree.label.split(",")]
            if len(refs) != len(set(refs)):
                raise QueryError("Schema", "duplicate attribute in projection", tree.position)
            indices = [resolve(child.columns, ref, tree.position) for ref in refs]
            if len(indices) != len(set(indices)):
                raise QueryError("Schema", "same attribute projected twice", tree.position)
            cols = tuple(child.columns[i] for i in indices)
            rows: list[tuple] = []
            buckets: dict[int, list[tuple]] = {}
            for row in child.rows:
                add_unique(rows, tuple(row[i] for i in indices), buckets)
            return Relation(child.name, cols, rows)
        condition = tree.children[0]
        predicate = compile_condition(condition, child.columns)
        entry = counters.register("select", tree.position)
        rows = []
        for row in child.rows:
            counters.select_evaluations += 1
            entry.evaluations += 1
            if predicate(row):
                rows.append(row)
        return Relation(child.name, child.columns, rows)

    left = evaluate(tree.children[0], catalog, counters)
    right = evaluate(tree.children[-1], catalog, counters)
    if kind in ("Union", "Intersect", "Minus"):
        compatible(left, right, tree.position)
        rows: list[tuple] = []
        if kind == "Union":
            buckets: dict[int, list[tuple]] = {}
            for row in left.rows + right.rows:
                add_unique(rows, row, buckets)
        else:
            for row in left.rows:
                present = contains_row(right.rows, row)
                if present == (kind == "Intersect"):
                    add_unique(rows, row)
        return Relation(left.name, left.columns, rows)
    if kind in ("Times", "Join"):
        cols = product_columns(left, right, tree.position)
        condition = tree.children[1] if kind == "Join" else None
        predicate = compile_condition(condition, cols) if condition is not None else None
        entry = counters.register("join", tree.position) if condition is not None else None
        rows = []
        for a in left.rows:
            for b in right.rows:
                pair = a + b
                if predicate is not None:
                    counters.join_comparisons += 1
                    entry.evaluations += 1
                    if not predicate(pair):
                        continue
                # Distinct input tuples produce distinct concatenated tuples.
                rows.append(pair)
        return Relation("", cols, rows)
    raise QueryError("Syntax", f"unknown tree operation {kind!r}", tree.position)


def display(relation: Relation) -> str:
    header = " | ".join(col.qualified for col in relation.columns)
    return header + "\n" + "\n".join(" | ".join(str(value) for value in row) for row in relation.rows)
