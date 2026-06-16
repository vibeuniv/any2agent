"""Verification critics for the connect loop. Each returns a structured result so
the repair loop can act on specific gaps, and so the final report is honest about
what was/wasn't verified (no silent "done").

  coverage  : every ground-truth route maps to a tool (static; needs route list)
  accuracy  : each tool is structurally sound (method + path + params object)
  liveness  : read tools actually return 2xx against the live target (needs base_url
              + consent; write/danger are never auto-called)
  agent_e2e : the LLM selects a plausible tool for representative probes (needs key)
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .spec import ToolSet, ToolSpec
from .adapters.base import Adapter
from .core import registry, toolrag


def coverage(toolset: ToolSet, routes: List[Dict[str, str]]) -> Dict[str, Any]:
    have = {(t.backing.get("method", "").upper(), t.backing.get("path", "")) for t in toolset.tools}
    missing = [r for r in routes if (r["method"].upper(), r["path"]) not in have]
    total = len(routes)
    covered = total - len(missing)
    pct = 1.0 if total == 0 else covered / total
    return {"name": "coverage", "passed": len(missing) == 0,
            "total": total, "covered": covered, "pct": round(pct, 3), "missing": missing}


import re as _re
_PATHVAR = _re.compile(r"\{([^}/]+)\}")


def accuracy(toolset: ToolSpec | ToolSet) -> Dict[str, Any]:
    """Structural soundness + parameter completeness. A tool is 'bad' (repairable)
    when: no method/path, non-object params, a path template var missing from
    params, or a body method (POST/PUT/PATCH) with an entirely empty schema."""
    # hard = structural error that MUST be fixed (gates fail); warn = best-effort
    # gap that can't be confirmed statically (e.g. a POST whose body schema lives in
    # runtime code, or a genuinely body-less POST). Warns are reported honestly but
    # do NOT fail the gate — LLM repair fills them when a key is available.
    bad, warn = [], []
    for t in toolset.tools:
        props = (t.parameters or {}).get("properties") if isinstance(t.parameters, dict) else None
        if not t.backing.get("path") or not t.backing.get("method"):
            bad.append({"name": t.name, "why": "missing method/path"}); continue
        if not isinstance(t.parameters, dict) or t.parameters.get("type") != "object":
            bad.append({"name": t.name, "why": "params not object-schema"}); continue
        path_vars = set(_PATHVAR.findall(t.backing["path"]))
        miss = [v for v in path_vars if v not in (props or {})]
        if miss:
            bad.append({"name": t.name, "why": "path params missing: %s" % ",".join(miss)}); continue
        if t.backing["method"].upper() in ("POST", "PUT", "PATCH") and not (props or {}):
            warn.append({"name": t.name, "why": "body method with empty params"})
    return {"name": "accuracy", "passed": not bad, "bad": bad, "warn": warn,
            "checked": len(toolset.tools)}


def liveness(toolset: ToolSet, adapter: Adapter, sample: int = 8,
             ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Smoke-call READ tools only (safe). Tools with required path params we can't
    fill are skipped (reported as 'unprobed'), not failed. ctx carries the user's
    session (passthrough) so probes run under the user's role."""
    results = []
    probed = 0
    for t in toolset.tools:
        if probed >= sample:
            break
        if t.write or t.danger:
            continue
        required = (t.parameters or {}).get("required") or []
        if required:  # can't safely synthesize required args
            results.append({"name": t.name, "status": "unprobed", "reason": "required args"})
            continue
        probed += 1
        res = adapter.call(t, {}, ctx or {})
        code = res.get("status")
        if res.get("ok"):
            status = "ok"
        elif code in (401, 403):
            status = "authz"   # RBAC denial — correct behavior for this user's role, NOT a failure
        else:
            status = "fail"    # transport/5xx/4xx-other — a real problem
        results.append({"name": t.name, "status": status, "code": code, "error": res.get("error")})
    failed = [r for r in results if r["status"] == "fail"]
    authz = [r for r in results if r["status"] == "authz"]
    ran = [r for r in results if r["status"] in ("ok", "fail", "authz")]
    # gate: transport must work (no 'fail'); 401/403 are acceptable (role-scoped)
    return {"name": "liveness", "passed": (len(ran) > 0 and not failed),
            "ran": len(ran), "ok": sum(1 for r in results if r["status"] == "ok"),
            "authz": len(authz), "failed": failed, "results": results,
            "note": ("no read tool was probable" if not ran else
                     ("%d개가 권한범위 밖(401/403) — 사용자 롤 정상 동작" % len(authz)) if authz else "")}


def agent_e2e(toolset: ToolSet, probes: List[str], model_id: Optional[str] = None,
              threshold: float = 0.9) -> Dict[str, Any]:
    """For each probe question, ask the LLM (with the tools) to make a tool call and
    check it selected a known tool. Needs a provider key; otherwise skipped."""
    entry, model_string, _ = registry.resolve(model_id)
    if not entry:
        return {"name": "agent_e2e", "passed": None, "skipped": "no provider key", "cases": []}
    names = set(toolset.by_name().keys())
    payload = [t.to_function() for t in toolrag.build_seed(toolset.tools)]
    payload.append(toolrag.SEARCH_TOOLS_DEF)
    cases = []
    for q in probes:
        picked = None
        try:
            resp = registry.completion(
                model_string,
                [{"role": "system", "content": "Use a tool to answer; do not ask back."},
                 {"role": "user", "content": q}],
                tools=payload, stream=False, extra=registry.completion_kwargs(entry),
            )
            msg = resp.choices[0].message
            for tc in (getattr(msg, "tool_calls", None) or []):
                picked = tc.function.name
                break
        except Exception as e:
            cases.append({"probe": q, "picked": None, "error": str(e)[:120]})
            continue
        ok = picked in names or picked == toolrag.SEARCH_TOOLS_NAME
        cases.append({"probe": q, "picked": picked, "ok": ok})
    rated = [c for c in cases if "ok" in c]
    rate = (sum(1 for c in rated if c["ok"]) / len(rated)) if rated else 0.0
    passed = bool(rated) and rate >= threshold
    return {"name": "agent_e2e", "passed": passed, "cases": cases,
            "rate": round(rate, 3), "threshold": threshold,
            "missed": [c["probe"] for c in rated if not c["ok"]]}


# ── CTO exit-threshold defaults (decisive criteria) ──
THRESHOLDS = {"coverage_pct": 1.0, "accuracy_bad": 0, "liveness_fail": 0, "e2e_rate": 0.9}


def run_all(toolset: ToolSet, routes, adapter: Optional[Adapter], probes,
            live: bool, model_id: Optional[str] = None,
            verify_ctx: Optional[Dict[str, Any]] = None,
            thresholds: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    th = {**THRESHOLDS, **(thresholds or {})}
    reports = [coverage(toolset, routes), accuracy(toolset)]
    if live and adapter is not None:
        reports.append(liveness(toolset, adapter, ctx=verify_ctx))
        reports.append(agent_e2e(toolset, probes, model_id, threshold=th["e2e_rate"]))
    # passed=None (skipped) does not fail the gate
    gate = all(r.get("passed") in (True, None) for r in reports)
    return {"passed": gate, "reports": reports, "thresholds": th}
