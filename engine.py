"""SQL shell connecting all four project modules."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import shlex
import sys

from catalog import Catalog
from opt import measure
from optimizer import Optimizer
from optimizer_stats import analyze
from ra_lexer import QueryError
from sql_compiler import compile_sql
from sql_frontend import run_statement
from storage import load_csv


class Shell:
    def __init__(self, directory, frames):
        self.directory = directory
        self.catalog = Catalog(directory, frames=frames)

    def close(self):
        self.catalog.close()

    def execute(self, statement):
        text = statement.strip().removesuffix(";").strip()
        if not text:
            return
        command, *parts = text.split(maxsplit=1)
        body = parts[0] if parts else ""
        command = command.upper()
        if command == "SET":
            name, sep, value = body.partition("=")
            if not sep or name.strip().lower() != "frames" or int(value) < 3:
                raise ValueError("usage: SET frames = n, with n at least 3")
            self.catalog.close()
            self.catalog = Catalog(self.directory, frames=int(value))
            print(f"frames={int(value)}")
        elif command == "LOAD":
            args = shlex.split(body)
            if len(args) != 3 or args[1].upper() != "FROM":
                raise ValueError("usage: LOAD table FROM 'file.csv'")
            print(f"loaded {load_csv(self.catalog, args[0], args[2])}")
        elif command == "ANALYZE":
            print(json.dumps(analyze(self.catalog, body)))
        else:
            explain = command == "EXPLAIN"
            explain_parts = body.split(maxsplit=1)
            analyze_plan = explain and bool(explain_parts) and explain_parts[0].upper() == "ANALYZE"
            query = (explain_parts[1] if len(explain_parts) == 2 else "") if analyze_plan else body
            plan = compile_sql(query if explain else text, self.catalog)
            if plan["op"] in ("create_table", "insert"):
                if explain:
                    raise ValueError("EXPLAIN requires a query")
                print(json.dumps(run_statement(plan, self.catalog)))
                return
            estimate = Optimizer(self.catalog).optimize(plan)
            if explain and not analyze_plan:
                print("\n".join(estimate.explain()))
                return
            result = measure(estimate, self.catalog, None if explain else sys.stdout)
            if explain:
                for node in result["nodes"]:
                    print(f"{'  ' * node['depth']}{node['op']} est_rows={node['est_rows']} "
                          f"actual_rows={node['tuples_out']} est_cost={node['est_cost']} "
                          f"reads={node['page_reads']} writes={node['page_writes']}")
            print(json.dumps({key: value for key, value in result.items() if key != "nodes"}))


def statements(lines):
    buffer = []
    quoted = False
    for line in lines:
        i = 0
        while i < len(line):
            char = line[i]
            if char == "'":
                if quoted and line[i:i+2] == "''":
                    buffer.extend("''")
                    i += 2
                    continue
                quoted = not quoted
            if not quoted and line[i:i+2] == "--":
                buffer.append("\n")
                break
            if not quoted and char == ";":
                yield "".join(buffer)
                buffer.clear()
            else:
                buffer.append(char)
            i += 1
    if "".join(buffer).strip():
        yield "".join(buffer)


def prompt():
    while True:
        try:
            line = input("db> ")
        except EOFError:
            return
        if line.strip() in (".quit", ".exit"):
            return
        yield line + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="engine")
    parser.add_argument("--db", required=True)
    parser.add_argument("--frames", type=int, default=32)
    parser.add_argument("--file", type=Path)
    args = parser.parse_args(argv)
    if args.frames < 3:
        parser.error("--frames must be at least 3")
    try:
        with closing(Shell(args.db, args.frames)) as shell:
            if args.file:
                lines = args.file.read_text(encoding="utf-8-sig").splitlines(keepends=True)
            else:
                lines = prompt() if sys.stdin.isatty() else sys.stdin
            failed = False
            for statement in statements(lines):
                try:
                    shell.execute(statement)
                except (ValueError, OSError, RuntimeError, QueryError) as error:
                    print(f"error: {error}", file=sys.stderr)
                    failed = True
                    if args.file:
                        break
            return int(failed)
    except (ValueError, OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
