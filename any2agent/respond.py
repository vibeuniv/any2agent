"""LLM-facing tool-response rendering — token efficiency and actionable errors,
per the "writing tools for agents" guidance.

Before this layer, tool results reached the model as raw JSON with a blunt
6000-char slice that could cut mid-structure. render() guarantees the model
always sees VALID JSON: lists are truncated item-by-item (with an explicit
"_meta" note steering toward filters/limit), long strings get a marker, and on
overflow the item budget halves until it fits. When the truncated list's tool
exposes a paging param, the hint names it concretely (…pass offset=10…) instead
of the generic "refine" nudge. A render-time `fields` control projects each list
item down to the keys the model asked for. Errors gain a deterministic `hint`
telling the agent what to do next (404 on notes_get suggests notes_list —
derived from the shaped resource_action naming, never guessed).

Scope invariant: this shapes ONLY the message fed back to the LLM
(core/agent._tool_msg). Adapter results, SSE events, eval traces, and grader
state checks all keep seeing the raw data.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .spec import ToolSpec, ToolSet

_MODES = {"concise": 10, "detailed": 50}
_MAX_STR = 500
_TRUNC_MARK = "…[truncated]"

# paging params we know how to steer toward. First match on the tool's schema
# wins (this order). offset/skip are numeric (name the concrete next offset);
# page/cursor/after are token-style (point at the response's own next token,
# since we can't compute their next value from the item count alone).
_PAGING_PARAMS = ("offset", "page", "cursor", "after", "skip")
_TOKEN_STYLE = {"page", "cursor", "after"}


# ── success-path shaping ─────────────────────────────────────────────────────

def shape(data: Any, mode: str = "concise",
          max_items: Optional[int] = None) -> Tuple[Any, List[str], List[Dict[str, int]]]:
    """Structure-aware trim. Returns (shaped, notes, truncations) where
    truncations = [{"shown","total"}] per truncated list. concise also drops
    null/empty fields; detailed keeps every field (IDs stay available for
    follow-up calls). Both modes bound list length and mark over-long string
    values with a truncation marker."""
    limit = max_items if max_items is not None else _MODES.get(mode, 10)
    notes: List[str] = []
    trunc: List[Dict[str, int]] = []

    def walk(v: Any) -> Any:
        if isinstance(v, list):
            out = [walk(x) for x in v[:limit]]
            if len(v) > limit:
                trunc.append({"shown": limit, "total": len(v)})
                notes.append("list truncated to %d of %d items — refine with "
                             "filters or a smaller limit" % (limit, len(v)))
            return out
        if isinstance(v, dict):
            out = {}
            for k, val in v.items():
                w = walk(val)
                if mode == "concise" and (w is None or w == "" or w == [] or w == {}):
                    continue
                out[k] = w
            return out
        if isinstance(v, str) and len(v) > _MAX_STR:
            notes.append("a long text field was shortened")
            return v[:_MAX_STR] + _TRUNC_MARK
        return v

    shaped = walk(data)
    return shaped, notes, trunc


# ── error-path hints ─────────────────────────────────────────────────────────

def _sibling_reader(spec: Optional[ToolSpec], toolset: Optional[ToolSet]) -> str:
    """For a shaped name like notes_get, find notes_list / notes_search.
    Deterministic: prefix match only — unshaped (mechanical) names never fire."""
    if spec is None or toolset is None or "_" not in spec.name:
        return ""
    prefix = spec.name.rsplit("_", 1)[0]
    names = {t.name for t in toolset.tools}
    for suffix in ("list", "search"):
        cand = "%s_%s" % (prefix, suffix)
        if cand in names and cand != spec.name:
            return " Call %s first to find a valid id." % cand
    return ""


def explain(result: Dict[str, Any], spec: Optional[ToolSpec] = None,
            toolset: Optional[ToolSet] = None) -> str:
    """One deterministic, actionable sentence for a failed call."""
    # composite failures carry the signal in `error` (+failed_tool), not status
    if result.get("composite"):
        return _explain_composite(result, toolset)
    status = result.get("status")
    if status in (400, 422):
        detail = ""
        body = result.get("data")
        if body:
            detail = " Server detail: " + json.dumps(body, ensure_ascii=False, default=str)[:400]
        return ("The arguments were rejected — re-check required parameters and "
                "types against this tool's schema." + detail)
    if status in (401, 403):
        return ("Not permitted for this user's session (RBAC). Do not retry with "
                "different arguments; tell the user instead.")
    if status == 404:
        return ("Resource not found — the identifier may be wrong or stale."
                + _sibling_reader(spec, toolset))
    if status == 405:
        return "Method not allowed — this operation may not exist on the target; try a different tool."
    if status == 429:
        return "Rate limited — wait before retrying, and prefer narrower queries."
    if isinstance(status, int) and status >= 500:
        return ("The target API failed internally — retry once; if it persists, "
                "report the failure to the user.")
    if status is None and result.get("error"):
        err = str(result["error"])
        if err == "unknown_tool":
            return "No such tool — pick one from the provided tool list, or call search_tools."
        if "_" in err and " " not in err:
            return ""  # other local error codes (snake_case): no transport hint
        return ("Could not reach the target API — it may be down or the base URL "
                "wrong. Do not retry repeatedly.")
    return ""


def _explain_composite(result: Dict[str, Any], toolset: Optional[ToolSet]) -> str:
    """Composite-internal failures: name the failing step, and when it was an
    HTTP error reuse the status table with the FAILING tool's spec so hints
    (incl. the 404 sibling suggestion) still apply."""
    err = str(result.get("error") or "")
    tool = result.get("failed_tool") or "?"
    step = result.get("failed_step")
    where = "step %s (%s)" % (step, tool) if step is not None else tool
    if err.startswith("binding_error"):
        return ("Composite %s: an input binding could not be resolved from earlier "
                "results — likely an empty list or a missing field. Try the steps "
                "individually, or narrow the request." % where)
    if err.startswith("unknown_tool"):
        return "Composite %s references a tool that no longer exists — re-run compose." % where
    if "nested composites" in err or "requires a toolset" in err:
        return "Composite configuration error at %s — report this; do not retry." % where
    m = re.match(r"^http_(\d{3})$", err)
    if m:
        failing = (toolset.by_name().get(tool) if toolset else None)
        inner = explain({"ok": False, "status": int(m.group(1))}, failing, toolset)
        return ("Composite %s failed: %s" % (where, inner)) if inner else ""
    return ""


# ── success-path steering: pagination + field projection ─────────────────────

def _paging_param(spec: Optional[ToolSpec]) -> str:
    """First paging param the tool's schema exposes (deterministic, order per
    _PAGING_PARAMS), or "" if none. Only real schema params fire — no guessing."""
    if spec is None:
        return ""
    props = (spec.parameters or {}).get("properties", {})
    for name in _PAGING_PARAMS:
        if name in props:
            return name
    return ""


def _steer_paging(notes: List[str], trunc: List[Dict[str, int]],
                  spec: Optional[ToolSpec]) -> List[str]:
    """When exactly one list was truncated and the tool exposes a paging param,
    rewrite the generic "refine with filters" tail into a concrete next-page
    instruction naming that param. No paging param -> notes unchanged."""
    param = _paging_param(spec)
    if not param or len(trunc) != 1:
        return notes
    shown = trunc[0].get("shown", 0)
    if param in _TOKEN_STYLE:
        tail = "refine with filters, or use the %s/next token from the response for the next page" % param
    else:
        tail = "refine with filters, or pass %s=%d for the next page" % (param, shown)
    out = []
    for n in notes:
        if n.startswith("list truncated"):
            out.append("%s — %s" % (n.split(" — ", 1)[0], tail))
        else:
            out.append(n)
    return out


def _project(data: Any, keys: List[str]) -> Tuple[Any, bool]:
    """Reduce every dict item inside any list of `data` to `keys` (+ always "id"
    when present). Non-dict items pass through untouched; unknown keys are simply
    absent. Walks wrapper dicts down to the list; the list itself may be bare.
    Returns (projected, touched) — touched is False when there was no list of
    dicts to project, so the caller can skip a misleading "projected" note."""
    keep = set(keys)
    touched = False

    def walk(v: Any) -> Any:
        nonlocal touched
        if isinstance(v, list):
            out = []
            for x in v:
                if isinstance(x, dict):
                    touched = True
                    out.append({k: val for k, val in x.items() if k in keep or k == "id"})
                else:
                    out.append(x)
            return out
        if isinstance(v, dict):
            return {k: walk(val) for k, val in v.items()}
        return v

    return walk(data), touched


# ── the LLM message ──────────────────────────────────────────────────────────

def render(result: Dict[str, Any], spec: Optional[ToolSpec] = None,
           toolset: Optional[ToolSet] = None, response_format: Optional[str] = None,
           fields: Optional[str] = None, cap: int = 6000) -> str:
    """Serialize a tool result for the model: always valid JSON, always ≤ cap.
    On overflow the list budget halves until it fits; as a last resort the data
    is omitted with an explicit _meta note (never a mid-structure slice).

    `response_format` and `fields` are OURS (render-time controls), never backend
    args: the agent loop pops them off the tool args before dispatch and passes
    them here. response_format picks the trim budget; fields projects every list
    item down to the named comma-separated keys (id always kept) for the model's
    reading — a defensive complement to the server-side `fields` projection that
    response_format; the leader must add the matching `fields` pop/forward in
    core/agent.py (mirror the `fmt = args.pop("response_format", ...)` line) after
    merge — until then `fields` is only reachable by direct callers/tests.

    When a truncated list's tool exposes a paging param (offset/page/cursor/
    after/skip), the truncation hint names it concretely so the model can page
    rather than only "refine" (see _steer_paging)."""
    out = dict(result)
    mode = response_format if response_format in _MODES else "concise"
    proj_keys = [f.strip() for f in fields.split(",")] if fields else []
    proj_keys = [k for k in proj_keys if k]

    if not out.get("ok", True):
        hint = explain(out, spec, toolset)
        if hint:
            out["hint"] = hint
        # error bodies can be huge too — bound them the same way (incl. a
        # composite's failing-step diagnostic data)
        if "data" in out:
            out["data"], _, _ = shape(out["data"], mode="concise")
        if isinstance(out.get("steps"), list):
            out["steps"] = [
                dict(s, data=shape(s["data"], mode="concise")[0]) if isinstance(s, dict) and "data" in s else s
                for s in out["steps"]]
        return _fit(out, cap)

    limit = _MODES[mode]
    while True:
        shaped, notes, trunc = shape(out.get("data"), mode=mode, max_items=limit)
        notes = _steer_paging(notes, trunc, spec)
        if proj_keys:
            shaped, projected = _project(shaped, proj_keys)
            if projected:
                notes = notes + ["items projected to fields: %s" % ", ".join(proj_keys)]
        candidate = dict(out)
        if notes:
            meta: Dict[str, Any] = {"hint": "; ".join(sorted(set(notes)))}
            if trunc:
                meta["truncated"] = trunc[0] if len(trunc) == 1 else trunc
            if isinstance(shaped, list):
                candidate["data"] = {"items": shaped, "_meta": meta}
            elif isinstance(shaped, dict):
                shaped = dict(shaped)
                shaped["_meta"] = meta
                candidate["data"] = shaped
            else:
                candidate["data"] = shaped
        else:
            candidate["data"] = shaped
        txt = json.dumps(candidate, ensure_ascii=False, default=str)
        if len(txt) <= cap:
            return txt
        if limit <= 1:
            candidate["data"] = {"_meta": {"omitted": True,
                                           "hint": "result too large to show — use filters/limit "
                                                   "or request specific items"}}
            return _fit(candidate, cap)  # final guard: bulk may live outside data (e.g. steps)
        limit = max(1, limit // 2)


def _fit(obj: Dict[str, Any], cap: int) -> str:
    """Last-resort cap enforcement — progressively drop the heavy parts while
    staying valid JSON: first any step data, then the data body itself."""
    txt = json.dumps(obj, ensure_ascii=False, default=str)
    if len(txt) <= cap:
        return txt
    slim = dict(obj)
    if isinstance(slim.get("steps"), list):
        slim["steps"] = [{k: v for k, v in s.items() if k != "data"} if isinstance(s, dict) else s
                         for s in slim["steps"]]
        txt = json.dumps(slim, ensure_ascii=False, default=str)
        if len(txt) <= cap:
            return txt
    slim["data"] = {"_meta": {"omitted": True, "hint": "body too large to show"}}
    return json.dumps(slim, ensure_ascii=False, default=str)
