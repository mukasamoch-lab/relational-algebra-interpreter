"""Translate a compiler plan in written order for the unoptimized baseline."""

OPERATORS = {
    "scan": "seqscan",
    "select": "filter",
    "join": "nested_loop_join",
    "cross": "nested_loop_join",
}


def lower_plan(logical):
    op = logical["op"]
    physical = {**logical, "op": OPERATORS.get(op, op)}
    if op == "cross":
        physical["condition"] = "1 = 1"
    if op in ("group_by", "distinct"):
        physical["mode"] = "sort"
    for field in ("child", "left", "right"):
        if field in logical:
            physical[field] = lower_plan(logical[field])
    return physical
