"""Estimate cardinality and enumerate physical left-deep join trees."""

from dataclasses import dataclass, field
from itertools import combinations
from math import ceil, prod
import struct

from optimizer_rewrite import outputs, references, render, rewrite, split
from physical_plan import lower_plan
from ra_parser import parse_condition
from record_codec import FIXED_TYPES
from slotted_page import PAGE_SIZE


def proportion(expr, statistics):
    kind = expr.kind
    if kind in ("And", "Or", "Not"):
        values = [proportion(child, statistics) for child in expr.children]
        if kind == "And":
            return prod(values)
        if kind == "Or":
            return sum(values) - prod(values)
        return 1 - values[0]
    left, right = expr.children
    if left.kind != "Attr" and right.kind == "Attr":
        left, right = right, left
        kind = {"Lt": "Gt", "Le": "Ge", "Gt": "Lt", "Ge": "Le"}.get(kind, kind)
    if left.kind != "Attr" or right.kind not in ("Num", "Str"):
        return 0.1
    stats = statistics.get(left.label, {})
    value = float(right.label) if right.kind == "Num" else right.label
    hist = stats.get("hist", [])
    if not hist:
        eq = 1 / max(1, stats.get("ndv", 10))
        return {"Eq": eq, "Ne": 1 - eq}.get(kind, 1 / 3)
    total = sum(bucket["count"] for bucket in hist)
    equal = below = 0
    for bucket in hist:
        lo, hi, count, ndv = (bucket[k] for k in ("min", "max", "count", "ndv"))
        if lo <= value <= hi:
            equal += count / ndv
        if hi < value:
            below += count
        elif lo < value <= hi:
            if isinstance(value, str):
                below += count * (0.5 if value < hi else 1 - 1 / ndv)
            else:
                below += count * min(1 - 1 / ndv, (value - lo) / (hi - lo))
    eq, lt = equal / total, below / total
    return max(0, min(1, {"Eq": eq, "Ne": 1 - eq, "Lt": lt, "Le": lt + eq,
                         "Gt": 1 - lt - eq, "Ge": 1 - lt}[kind]))


def sort_cost(pages, frames):
    runs = max(1, ceil(pages / frames))
    passes = 0
    while runs > 1:
        runs = ceil(runs / (frames - 1))
        passes += 1
    return pages * (1 + 2 * passes)


@dataclass
class Estimate:
    plan: dict
    rows: int
    cost: int
    columns: dict
    statistics: dict = field(default_factory=dict)
    children: tuple = ()
    disk_pages: int | None = None

    @property
    def width(self):
        return sum(self.columns.values())

    @property
    def pages(self):
        return self.disk_pages if self.disk_pages is not None else ceil(self.rows * self.width / PAGE_SIZE)

    def explain(self, depth=0):
        detail = self.plan.get("table", self.plan.get("condition", ""))
        label = f"{self.plan['op']} {detail}".rstrip()
        yield f"{'  ' * depth}{label} est_rows={self.rows} est_cost={self.cost}"
        for child in self.children:
            yield from child.explain(depth + 1)


class Optimizer:
    def __init__(self, catalog):
        self.catalog = catalog
        self.frames = catalog.pool.capacity
        if self.frames < 3:
            raise ValueError("optimizer requires at least three frames")
        self.tables = {}
        self.dp_tables = []

    def table(self, name):
        if name not in self.tables:
            self.tables[name] = self.catalog.get_table(name)
        return self.tables[name]

    def table_columns(self, name):
        return [column[0] for column in self.table(name)["schema"]]

    def optimize(self, logical, disabled=()):
        self.dp_tables.clear()
        return self.estimate(rewrite(logical, self.table_columns, disabled))

    def scan(self, node):
        table = node["table"]
        info = self.table(table)
        statistics = info["statistics"]
        sizes = {col: struct.calcsize(FIXED_TYPES[kind]) if kind in FIXED_TYPES else 2 + extra[0]
                 for col, kind, *extra in info["schema"]}
        declared = sum(sizes.values())
        width = statistics.get("width", 0)
        if width and declared:
            sizes = {key: value * width / declared for key, value in sizes.items()}
        columns = {f"{table}.{key}": value for key, value in sizes.items()}
        stats = {f"{table}.{key}": value for key, value in statistics.get("columns", {}).items()}
        return Estimate(lower_plan(node), max(1, info["num_tuples"]), info["num_pages"],
                        columns, stats, disk_pages=info["num_pages"])

    def project(self, child, columns, aliases):
        physical = {"op": "project", "columns": columns, "child": child.plan}
        if columns != aliases:
            physical["aliases"] = aliases
        sizes = {alias: child.columns[col] for col, alias in zip(columns, aliases)}
        stats = {alias: child.statistics[col] for col, alias in zip(columns, aliases)
                 if col in child.statistics}
        return Estimate(physical, child.rows, child.cost, sizes, stats, (child,))

    def estimate(self, node, baseline=False):
        op = node["op"]
        if op == "scan":
            return self.scan(node)
        if op in ("join", "cross"):
            if not baseline:
                return self.enumerate(node)
            left, right = (self.estimate(node[key], baseline) for key in ("left", "right"))
            predicates = [parse_condition(node["condition"])] if op == "join" else []
            return self.join(left, right, predicates, "nested_loop_join")
        physical = lower_plan(node)
        if op in ("union", "union_all", "intersect", "except"):
            left, right = (self.estimate(node[key], baseline) for key in ("left", "right"))
            physical.update(left=left.plan, right=right.plan)
            rows = left.rows + right.rows if op.startswith("union") else left.rows
            extra = 0 if op == "union_all" else sum(sort_cost(c.pages, self.frames) for c in (left, right))
            return Estimate(physical, rows, left.cost + right.cost + extra,
                            left.columns, children=(left, right))
        child = self.estimate(node["child"], baseline)
        physical["child"] = child.plan
        rows, cost, columns, stats = child.rows, child.cost, child.columns, child.statistics
        if op == "project":
            return self.project(child, node["columns"], node.get("aliases", node["columns"]))
        if op == "rename":
            mapping = {key: f"{node['to']}.{key.rsplit('.', 1)[-1]}" for key in columns}
            columns = {mapping[key]: value for key, value in columns.items()}
            stats = {mapping[key]: value for key, value in stats.items()}
        if op == "select":
            rows = max(1, ceil(rows * proportion(parse_condition(node["condition"]), stats)))
        if op == "limit":
            rows = max(1, min(rows, node["n"]))
        if op in ("sort", "group_by", "distinct"):
            cost += sort_cost(child.pages, self.frames)
        if op == "group_by":
            keys = node["keys"]
            rows = max(1, min(rows, prod(stats.get(key, {}).get("ndv", 10) for key in keys)))
            result = outputs(node, self.table_columns)
            cols = [columns[key] for key in keys]
            cols.extend(columns.get(agg[1], 8) if agg[0] in ("min", "max") else 8
                        for agg in node["aggregates"])
            columns = dict(zip(result, cols))
            stats = {key: stats[key] for key in keys if key in stats}
        return Estimate(physical, rows, cost, columns, stats, (child,))

    @staticmethod
    def equi(expr, left, right):
        if expr.kind != "Eq" or any(child.kind != "Attr" for child in expr.children):
            return False
        a, b = (child.label for child in expr.children)
        return (a in left.columns and b in right.columns) or (b in left.columns and a in right.columns)

    def join(self, left, right, predicates, method):
        columns = {**left.columns, **right.columns}
        stats = {**left.statistics, **right.statistics}
        equis = [expr for expr in predicates if self.equi(expr, left, right)]
        workspace = max(1, ceil(sum(columns.values()) / PAGE_SIZE))
        m, n, b = left.pages, right.pages, self.frames
        if method == "nested_loop_join":
            chosen, residual = predicates, []
            extra = m + ceil(m / max(1, b - workspace - 1)) * n
        else:
            chosen = equis[:1]
            residual = [expr for expr in predicates if expr is not chosen[0]]
            extra = (3 * (m + n) if method == "hash_join"
                     else sort_cost(m, b) + sort_cost(n, b) + m + n)
        denominator = max([1, *(stats.get(operand.label, {}).get("ndv", 10)
                              for expr in equis if expr in chosen for operand in expr.children)])
        rows = max(1, ceil(left.rows * right.rows / denominator
                           * 0.1 ** sum(expr not in equis for expr in chosen)))
        condition = " and ".join(f"({render(expr)})" for expr in chosen) or "1 = 1"
        plan = {"op": method, "condition": condition, "left": left.plan, "right": right.plan}
        estimate = Estimate(plan, rows, left.cost + right.cost + extra, columns, stats, (left, right))
        if residual:
            condition = " and ".join(f"({render(expr)})" for expr in residual)
            residual_rows = max(1, ceil(rows * 0.1 ** len(residual)))
            return Estimate({"op": "filter", "condition": condition, "child": plan},
                            residual_rows, estimate.cost, columns, stats, (estimate,))
        return estimate

    def enumerate(self, node):
        leaves, predicates = [], []

        def collect(current):
            if current["op"] in ("join", "cross"):
                collect(current["left"])
                collect(current["right"])
                if current["op"] == "join":
                    predicates.extend(split(parse_condition(current["condition"])))
            else:
                leaves.append(self.estimate(current))

        collect(node)
        if len(leaves) > 6 or any(
            not references(expr) or any(references(expr) <= set(leaf.columns) for leaf in leaves)
            for expr in predicates
        ):
            return self.estimate(node, baseline=True)
        columns = [name for leaf in leaves for name in leaf.columns]
        if len(set(columns)) != len(columns):
            return self.estimate(node, baseline=True)
        dp = {frozenset([i]): leaf for i, leaf in enumerate(leaves)}
        listing = []
        for size in range(1, len(leaves) + 1):
            for ids in combinations(range(len(leaves)), size):
                subset = frozenset(ids)
                if size > 1:
                    connected, cross = [], []
                    for i in ids:
                        left, right = dp[subset - {i}], leaves[i]
                        left_names, right_names = set(left.columns), set(right.columns)
                        clauses = [expr for expr in predicates
                                   if (refs := references(expr)) <= left_names | right_names
                                   and refs & left_names and refs & right_names]
                        methods = ["nested_loop_join"]
                        if any(self.equi(expr, left, right) for expr in clauses):
                            methods.extend(("sort_merge_join", "hash_join"))
                        target = connected if clauses else cross
                        target.extend(self.join(left, right, clauses, method) for method in methods)
                    dp[subset] = min(connected or cross, key=lambda item: item.cost)
                best = dp[subset]
                listing.append({"subset": list(ids), "sources": [list(leaves[i].columns) for i in ids],
                                "rows": best.rows, "pages": best.pages, "cost": best.cost, "plan": best.plan})
        self.dp_tables.append(listing)
        winner = dp[frozenset(range(len(leaves)))]
        return self.project(winner, columns, columns) if list(winner.columns) != columns else winner
