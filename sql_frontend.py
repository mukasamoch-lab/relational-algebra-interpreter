"""Compile SQL and run it through the storage and execution modules."""

import argparse
from io import StringIO
import json
from pathlib import Path
import sys
from time import perf_counter

from catalog import Catalog
from executor import build_plan, execute, nodes_with_depth
from physical_plan import lower_plan
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


def run_statement(plan, catalog):
    op = plan["op"]
    if op == "create_table":
        catalog.create_table(plan["table"], plan["schema"])
        return {"created": plan["table"]}
    if op == "insert":
        rid = catalog.open_table(plan["table"]).insert(plan["values"])
        return {"inserted": plan["table"], "rid": rid}

    start = perf_counter()
    root = build_plan(lower_plan(plan), catalog)
    output = StringIO()
    execute(root, output, as_json=True)
    wall_ms = (perf_counter() - start) * 1000
    result = json.loads(output.getvalue())
    result["wall_ms"] = wall_ms
    result["operators"] = [
        {"depth": depth, "op": operator.op, **operator.counters()}
        for depth, operator in nodes_with_depth(root)
    ]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog="sql")
    sub = parser.add_subparsers(dest="command", required=True)
    parse = sub.add_parser("parse")
    parse.add_argument("--query", required=True)
    parse.add_argument("--explain", action="store_true")
    for command in ("compile", "check", "run"):
        mode = sub.add_parser(command)
        mode.add_argument("--db", required=True)
        mode.add_argument("--file", required=True)
        if command == "run":
            mode.add_argument("--frames", type=int, default=32)
        if command == "compile":
            mode.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    catalog = None
    try:
        if args.command == "parse":
            result = describe(Parser(args.query).statements())
        else:
            query = Path(args.file).read_text(encoding="utf-8-sig")
            catalog = Catalog(args.db, frames=getattr(args, "frames", 8))
            result = compile_sql(query, catalog)
        if args.command == "run":
            if isinstance(result, list):
                result = [run_statement(plan, catalog) for plan in result]
            else:
                result = run_statement(result, catalog)
        if args.command != "check":
            print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except SQLError as error:
        print(error, file=sys.stderr)
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
    except (OSError, ValueError) as error:
        print(f"error: schema: {error} at line 1, column 1", file=sys.stderr)
    finally:
        if catalog is not None:
            catalog.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
