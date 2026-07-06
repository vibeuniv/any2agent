"""Tool specification — the portable contract between a scanned system and the
agent core. A ToolSpec describes one callable operation: its name, a natural
language description (used by the LLM to decide when to call it), a JSON-Schema
for its inputs, the backing transport call, and safety flags.

This schema is generic and system-agnostic; a scanner (e.g. scan/openapi.py)
produces a list of ToolSpec, and the core dispatches/serves them unchanged.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List


@dataclass
class ToolSpec:
    name: str
    description: str
    # JSON-Schema object describing the tool's input arguments.
    parameters: Dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    # How to actually invoke it. method = HTTP verb, path = template (e.g. /pet/{petId}).
    backing: Dict[str, Any] = field(default_factory=dict)
    write: bool = False     # mutates state -> confirm gate
    danger: bool = False    # irreversible/destructive -> stronger confirm gate
    domain: str = ""        # optional grouping label (for tool discovery)
    defaults: Dict[str, Any] = field(default_factory=dict)  # default args merged when missing
    # former names of this tool (e.g. pre-shaping "get__notes"). Resolved by
    # ToolSet.by_name() so old toolspecs/evals/lessons keep working; never
    # advertised to the LLM (to_function uses the canonical name only).
    aliases: List[str] = field(default_factory=list)

    def to_function(self) -> Dict[str, Any]:
        """OpenAI/litellm tool-calling 'function' shape."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}},
            },
        }

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ToolSpec":
        return ToolSpec(
            name=d["name"],
            description=d.get("description", ""),
            parameters=d.get("parameters") or {"type": "object", "properties": {}},
            backing=d.get("backing") or {},
            write=bool(d.get("write")),
            danger=bool(d.get("danger")),
            domain=d.get("domain", ""),
            defaults=d.get("defaults") or {},
            aliases=list(d.get("aliases") or []),
        )


class ToolSet:
    """A named collection of ToolSpec, persisted as <project>.toolspec.json."""

    def __init__(self, project: str, tools: List[ToolSpec] | None = None, meta: Dict[str, Any] | None = None):
        self.project = project
        self.tools: List[ToolSpec] = tools or []
        self.meta: Dict[str, Any] = meta or {}

    def by_name(self) -> Dict[str, ToolSpec]:
        """Canonical names first, then aliases where they don't collide — so a
        reference by an old (pre-shaping) name still resolves."""
        out = {t.name: t for t in self.tools}
        for t in self.tools:
            for a in t.aliases:
                out.setdefault(a, t)
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project": self.project,
            "meta": self.meta,
            "tools": [t.to_dict() for t in self.tools],
        }

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @staticmethod
    def load(path: str) -> "ToolSet":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return ToolSet(
            project=d.get("project", ""),
            tools=[ToolSpec.from_dict(t) for t in d.get("tools", [])],
            meta=d.get("meta", {}),
        )

    def counts(self) -> Dict[str, int]:
        return {
            "tools": len(self.tools),
            "write": sum(1 for t in self.tools if t.write),
            "danger": sum(1 for t in self.tools if t.danger),
        }
