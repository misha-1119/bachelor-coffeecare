"""Loader and walker for data/diagnostic_tree.json.

Tree shape:
    categories[<cat>] = {
        label: str,
        root: <node_id>,
        nodes[<node_id>]: {
            prompt: str,
            options?: [{id, label, next? | leaf?: {complexity, summary_template}}],
            input?: "free_text",
            leaf?: {...},
        }
    }
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_DEFAULT_PATH = Path(__file__).parent.parent / "data" / "diagnostic_tree.json"


@dataclass
class Leaf:
    complexity: str
    summary_template: str

    def render_summary(self, free_input: str | None = None) -> str:
        if "{input}" in self.summary_template:
            return self.summary_template.format(input=free_input or "")
        return self.summary_template


@dataclass
class Option:
    id: str
    label: str
    next: str | None = None
    leaf: Leaf | None = None


@dataclass
class Node:
    prompt: str
    options: list[Option]
    input: str | None  # "free_text" or None
    leaf: Leaf | None  # set when node itself is terminal (free-text only categories)


@dataclass
class Category:
    key: str
    label: str
    root: str
    nodes: dict[str, Node]

    def node(self, node_id: str) -> Node:
        if node_id not in self.nodes:
            raise KeyError(f"node '{node_id}' not in category '{self.key}'")
        return self.nodes[node_id]

    def root_node(self) -> Node:
        return self.node(self.root)


class DiagnosticTree:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else _DEFAULT_PATH
        self.categories: dict[str, Category] = {}
        self._load()

    def _load(self) -> None:
        with open(self.path, encoding="utf-8") as f:
            raw = json.load(f)
        for key, cat_raw in raw.get("categories", {}).items():
            nodes: dict[str, Node] = {}
            for node_id, n_raw in cat_raw.get("nodes", {}).items():
                opts = [self._parse_option(o) for o in n_raw.get("options", [])]
                nodes[node_id] = Node(
                    prompt=n_raw["prompt"],
                    options=opts,
                    input=n_raw.get("input"),
                    leaf=self._parse_leaf(n_raw.get("leaf")),
                )
            self.categories[key] = Category(
                key=key,
                label=cat_raw["label"],
                root=cat_raw["root"],
                nodes=nodes,
            )

    @staticmethod
    def _parse_leaf(raw: dict[str, Any] | None) -> Leaf | None:
        if not raw:
            return None
        return Leaf(
            complexity=raw.get("complexity", "Середня"),
            summary_template=raw.get("summary_template", ""),
        )

    def _parse_option(self, raw: dict[str, Any]) -> Option:
        return Option(
            id=raw["id"],
            label=raw["label"],
            next=raw.get("next"),
            leaf=self._parse_leaf(raw.get("leaf")),
        )

    def category(self, key: str) -> Category:
        if key not in self.categories:
            raise KeyError(f"category '{key}' not in tree")
        return self.categories[key]

    def category_buttons(self) -> list[tuple[str, str]]:
        """List of (key, label) for dashboard. Stable insertion order."""
        return [(c.key, c.label) for c in self.categories.values()]

    def resolve_option(self, cat_key: str, node_id: str, option_id: str) -> Option:
        node = self.category(cat_key).node(node_id)
        for opt in node.options:
            if opt.id == option_id:
                return opt
        raise KeyError(f"option '{option_id}' not in {cat_key}.{node_id}")

    def collect_summary(
        self,
        cat_key: str,
        path: list[tuple[str, str]],
        free_input: str | None = None,
    ) -> str:
        """Build a human-readable symptom summary from a traversed path.

        path: list of (node_id, option_id_or_input).
        """
        cat = self.category(cat_key)
        parts: list[str] = [f"Категорія: {cat.label}"]
        for node_id, value in path:
            node = cat.node(node_id)
            if node.input == "free_text":
                parts.append(f"{node.prompt} → {value}")
                continue
            label = next((o.label for o in node.options if o.id == value), value)
            parts.append(f"{node.prompt} → {label}")
        if free_input and not any(p.endswith(f"→ {free_input}") for p in parts):
            parts.append(f"Деталі: {free_input}")
        return "\n".join(parts)


_singleton: DiagnosticTree | None = None


def get_tree() -> DiagnosticTree:
    global _singleton
    if _singleton is None:
        _singleton = DiagnosticTree()
    return _singleton
