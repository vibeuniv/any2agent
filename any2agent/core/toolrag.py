"""Tool discovery. With many tools, putting every schema in the prompt is wasteful
and hurts selection accuracy. We expose a small seed plus a `search_tools` meta-
tool the model calls to discover the rest by keyword relevance.

Ranking has two paths. The default is offline, key-free keyword overlap (score()).
When a provider key is present (OPENAI_API_KEY) and litellm is importable, search()
transparently upgrades to embedding similarity (text-embedding-3-small via litellm
— no new dependency, litellm is already required). Tool embeddings are built lazily
on first use and cached module-side, re-embedded only when the toolset's names or
descriptions change (content hash). ANY failure — no key, import error, network
error, malformed response — falls back silently to keyword overlap, so discovery
never breaks. The public API (score/search/build_seed) is unchanged.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Any, Dict, List, Optional

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


# ── embedding path (optional upgrade, silent fallback) ───────────────────────

_EMBED_MODEL = "text-embedding-3-small"
# module-level cache for the current toolset: {"sig": <hash>, "vecs": {name: vec}}
_emb_cache: Dict[str, Any] = {}


def _toolset_sig(tools: List[ToolSpec]) -> str:
    """Content hash over names+descriptions — changes iff the embeddable text
    changes, so we re-embed only when the toolset actually differs."""
    h = hashlib.sha256()
    for t in tools:
        h.update(t.name.encode("utf-8"))
        h.update(b"\x00")
        h.update((t.description or "").encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _embed(texts: List[str]) -> List[List[float]]:
    """Embed via litellm (imported lazily; already a required dep). Handles both
    the dict-shaped and object-shaped responses litellm may return."""
    import litellm  # lazy: keeps the keyword path import-free
    resp = litellm.embedding(model=_EMBED_MODEL, input=texts)
    data = resp.data if hasattr(resp, "data") else resp["data"]
    out: List[List[float]] = []
    for item in data:
        try:
            out.append(list(item["embedding"]))
        except (TypeError, KeyError):
            out.append(list(item.embedding))
    return out


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _embed_search(query: str, tools: List[ToolSpec], top_k: int) -> Optional[List[ToolSpec]]:
    """Embedding-ranked results, or None when the embedding path is unavailable
    (no key / no tools) so the caller uses keyword overlap instead. Raises on
    embedding failure — search() catches it and falls back."""
    if not tools or not os.getenv("OPENAI_API_KEY"):
        return None
    sig = _toolset_sig(tools)
    if _emb_cache.get("sig") != sig:
        vecs = _embed(["%s: %s" % (t.name, t.description or "") for t in tools])
        _emb_cache.clear()
        _emb_cache["sig"] = sig
        _emb_cache["vecs"] = {t.name: v for t, v in zip(tools, vecs)}
    qvec = _embed([query])[0]
    cache = _emb_cache["vecs"]
    ranked = sorted(((_cosine(qvec, cache[t.name]), t) for t in tools if t.name in cache),
                    key=lambda x: x[0], reverse=True)
    return [t for s, t in ranked if s > 0][:max(1, int(top_k or 8))]


def search(query: str, tools: List[ToolSpec], top_k: int = 8) -> List[ToolSpec]:
    """Rank tools by relevance to `query`. Embedding similarity when a key is set
    and litellm is reachable; otherwise (or on any embedding failure) keyword
    overlap. Return type/shape is identical on both paths."""
    try:
        hits = _embed_search(query, tools, top_k)
        if hits is not None:
            return hits
    except Exception:
        pass  # embedding unavailable/failed — never break discovery
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
            seed[n] = by_name[n]
    return list(seed.values())[:DIRECT_LIMIT]
