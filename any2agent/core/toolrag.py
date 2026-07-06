"""Tool discovery. With many tools, putting every schema in the prompt is wasteful
and hurts selection accuracy. We expose a small seed plus a `search_tools` meta-
tool the model calls to discover the rest by keyword relevance. Offline and
key-free by design (keyword overlap); swap in embeddings later if desired.
"""
from __future__ import annotations

import re
from typing import Dict, List

from ..spec import ToolSpec

SEARCH_TOOLS_NAME = "search_tools"
SEARCH_TOOLS_DEF = {
    "type": "function",
    "function": {
        "name": SEARCH_TOOLS_NAME,
        "description": (
            "Find available tools by natural-language intent. Call this first when no "
            "currently-listed tool fits the user's request; it returns matching tool "
            "names/descriptions which then become callable."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What you want to do, in natural language."},
                "top_k": {"type": "integer", "description": "Max results (default 8)."},
            },
            "required": ["query"],
        },
    },
}

# Expose all tools directly (no RAG) when the set is small enough.
DIRECT_LIMIT = 30
_TOK = re.compile(r"[a-zA-Z0-9가-힣]+")


def _tokens(s: str) -> List[str]:
    return [t.lower() for t in _TOK.findall(s or "") if len(t) >= 2]


def score(query: str, spec: ToolSpec) -> float:
    q = set(_tokens(query))
    if not q:
        return 0.0
    hay = " ".join([spec.name, spec.description, spec.domain,
                    " ".join(getattr(spec, "aliases", []) or []),
                    " ".join((spec.parameters or {}).get("properties", {}).keys())])
    h = set(_tokens(hay))
    if not h:
        return 0.0
    return len(q & h) / len(q)


def search(query: str, tools: List[ToolSpec], top_k: int = 8) -> List[ToolSpec]:
    ranked = sorted(((score(query, t), t) for t in tools), key=lambda x: x[0], reverse=True)
    return [t for s, t in ranked if s > 0][:max(1, int(top_k or 8))]


def build_seed(tools: List[ToolSpec], discovered: List[str] | None = None) -> List[ToolSpec]:
    """Tools to advertise to the model this turn. Small sets: everything. Large
    sets: a domain-spread sample + anything already discovered via search_tools."""
    if len(tools) <= DIRECT_LIMIT:
        return tools
    by_name = {t.name: t for t in tools}
    seed: Dict[str, ToolSpec] = {}
    # one representative per domain to give breadth
    seen_dom = set()
    for t in tools:
        if t.domain and t.domain not in seen_dom:
            seen_dom.add(t.domain)
            seed[t.name] = t
    for n in (discovered or []):
        if n in by_name:
            seed[by_name[n]] = by_name[n] if False else by_name[n]  # noqa
            seed[n] = by_name[n]
    return list(seed.values())[:DIRECT_LIMIT]
