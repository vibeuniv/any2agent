"""Grades an EvalTrace against its EvalTask. Deterministic checks first (cheap,
reproducible), LLM judge last and only as an advisory signal. A task with no
deterministic signal AND no usable judge is `ungraded` — excluded from the rate
denominator rather than silently passed or failed.

success = every deterministic check passed AND (no judge OR judge passed).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..spec import ToolSet
from ..adapters.base import Adapter
from ..core import registry
from . import budget
from .model import EvalTask, EvalTrace, EvalResult

CHECK_TYPES = ("tool_called", "state", "answer_contains", "no_errors", "judge")


def grade(task: EvalTask, trace: EvalTrace, toolset: ToolSet, adapter: Optional[Adapter],
          model_id: Optional[str] = None, verify_ctx: Optional[Dict[str, Any]] = None,
          judge_model: Optional[str] = None) -> EvalResult:
    r = EvalResult(task_id=task.id, success=False)
    called = [s["tool"] for s in trace.steps]
    called_set = set(called)
    expected_union = {n for path in task.expected_tools for n in path}
    r.metrics = {
        "tool_calls": len(called),
        "wrong_tool_calls": len(called_set - expected_union) if expected_union else 0,
        "errors": sum(1 for s in trace.steps if not s["ok"]),
        "rounds": trace.rounds,
        "called": sorted(called_set),
        # 4xx arg failures — the eval repair feeds these to synth_params
        "bad_calls": [{"tool": s["tool"], "status": s.get("status"),
                       "args": json.dumps(s.get("args") or {}, ensure_ascii=False)[:300]}
                      for s in trace.steps
                      if not s["ok"] and s.get("status") in (400, 422)],
    }

    # 1) runner-level failure (LLM/infra/budget) — not the agent's fault, but not a pass
    if trace.error:
        r.reasons.append("runner: %s" % trace.error)
        return r
    # 2) a read task reached for a write tool
    if trace.write_blocked:
        r.reasons.append("attempted write tool: %s" % trace.write_blocked)
        return r

    ok = True
    # 3) expected tool paths (OR-of-AND)
    if task.expected_tools:
        if not any(set(path) <= called_set for path in task.expected_tools):
            ok = False
            r.reasons.append("expected tools not covered (called: %s)"
                             % (",".join(sorted(called_set)) or "none"))

    # 4) deterministic checks
    judge_rubrics: List[str] = []
    det = [c for c in task.checks if c.get("type") != "judge"]
    judge_rubrics = [c.get("rubric", "") for c in task.checks if c.get("type") == "judge"]
    r.checks_total = len(det)
    for c in det:
        passed, why = _check(c, task, trace, called_set, toolset, adapter, verify_ctx)
        if passed:
            r.checks_passed += 1
        else:
            ok = False
            r.reasons.append(why)

    # 5) judge — required when asked for, or when there's no deterministic signal
    need_judge = bool(judge_rubrics) or (not det and not task.expected_tools)
    if need_judge:
        r.judge = _judge(task, trace, judge_rubrics, judge_model or model_id)
        if r.judge is None:
            if not det and not task.expected_tools:
                r.ungraded = True
                r.reasons.append("ungraded: no deterministic checks and judge unavailable")
                return r
            # judge unavailable but deterministic signal exists — grade on that alone
        elif not r.judge.get("pass"):
            ok = False
            r.reasons.append("judge: %s" % r.judge.get("reason", "failed"))

    r.success = ok
    return r


def _check(c: Dict[str, Any], task: EvalTask, trace: EvalTrace, called_set,
           toolset: ToolSet, adapter: Optional[Adapter],
           verify_ctx: Optional[Dict[str, Any]]) -> tuple:
    t = c.get("type")
    if t == "tool_called":
        names = c.get("any_of") or []
        hit = any(n in called_set for n in names)
        return hit, "" if hit else "tool_called failed: none of %s called" % ",".join(names)
    if t == "no_errors":
        bad = [s for s in trace.steps if not s["ok"]]
        return (not bad), "" if not bad else "no_errors failed: %s" % ",".join(
            "%s(%s)" % (s["tool"], s.get("error") or s.get("status")) for s in bad[:3])
    if t == "answer_contains":
        vals = c.get("any_of") or ([c["value"]] if c.get("value") else [])
        low = trace.answer.casefold()
        hit = any(str(v).casefold() in low for v in vals)
        return hit, "" if hit else "answer_contains failed: %s" % ",".join(map(str, vals))
    if t == "state":
        if adapter is None:
            return False, "state check needs a live adapter"
        spec = toolset.by_name().get(c.get("tool", ""))
        if spec is None:
            return False, "state check: unknown tool %s" % c.get("tool")
        res = adapter.call(spec, c.get("args") or {}, dict(verify_ctx or {}))
        blob = json.dumps(res, ensure_ascii=False, default=str)
        hit = str(c.get("expect_contains", "")) in blob
        return hit, "" if hit else "state check failed: %r not in %s response" % (
            c.get("expect_contains"), spec.name)
    return False, "unknown check type: %s" % t


_JUDGE_PROMPT = """Judge whether an AI agent completed the user's task. Output ONLY JSON:
{"pass": true|false, "reason": "<one sentence>"}

Rubric: %s

User task: %s

Agent's tool calls (name, ok, http status):
%s

Agent's final answer:
%s"""

_DEFAULT_RUBRIC = ("The agent actually completed the request, the answer is grounded in "
                   "the tool results, and it does not claim to have done things it didn't do.")


def _judge(task: EvalTask, trace: EvalTrace, rubrics: List[str],
           model_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    entry, model_string, _ = registry.resolve(model_id)
    if not entry:
        return None
    steps = "\n".join("- %s ok=%s status=%s" % (s["tool"], s["ok"], s.get("status"))
                      for s in trace.steps[:20]) or "(no tool calls)"
    prompt = _JUDGE_PROMPT % ("; ".join(r for r in rubrics if r) or _DEFAULT_RUBRIC,
                              task.prompt, steps, trace.answer[:2000])
    for _ in range(2):  # one retry on parse failure
        if not budget.spend():
            return None
        try:
            resp = registry.completion(model_string, [{"role": "user", "content": prompt}],
                                       tools=None, stream=False,
                                       extra=registry.completion_kwargs(entry))
            txt = (resp.choices[0].message.content or "").strip()
        except Exception:
            return None
        i, j = txt.find("{"), txt.rfind("}")
        if i >= 0 and j >= 0:
            try:
                d = json.loads(txt[i:j + 1])
                if isinstance(d, dict) and "pass" in d:
                    return {"pass": bool(d["pass"]), "reason": str(d.get("reason", ""))[:300]}
            except Exception:
                pass
    return None
