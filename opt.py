"""Optimizer command-line tools and estimated/actual plan comparison."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import sys
from time import perf_counter

from catalog import Catalog
from executor import build_plan, format_row, totals
from optimizer import Optimizer, sort_cost
from optimizer_stats import analyze
from ra_lexer import QueryError


def measure(plan, catalog, output=None):
    start = perf_counter()
    root = build_plan(plan.plan, catalog)
    with closing(root):
        root.open()
        while (row := root.next()) is not None:
            if output is not None:
                output.write(format_row(row, root.schema) + "\n")
            row = None
    elapsed = (perf_counter() - start) * 1000

    def compare(operator, estimate, depth=0):
        implicit_sort = operator.op == "sort" and estimate.plan["op"] != "sort"
        cost = estimate.cost
        if implicit_sort:
            cost += sort_cost(estimate.pages, catalog.pool.capacity)
        elif operator.op != estimate.plan["op"]:
            raise ValueError(f"estimate tree expected {estimate.plan['op']}, got {operator.op}")
        actual = operator.counters()
        yield {"depth": depth, "op": operator.op, "est_rows": estimate.rows,
               "est_cost": cost, **actual}
        sources = (estimate,) if implicit_sort else estimate.children
        for child, expected in zip(operator.inputs, sources, strict=True):
            yield from compare(child, expected, depth + 1)

    return {**totals(root), "wall_ms": elapsed, "est_rows": plan.rows,
            "nodes": list(compare(root, plan))}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="opt")
    sub = parser.add_subparsers(dest="command", required=True)
    for verb in ("analyze", "plan", "explain", "dp-table"):
        cmd = sub.add_parser(verb)
        cmd.add_argument("--db", required=True)
        cmd.add_argument("--frames", type=int, default=32)
        if verb == "analyze":
            cmd.add_argument("--table", required=True)
            cmd.add_argument("--buckets", type=int, default=20)
        else:
            cmd.add_argument("--logical", type=Path, required=True)
            cmd.add_argument("--disable-rule", type=int, choices=range(1, 8), action="append", default=[])
            if verb == "plan":
                cmd.add_argument("--json", action="store_true")
            if verb == "explain":
                cmd.add_argument("--before", action="store_true")
    args = parser.parse_args(argv)
    try:
        with closing(Catalog(args.db, frames=args.frames)) as catalog:
            if args.command == "analyze":
                print(json.dumps(analyze(catalog, args.table, args.buckets), indent=2))
                return 0
            logical = json.loads(args.logical.read_text(encoding="utf-8-sig"))
            optimizer = Optimizer(catalog)
            result = optimizer.optimize(logical, args.disable_rule)
            if args.command == "plan":
                print(json.dumps(result.plan, indent=2))
            elif args.command == "dp-table":
                print(json.dumps(optimizer.dp_tables, indent=2))
            else:
                if args.before:
                    print("Written order:")
                    print("\n".join(optimizer.estimate(logical, baseline=True).explain()))
                    print("Optimized:")
                print("\n".join(result.explain()))
    except (ValueError, OSError, RuntimeError, QueryError, KeyError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
