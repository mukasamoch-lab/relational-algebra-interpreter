"""Line-aware relation-definition loader; each tuple occupies one line."""

from ra import QueryError, tokenize
from ra_engine import Column, Relation, add_unique, value_type


def identifier(text: str) -> bool:
    return bool(text) and text[0].isascii() and text[0].isalpha() and all(
        ch.isascii() and (ch.isalnum() or ch == "_") for ch in text[1:]
    )


def split_values(line: str, offset: int) -> list[str]:
    values: list[str] = []
    start = 0
    inside = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "'":
            if inside and i + 1 < len(line) and line[i + 1] == "'":
                i += 2
                continue
            inside = not inside
        elif ch == "," and not inside:
            values.append(line[start:i].strip())
            start = i + 1
        i += 1
    if inside:
        raise QueryError("Lexical", "unterminated quoted string", offset + start)
    values.append(line[start:].strip())
    if any(not value for value in values):
        raise QueryError("Syntax", "missing tuple value", offset)
    return values


def parse_value(text: str, offset: int) -> int | float | str:
    if text.startswith("'"):
        tokens = tokenize(text)
        if len(tokens) != 2 or tokens[0].kind != "STRING":
            raise QueryError("Syntax", "unexpected text after quoted value", offset)
        return str(tokens[0].value)
    if "'" in text or any(ch.isspace() or ch in ",(){}" for ch in text):
        raise QueryError("Syntax", "string requires single quotes", offset)
    # A numeric spelling is recognized only if the whole bare value matches it.
    i = 1 if text.startswith("-") else 0
    before = 0
    while i < len(text) and text[i].isascii() and text[i].isdigit():
        before += 1
        i += 1
    if before and i < len(text) and text[i] == ".":
        i += 1
        after = 0
        while i < len(text) and text[i].isascii() and text[i].isdigit():
            after += 1
            i += 1
        if after and i == len(text):
            return float(text)
    elif before and i == len(text):
        return int(text)
    return text


def load_relations(source: str) -> dict[str, Relation]:
    catalog: dict[str, Relation] = {}
    unique_buckets: dict[str, dict[int, list[tuple]]] = {}
    current: Relation | None = None
    offset = 0
    for raw in source.splitlines(keepends=True):
        line = raw.rstrip("\r\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            offset += len(raw)
            continue
        if current is None:
            opening = stripped.find("(")
            closing = stripped.find(")", opening + 1)
            equals = stripped.find("=", closing + 1)
            brace = stripped.find("{", equals + 1)
            if min(opening, closing, equals, brace) < 0 or stripped[closing + 1:equals].strip() or stripped[equals + 1:brace].strip():
                raise QueryError("Syntax", "expected Name(attributes) = {", offset)
            name = stripped[:opening].strip()
            attrs = [part.strip() for part in stripped[opening + 1:closing].split(",")]
            if not identifier(name) or not attrs or any(not identifier(a) for a in attrs):
                raise QueryError("Syntax", "invalid relation or attribute name", offset)
            if len(attrs) != len(set(attrs)):
                raise QueryError("Schema", "duplicate attribute name", offset)
            if name in catalog:
                raise QueryError("Name", f"relation {name!r} already defined", offset)
            tail = stripped[brace + 1:].strip()
            if tail not in ("", "}"):
                raise QueryError("Syntax", "tuples must start on a new line", offset)
            current = Relation(name, tuple(Column(a, name, None) for a in attrs), [])
            catalog[name] = current
            unique_buckets[name] = {}
            if tail == "}":
                current = None
        elif stripped == "}":
            current = None
        else:
            parts = split_values(line, offset)
            if len(parts) != len(current.columns):
                raise QueryError("Schema", f"expected {len(current.columns)} values, found {len(parts)}", offset)
            row = tuple(parse_value(part, offset) for part in parts)
            updated = []
            for col, val in zip(current.columns, row):
                typ = value_type(val)
                if col.type_name is not None and col.type_name != typ:
                    raise QueryError("Type", f"mixed types in column {col.name!r}", offset)
                updated.append(Column(col.name, col.qualifier, typ))
            current.columns = tuple(updated)
            add_unique(current.rows, row, unique_buckets[current.name])
        offset += len(raw)
    if current is not None:
        raise QueryError("Syntax", "missing closing '}'", len(source))
    return catalog
