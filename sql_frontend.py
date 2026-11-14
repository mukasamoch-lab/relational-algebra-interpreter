"""Command-line entry point for Module 3."""

import argparse
import json
from pathlib import Path
import sys

from catalog import Catalog
from sql_compiler import compile_sql
from sql_parser import Expr, Parser, SQLError, Token


def describe(value):
    if isinstance(value, Expr):
        return {value.kind: value.value, **({"args": describe(value.args)} if value.args else {})}
    if isinstance(value, Token):
        return value.value
    if isinstance(value, dict):
        return {key: describe(item) for key, item in value.items() if key != "token" and item is not None}
    if isinstance(value, (list, tuple)):
        return [describe(item) for item in value]
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(prog="sql")
    sub = parser.add_subparsers(dest="command", required=True)
    parse = sub.add_parser("parse")
    parse.add_argument("--query", required=True)
    parse.add_argument("--explain", action="store_true")
    for command in ("compile", "check"):
        mode = sub.add_parser(command)
        mode.add_argument("--db", required=True)
        mode.add_argument("--file", required=True)
        if command == "compile":
            mode.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    catalog = None
    try:
        if args.command == "parse":
            result = describe(Parser(args.query).statements())
        else:
            query = Path(args.file).read_text(encoding="utf-8-sig")
            catalog = Catalog(args.db)
            result = compile_sql(query, catalog)
        if args.command != "check":
            print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except SQLError as error:
        print(error, file=sys.stderr)
    except (OSError, ValueError) as error:
        print(f"error: schema: {error} at line 1, column 1", file=sys.stderr)
    finally:
        if catalog is not None:
            catalog.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
