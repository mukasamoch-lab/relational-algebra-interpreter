"""Command-line interface and Bonus Project 1 compatibility imports."""

import argparse
import sys

from ra_lexer import QueryError, Token, tokenize
from ra_parser import Parser, parse, parse_condition
from ra_tree import Node, draw_tree


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
