"""LLM-assisted repair helpers: description rewriting and parameter synthesis.

Used both at `init` (enrich terse descriptions) and inside the connect verify→
repair loop (fix tools that fail accuracy / agent_e2e). Entirely optional — if no
provider key is set, everything is a no-op pass-through and the loop falls back to
deterministic repair. A module-level call budget caps token spend so the loop
can't run away (CTO LLM-BUDGET exit).
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from .spec import ToolSpec
from .core import registry

# LLM-BUDGET: hard cap on repair LLM calls per process. budget_left() drives the
# loop's LLM-budget exit; reset_budget() at the start of a connect run.
_CALL_BUDGET = 60
_calls_made = 0


def reset_budget(n: int = 60) -> None:
    global _CALL_BUDGET, _calls_made
    _CALL_BUDGET, _calls_made = n, 0


def budget_left() -> int:
    return max(0, _CALL_BUDGET - _calls_made)


def _ask(model_string, entry, prompt: str) -> str:
    global _calls_made
    if budget_left() <= 0:
        return ""
    _calls_made += 1
    try:
        resp = registry.completion(model_string, [{"role": "user", "content": prompt}],
                                   tools=None, stream=False, extra=registry.completion_kwargs(entry))
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


def _json_obj(txt: str):
    i, j = txt.find("{"), txt.rfind("}")
    if i < 0 or j < 0:
        return None
    try:
        return json.loads(txt[i:j + 1])
    except Exception:
        return None


def enrich(tools: List[ToolSpec], model_id: str | None = None, force: bool = False) -> List[ToolSpec]:
    """Rewrite terse (or, with force, all) descriptions into 'when to call this'."""
    entry, model_string, _ = registry.resolve(model_id)
    if not entry:
        return tools
    for t in tools:
        if not force and len(t.description) >= 40:
            continue
        out = _ask(model_string, entry,
                   "Rewrite this API operation as ONE concise sentence describing WHEN an "
                   "assistant should call it (user intent + key inputs), for an LLM tool selector. "
                   "Be prescriptive about the trigger, not just what it does. No fluff.\n"
                   "name: %s\nmethod/path: %s %s\ncurrent: %s\nparams: %s"
                   % (t.name, t.backing.get("method", ""), t.backing.get("path", ""),
                      t.description, list((t.parameters or {}).get("properties", {}).keys())))
        if out:
            t.description = out[:600]
    return tools


def synth_params(tool: ToolSpec, source_hint: str = "", model_id: str | None = None) -> bool:
    """Infer missing query/body parameters for a tool from its description (+optional
    source snippet). Returns True if params were added. Path params are filled
    deterministically by the caller; this targets query/body the static scan missed."""
    entry, model_string, _ = registry.resolve(model_id)
    if not entry:
        return False
    out = _ask(model_string, entry,
               "Infer the request parameters for this API operation. Output ONLY JSON: "
               '{"properties": {"<name>": {"type": "<json-type>", "description": "<short>"}}, '
               '"required": ["<name>"]}. Use path/query for GET/DELETE, body fields for POST/PUT/PATCH. '
               "If none are needed, output {\"properties\": {}}. No prose.\n"
               "method/path: %s %s\ndescription: %s\nsource:\n%s"
               % (tool.backing.get("method", ""), tool.backing.get("path", ""),
                  tool.description, (source_hint or "")[:2000]))
    d = _json_obj(out)
    if not isinstance(d, dict) or not isinstance(d.get("properties"), dict):
        return False
    props = tool.parameters.setdefault("properties", {})
    added = False
    for k, v in d["properties"].items():
        if k not in props and isinstance(v, dict):
            props[k] = {"type": v.get("type", "string"),
                        "description": str(v.get("description", ""))[:200]}
            added = True
    req = [r for r in (d.get("required") or []) if r in props]
    if req:
        tool.parameters["required"] = sorted(set((tool.parameters.get("required") or []) + req))
    return added
