"""Deterministic tool shaping — the pass between scan and verify that turns
mechanical 1-route-=-1-tool output into agent-friendly tools, per Anthropic's
"writing tools for agents" guidance:

  rename    <method>_<path> mush (get__notes) -> resource_action (notes_list),
            so related tools group under a resource prefix and the intent is in
            the name. Old names are kept as aliases — existing toolspecs, eval
            tasks, and lessons keep resolving.
  promote   collection reads get a `limit` param and a "prefer filters over
            fetching everything" nudge — agents pay for context; a full table
            dump is the expensive default this steers away from.

Conservative by design: anything that doesn't match the mechanical naming
pattern (e.g. a curated OpenAPI operationId, or a human-edited name) or would
collide is left untouched and reported in `skipped` — never silently mangled.
Idempotent via meta.shaping.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from .spec import ToolSet, ToolSpec

SHAPING_VERSION = 1

# only names produced by the mechanical scanners are eligible for renaming
_MECHANICAL = re.compile(r"^(get|post|put|patch|delete|head|options)_")
_PATHVAR = re.compile(r"^\{[^}]+\}$")
_CLEAN = re.compile(r"[^a-z0-9_]+")

_PROMOTE_NOTE = " Prefer filters/limit over fetching everything — results can be large."
_LIMIT_PARAM = {"type": "integer",
                "description": "Max items to return (default 20). Use the smallest limit that answers the question."}


def _segments(path: str) -> List[str]:
    return [s for s in (path or "").split("/") if s]


def _resource(path: str) -> str:
    """Join the static segments: /users/{id}/posts -> users_posts."""
    statics = [s for s in _segments(path) if not _PATHVAR.match(s)]
    res = "_".join(_CLEAN.sub("_", s.lower()).strip("_") for s in statics)
    return res.strip("_")


def _action(method: str, path: str) -> str:
    segs = _segments(path)
    ends_var = bool(segs) and bool(_PATHVAR.match(segs[-1]))
    m = method.upper()
    if m in ("GET", "HEAD"):
        if ends_var:
            return "get"
        # a bare single static segment (e.g. /health) is a singleton read, not a collection
        return "get" if len(segs) == 1 and not _has_collection_shape(path) else "list"
    if m == "POST":
        return "update" if ends_var else "create"   # POST on an item = RPC-ish mutation
    if m == "PUT":
        return "replace"
    if m == "PATCH":
        return "update"
    if m == "DELETE":
        return "delete"
    return m.lower()


def _has_collection_shape(path: str) -> bool:
    """A resource is collection-shaped when a sibling item route ({var}) is
    plausible — heuristically: plural-looking last static segment."""
    statics = [s for s in _segments(path) if not _PATHVAR.match(s)]
    return bool(statics) and statics[-1].lower().endswith("s")


def is_list_tool(t: ToolSpec) -> bool:
    return (not t.write and not t.danger
            and _action(t.backing.get("method", "GET"), t.backing.get("path", "")) == "list")


def _proposed_name(t: ToolSpec) -> str:
    res = _resource(t.backing.get("path", ""))
    if not res:
        return ""
    return ("%s_%s" % (res, _action(t.backing.get("method", "GET"),
                                    t.backing.get("path", ""))))[:60]


def apply(toolset: ToolSet) -> Dict[str, Any]:
    """Rename + promote in place. Returns {"renamed", "promoted", "skipped"}."""
    meta_sh = (toolset.meta or {}).get("shaping") or {}
    if meta_sh.get("version", 0) >= SHAPING_VERSION:
        return {"renamed": 0, "promoted": 0, "skipped": [], "noop": True}

    skipped: List[Dict[str, str]] = []
    renamed: Dict[str, str] = {}   # new -> old

    # pass 1: propose names; resolve collisions before committing anything
    taken = {t.name for t in toolset.tools}
    proposals: List[Tuple[ToolSpec, str]] = []
    for t in toolset.tools:
        if not _MECHANICAL.match(t.name):
            skipped.append({"name": t.name, "why": "not a mechanical name (curated?) — kept"})
            continue
        new = _proposed_name(t)
        if not new:
            skipped.append({"name": t.name, "why": "no resource in path — kept"})
            continue
        if new == t.name:
            continue
        proposals.append((t, new))

    counts: Dict[str, int] = {}
    for _, new in proposals:
        counts[new] = counts.get(new, 0) + 1
    for t, new in proposals:
        if counts[new] > 1 or new in taken:
            # disambiguate with the path-var name: notes_get_by_note_id
            segs = _segments(t.backing.get("path", ""))
            var = next((s[1:-1] for s in reversed(segs) if _PATHVAR.match(s)), "")
            alt = ("%s_by_%s" % (new, _CLEAN.sub("_", var.lower()))) if var else ""
            if alt and alt not in taken and counts.get(alt, 0) == 0:
                new = alt[:60]
            else:
                skipped.append({"name": t.name, "why": "name collision on %r — kept" % new})
                continue
        taken.discard(t.name)
        taken.add(new)
        if t.name not in t.aliases:
            t.aliases.append(t.name)
        renamed[new] = t.name
        t.name = new

    # pass 2: promote collection reads toward search-shaped usage
    promoted = 0
    for t in toolset.tools:
        if not is_list_tool(t):
            continue
        props = t.parameters.setdefault("properties", {})
        changed = False
        if "limit" not in props:
            props["limit"] = dict(_LIMIT_PARAM)
            changed = True
        if _PROMOTE_NOTE.strip() not in t.description:
            t.description = (t.description + _PROMOTE_NOTE)[:400]
            changed = True
        if changed:
            promoted += 1

    toolset.meta["shaping"] = {"version": SHAPING_VERSION, "renamed": renamed}
    return {"renamed": len(renamed), "promoted": promoted, "skipped": skipped}
