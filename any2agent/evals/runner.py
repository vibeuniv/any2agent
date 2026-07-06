"""Plays one EvalTask through the REAL agent runtime (core/agent.run_chat) and
records what happened. No parallel implementation of the loop — if the runtime
has a bug, the eval sees it.

Confirm policy:
  - read task: ctx has NO auto_confirm. A `confirm` event means the agent tried
    a write tool for a read request — recorded as write_blocked (a wrong-tool
    signal; the target's data stays safe, run_chat halts the turn by itself).
  - write task (write_ok only): ctx["auto_confirm"]=True lets dispatch execute
    write tools immediately; the user consented up front via --live-write.

Budget: one eval-budget unit per task run (the inner loop's own LLM calls are
bounded by run_chat's MAX_STEPS).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..spec import ToolSet
from ..adapters.base import Adapter
from ..core import agent as core_agent
from ..core import dispatch
from . import budget
from .model import EvalTask, EvalTrace


def run_task(task: EvalTask, toolset: ToolSet, adapter: Adapter,
             model_id: Optional[str] = None, verify_ctx: Optional[Dict[str, Any]] = None,
             write_ok: bool = False) -> EvalTrace:
    trace = EvalTrace(task_id=task.id)
    if not budget.spend():
        trace.error = "skipped_budget"
        return trace

    ctx: Dict[str, Any] = dict(verify_ctx or {})
    if task.kind == "write" and write_ok:
        ctx["auto_confirm"] = True

    prev = None
    for ev in core_agent.run_chat([{"role": "user", "content": task.prompt}],
                                  toolset, adapter, model_id=model_id, ctx=ctx):
        et = ev.get("type")
        if et == "delta":
            trace.answer += ev.get("text", "")
        elif et == "tool":
            res = ev.get("result") or {}
            trace.steps.append({
                "tool": ev.get("name", ""),
                "args": ev.get("args") or {},
                "ok": bool(res.get("ok", True)),   # meta-tools (search_tools) have no ok
                "status": res.get("status"),
                "error": res.get("error", ""),
            })
        elif et == "confirm":
            # only reachable without auto_confirm => a read task hit a write tool
            trace.write_blocked = ev.get("name", "")
        # rounds: best-effort — a tool batch followed by more output = one round
        if prev == "tool" and et in ("delta", "done"):
            trace.rounds += 1
        prev = et
    trace.rounds += 1  # the final (or only) round

    # infra failures surface as an apologetic delta from run_chat, not an exception
    low = trace.answer.strip()
    if not trace.steps and (low.startswith("LLM call error") or low.startswith("No LLM provider key")):
        trace.error = low[:200]
    return trace


def run_cleanup(task: EvalTask, toolset: ToolSet, adapter: Adapter,
                verify_ctx: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    """Best-effort undo of a write task's side effects (runs confirmed).
    Returns the residue list — cleanup calls that FAILED and left data behind;
    the report surfaces these honestly for manual cleanup."""
    residue = []
    by_name = toolset.by_name()
    for c in task.cleanup:
        spec = by_name.get(c.get("tool", ""))
        if not spec:
            residue.append({"task": task.id, "tool": c.get("tool", ""), "why": "unknown_tool"})
            continue
        res = dispatch.execute(spec, c.get("args") or {}, adapter,
                               ctx=dict(verify_ctx or {}), confirmed=True)
        if not res.get("ok"):
            residue.append({"task": task.id, "tool": spec.name,
                            "why": str(res.get("error", "failed"))[:120]})
    return residue
