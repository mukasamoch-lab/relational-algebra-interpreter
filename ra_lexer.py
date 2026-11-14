"""Handwritten tokenizer for relational algebra text."""

from dataclasses import dataclass


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


def tokenize(source: str, *, multiline_strings: bool = False) -> list[Token]:
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
                if i >= len(source) or (not multiline_strings and source[i] in "\r\n"):
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
        if ch.isascii() and (ch.isalpha() or ch == "_"):
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
