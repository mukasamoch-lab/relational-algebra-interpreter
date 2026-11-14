"""First M2 operators: scan, filter, project, and limit."""

import argparse
import json
import sys

from catalog import Catalog
from ra_engine import Column, compile_condition
from ra_lexer import QueryError
from ra_parser import parse_condition
from record_codec import FIXED_TYPES, decode_record


JOINS = {"nested_loop_join", "sort_merge_join", "hash_join"}

SETS = {"union_all", "union", "intersect", "except"}
GROUPS = {"group_by", "distinct"}

SUPPORTED = {"seqscan", "filter", "project", "limit", "sort"} | JOINS | SETS | GROUPS


def node_error(op, message):
    raise ValueError(f"Node '{op}': {message}")


def validate_plan(plan):
    if not isinstance(plan, dict):
        node_error("unknown", "plan node must be an object")
    op = plan.get("op")
    if not isinstance(op, str) or op not in SUPPORTED:
        node_error(op if isinstance(op, str) else "unknown", "unsupported operator")
    required = {"left": dict, "right": dict, "condition": str} if op in JOINS else {
        **{name: {"left": dict, "right": dict} for name in SETS},
        "group_by": {"mode": str, "keys": list, "aggregates": list, "child": dict},
        "distinct": {"mode": str, "child": dict},
        "seqscan": {"table": str},
        "filter": {"condition": str, "child": dict},
        "project": {"columns": list, "child": dict},
        "limit": {"n": int, "child": dict},
        "sort": {"keys": list, "child": dict},
    }[op]
    for field, expected in required.items():
        if field not in plan:
            node_error(op, f"missing required field '{field}'")
        if type(plan[field]) is not expected:
            node_error(op, f"field '{field}' must be {expected.__name__}")
    if op == "seqscan" and not plan["table"]:
        node_error(op, "table must be nonempty")
    if op == "project":
        columns = plan["columns"]
        if not columns or not all(isinstance(name, str) for name in columns):
            node_error(op, "columns must be a nonempty list of column names")
        if len(set(columns)) != len(columns):
            node_error(op, "output has duplicate qualified column names")
    if op == "sort":
        keys = plan["keys"]
        if not keys:
            node_error(op, "keys must be a nonempty list")
        for key in keys:
            if (
                not isinstance(key, list) or len(key) != 2
                or not isinstance(key[0], str)
                or key[1] not in ("asc", "desc")
            ):
                node_error(op, "each key must be [column, 'asc' or 'desc']")
    if op == "limit" and plan["n"] < 0:
        node_error(op, "n must be nonnegative")
    if op in GROUPS and plan["mode"] not in ("sort", "hash"):
        node_error(op, "mode must be sort or hash")
    if op == "group_by":
        if not all(isinstance(key, str) for key in plan["keys"]):
            node_error(op, "keys must contain column names")
        for agg in plan["aggregates"]:
            if (not isinstance(agg, list) or len(agg) != 2
                    or not all(isinstance(value, str) for value in agg)):
                node_error(op, "each aggregate must be [function, column]")
        if not plan["keys"] and not plan["aggregates"]:
            node_error(op, "at least one key or aggregate is required")
    if op in JOINS | SETS:
        validate_plan(plan["left"])
        validate_plan(plan["right"])
    elif op != "seqscan":
        validate_plan(plan["child"])


class Operator:
    def __init__(self, op, schema, child=None):
        self.op = op
        self.schema = schema
        self.child = child
        self.inputs = () if child is None else (child,)
        if child is not None:
            self.pool = child.pool
        self.opened = False
        self.counts = {
            "tuples_out": 0, "comparisons": 0, "page_reads": 0, "page_writes": 0,
        }

    def open(self):
        if self.opened:
            raise RuntimeError(f"{self.op} is already open")
        for name in self.counts:
            self.counts[name] = 0
        if self.child is not None:
            self.child.open()
        self.opened = True

    def next(self):
        if not self.opened:
            raise RuntimeError(f"{self.op} is not open")
        with self.pool.working_memory(1):
            row = self._next()
            if row is not None:
                self.counts["tuples_out"] += 1
            return row

    def close(self):
        if self.child is not None:
            self.child.close()
        self.opened = False

    def counters(self):
        return dict(self.counts)


class SeqScan(Operator):
    def __init__(self, catalog, info):
        self.heap = catalog.open_heap(info)
        self.rows = None
        self.pool = catalog.pool
        schema = [[f"{info['name']}.{column[0]}", *column[1:]] for column in info["schema"]]
        super().__init__("seqscan", schema)
        self.record_schema = info["schema"]
        self.table = info["name"]

    def open(self):
        super().open()
        self.rows = self.heap.scan(copy_records=False)

    def _next(self):
        before = self.pool.stats()
        try:
            item = next(self.rows, None)
            return None if item is None else decode_record(self.record_schema, item[1])
        finally:
            after = self.pool.stats()
            self.counts["page_reads"] += after["reads"] - before["reads"]
            self.counts["page_writes"] += after["writes"] - before["writes"]

    def close(self):
        if self.rows is not None:
            self.rows.close()
            self.rows = None
        super().close()


class Filter(Operator):
    def __init__(self, child, condition):
        super().__init__("filter", child.schema, child)
        self.condition = condition
        tree = parse_condition(condition)
        names = {column[0] for column in child.schema}
        has_real = any(column[1] == "REAL" for column in child.schema)
        pending = [tree]
        while pending:
            node = pending.pop()
            if node.kind == "Attr" and "." not in node.label and node.label not in names:
                node_error("filter", f"column '{node.label}' must be qualified")
            if node.kind == "Num" and "." in node.label and not has_real:
                node_error("filter", "numeric literals must be integers")
            pending.extend(node.children)
        columns = tuple(
            Column(name.rpartition(".")[2], name.rpartition(".")[0],
                   "number" if kind in FIXED_TYPES else "string")
            for name, kind, *options in child.schema
        )
        self.predicate = compile_condition(tree, columns)

    def _next(self):
        while True:
            row = self.child.next()
            if row is None:
                return None
            self.counts["comparisons"] += 1
            if self.predicate(row):
                return row
            row = None


class Project(Operator):
    def __init__(self, child, columns):
        names = [column[0] for column in child.schema]
        for name in columns:
            if name not in names:
                node_error("project", f"unknown column '{name}'")
        self.indices = [names.index(name) for name in columns]
        super().__init__("project", [child.schema[index] for index in self.indices], child)

    def _next(self):
        row = self.child.next()
        return None if row is None else tuple(row[index] for index in self.indices)


class Limit(Operator):
    def __init__(self, child, count):
        super().__init__("limit", child.schema, child)
        self.limit = count
        self.returned = 0

    def open(self):
        super().open()
        self.returned = 0
        if self.limit == 0:
            self.child.close()

    def _next(self):
        if self.returned == self.limit:
            return None
        row = self.child.next()
        if row is None:
            self.returned = self.limit
            self.child.close()
            return None
        self.returned += 1
        if self.returned == self.limit:
            self.child.close()
        return row


def build_plan(plan, catalog):
    """Validate the whole tree before opening operators or reading table pages."""
    validate_plan(plan)

    def build(node):
        op = node["op"]
        try:
            if op == "seqscan":
                return SeqScan(catalog, catalog.get_table(node["table"]))
            if op in JOINS:
                from joins import Join
                return Join(op, build(node["left"]), build(node["right"]),
                            node["condition"], catalog)
            if op in SETS:
                from set_group_ops import Concat, SetOperation
                left, right = build(node["left"]), build(node["right"])
                if op == "union_all":
                    return Concat(left, right)
                return SetOperation(op, left, right, catalog)
            child = build(node["child"])
            if op in GROUPS:
                from set_group_ops import Group
                return Group(op, child, node.get("keys"), node.get("aggregates", []),
                             node["mode"], catalog)
            if op == "filter":
                return Filter(child, node["condition"])
            if op == "project":
                return Project(child, node["columns"])
            if op == "sort":
                from external_sort import Sort
                return Sort(child, node["keys"], catalog)
            return Limit(child, node["n"])
        except QueryError as error:
            node_error(op, str(error))
        except ValueError as error:
            if str(error).startswith("Node '"):
                raise
            node_error(op, str(error))

    return build(plan)


def nodes_with_depth(root, depth=0):
    yield depth, root
    for child in root.inputs:
        yield from nodes_with_depth(child, depth + 1)


def nodes(root):
    for _, operator in nodes_with_depth(root):
        yield operator


def totals(root):
    operators = list(nodes(root))
    return {
        "tuples_out": root.counts["tuples_out"],
        "comparisons": sum(op.counts["comparisons"] for op in operators),
        "page_reads": sum(op.counts["page_reads"] for op in operators),
        "page_writes": sum(op.counts["page_writes"] for op in operators),
    }


def format_row(row, schema):
    values = (
        f"{value:.4f}" if column[1] == "REAL" else json.dumps(value, ensure_ascii=False)
        for value, column in zip(row, schema)
    )
    return "[" + ", ".join(values) + "]"


def execute(root, output, as_json):
    root.open()
    try:
        if as_json:
            output.write('{"schema":' + json.dumps(root.schema) + ',"rows":[')
        count = 0
        while True:
            row = root.next()
            if row is None:
                break
            if as_json:
                output.write(("," if count else "") + format_row(row, root.schema))
            else:
                output.write(format_row(row, root.schema) + "\n")
            count += 1
            row = None
    finally:
        root.close()
    if as_json:
        output.write('],"totals":' + json.dumps(totals(root)) + "}\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Module 2 execution engine")
    parser.add_argument("command", choices=("run", "trace"))
    parser.add_argument("--db", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--frames", type=int, default=32)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    catalog = None
    try:
        with open(args.plan, encoding="utf-8-sig") as source:
            plan = json.load(source)
        validate_plan(plan)
        catalog = Catalog(args.db, frames=args.frames)
        root = build_plan(plan, catalog)
        if args.command == "run":
            execute(root, sys.stdout, args.json)
        else:
            root.open()
            try:
                while root.next() is not None:
                    pass
            finally:
                root.close()
            for depth, op in nodes_with_depth(root):
                label = f"{op.op} {op.table}" if isinstance(op, SeqScan) else op.op
                counts = op.counters()
                extra = ""
                if "passes" in counts:
                    extra = (
                        f" passes={counts['passes']} initial_runs={counts['initial_runs']}"
                        f" peak_frames={counts['peak_frames']}"
                    )
                print(
                    f"{'  ' * depth}{label} tuples_out={counts['tuples_out']} "
                    f"comparisons={counts['comparisons']} reads={counts['page_reads']} "
                    f"writes={counts['page_writes']}{extra}"
                )
        return 0
    except json.JSONDecodeError as error:
        print(
            f"error: JSON syntax at line {error.lineno}, column {error.colno}: {error.msg}",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    finally:
        if catalog is not None:
            catalog.close()


if __name__ == "__main__":
    sys.modules.setdefault("executor", sys.modules[__name__])
    raise SystemExit(main())
