"""Relational algebra tree, JSON encoding, and text rendering."""

from __future__ import annotations

from dataclasses import dataclass
import json


@dataclass(frozen=True)
class Node:
    kind: str
    label: str
    children: tuple[Node, ...] = ()
    position: int = 0


def tree_to_json(tree: Node) -> str:
    """Encode a tree, including source positions used by diagnostics."""
    def encode(node: Node) -> dict:
        return {
            "kind": node.kind,
            "label": node.label,
            "children": [encode(child) for child in node.children],
            "position": node.position,
        }

    return json.dumps(encode(tree))


def tree_from_json(source: str) -> Node:
    """Rebuild the immutable tree encoded by tree_to_json."""
    def decode(data: object) -> Node:
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("kind"), str)
            or not isinstance(data.get("label"), str)
            or not isinstance(data.get("children"), list)
            or type(data.get("position")) is not int
            or data["position"] < 0
        ):
            raise ValueError("invalid relational algebra tree node")
        return Node(
            data["kind"], data["label"],
            tuple(decode(child) for child in data["children"]), data["position"],
        )

    return decode(json.loads(source))


def _name(node: Node) -> str:
    if node.kind == "Str":
        return f"Str({node.label!r})"
    if node.kind in ("Relation", "Attr", "Num", "Str"):
        return f"{node.kind}({node.label})"
    if node.kind == "Project":
        return f"Project(attrs=[{node.label}])"
    if node.kind == "Rename":
        return f"Rename(name={node.label})"
    return node.kind


def draw_tree(node: Node) -> str:
    """Return an ASCII rendering that does not require Unicode terminal support."""
    lines = [_name(node)]

    def visit(current: Node, prefix: str) -> None:
        for index, child in enumerate(current.children):
            last = index == len(current.children) - 1
            lines.append(prefix + ("`-- " if last else "|-- ") + _name(child))
            visit(child, prefix + ("    " if last else "|   "))

    visit(node, "")
    return "\n".join(lines)
