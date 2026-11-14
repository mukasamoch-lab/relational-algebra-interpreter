"""Logical rewrites with explicit column dependencies."""

from ra_parser import parse_condition

BINARY = {"join", "cross", "union", "union_all", "intersect", "except"}
SIGNS = {"Eq": "=", "Ne": "!=", "Lt": "<", "Le": "<=", "Gt": ">", "Ge": ">="}


def references(expr):
    if expr.kind == "Attr":
        return {expr.label}
    return set().union(*(references(child) for child in expr.children))


def split(expr):
    if expr.kind != "And":
        return [expr]
    return [item for child in expr.children for item in split(child)]


def render(expr, names=None):
    if expr.kind == "Attr":
        return names.get(expr.label, expr.label) if names is not None else expr.label
    if expr.kind == "Str":
        return "'" + expr.label.replace("'", "''") + "'"
    if expr.kind == "Num":
        return expr.label
    args = [render(child, names) for child in expr.children]
    if expr.kind == "Not":
        return f"not ({args[0]})"
    if expr.kind in ("And", "Or"):
        return f"({args[0]}) {expr.kind.lower()} ({args[1]})"
    return f"{args[0]} {SIGNS[expr.kind]} {args[1]}"


def outputs(node, table_columns):
    op = node["op"]
    if op == "scan":
        return [f"{node['table']}.{col}" for col in table_columns(node["table"])]
    if op == "project":
        return node.get("aliases", node["columns"])
    if op == "group_by":
        return node["keys"] + [
            agg[2] if len(agg) == 3 else f"{agg[0]}_{'star' if agg[1] == '*' else agg[1].replace('.', '_')}"
            for agg in node["aggregates"]
        ]
    if op in BINARY:
        left = outputs(node["left"], table_columns)
        return left + outputs(node["right"], table_columns) if op in ("cross", "join") else left
    child = outputs(node["child"], table_columns)
    if op == "rename":
        return [f"{node['to']}.{col.rsplit('.', 1)[-1]}" for col in child]
    return child


def rewrite(logical, table_columns, disabled=()):
    def names(node):
        return outputs(node, table_columns)

    def select(node, expr):
        return {"op": "select", "condition": render(expr), "child": node}

    def push(node, expr):
        op = node["op"]
        refs = references(expr)
        if op in ("join", "cross"):
            left, right = (set(names(node[key])) for key in ("left", "right"))
            if 2 not in disabled and refs:
                for key, columns in (("left", left), ("right", right)):
                    if refs <= columns:
                        return {**node, key: push(node[key], expr)}
            if (3 not in disabled and op == "cross" and expr.kind == "Eq"
                    and refs & left and refs & right and refs <= left | right):
                return {**node, "op": "join", "condition": render(expr)}
        if 4 not in disabled and op == "rename":
            mapped = parse_condition(render(expr, dict(zip(names(node), names(node["child"])))))
            return {**node, "child": push(node["child"], mapped)}
        if 4 not in disabled and op in ("union", "union_all"):
            mapping = dict(zip(names(node["left"]), names(node["right"])))
            return {**node, "left": push(node["left"], expr),
                    "right": push(node["right"], parse_condition(render(expr, mapping)))}
        if 5 not in disabled and op == "group_by" and node["keys"] and refs <= set(node["keys"]):
            return {**node, "child": push(node["child"], expr)}
        if op == "select" and all(rule not in disabled for rule in (1, 2)):
            return {**node, "child": push(node["child"], expr)}
        return select(node, expr)

    def walk(node):
        node = {**node, **{key: walk(node[key]) for key in ("child", "left", "right") if key in node}}
        if node["op"] == "join" and {1, 2}.isdisjoint(disabled):
            remaining = []
            for clause in split(parse_condition(node["condition"])):
                refs = references(clause)
                for key in ("left", "right"):
                    if refs and refs <= set(names(node[key])):
                        node[key] = push(node[key], clause)
                        break
                else:
                    remaining.append(render(clause))
            if remaining:
                node["condition"] = " and ".join(f"({part})" for part in remaining)
            else:
                node["op"] = "cross"
                del node["condition"]
        if node["op"] == "select":
            expr = parse_condition(node["condition"])
            child = node["child"]
            for clause in split(expr) if 1 not in disabled else [expr]:
                child = push(child, clause)
            return child
        return node

    def prune(node, required):
        op = node["op"]
        if op == "project":
            pairs = [(c, a) for c, a in zip(node["columns"], names(node)) if a in required]
            if not pairs:
                pairs = list(zip(node["columns"], names(node)))[:1]
            columns, aliases = map(list, zip(*pairs))
            return {**node, "columns": columns, "aliases": aliases,
                    "child": prune(node["child"], set(columns))}
        if op in ("join", "cross"):
            needed = required | (references(parse_condition(node["condition"])) if op == "join" else set())
            return {**node, **{key: prune(node[key], needed & set(names(node[key])))
                               for key in ("left", "right")}}
        if op in ("select", "sort", "rename", "limit"):
            needed = required
            if op == "select":
                needed = needed | references(parse_condition(node["condition"]))
            if op == "sort":
                needed = needed | {key for key, _ in node["keys"]}
            if op == "rename":
                needed = {before for after, before in zip(names(node), names(node["child"])) if after in needed}
            node = {**node, "child": prune(node["child"], needed)}
        keep = [name for name in names(node) if name in required]
        if keep and keep != names(node):
            return {"op": "project", "columns": keep, "child": node}
        return node

    def clean(node):
        node = {**node, **{key: clean(node[key]) for key in ("child", "left", "right") if key in node}}
        if node["op"] == "project":
            if node["columns"] == names(node) == names(node["child"]):
                return node["child"]
        if node["op"] == "select" and node["child"]["op"] == "select":
            child = node["child"]
            return {**node, "child": child["child"],
                    "condition": f"({child['condition']}) and ({node['condition']})"}
        return node

    result = walk(logical)
    if 6 not in disabled:
        result = prune(result, set(names(result)))
    return clean(result) if 7 not in disabled else result
