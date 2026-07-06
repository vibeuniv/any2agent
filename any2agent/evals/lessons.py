"""Failure → lesson pipeline: classify each failed eval task into one of five
deterministic causes and turn it into a single actionable guidance line. The
same line serves two consumers:

  1. the USER — printed under "what to fix" instead of raw failure reasons
  2. the AGENT — persisted to <project>.eval-lessons.json and injected as a
     system note at serve time, so the next conversation avoids the same
     mistake (the article's "steer agents with helpful instructions")

Lessons are hints, never policy: the confirm/auth gates do not read them.
Lessons self-clean — a task that passes removes its lesson, references to
tools that no longer exist are dropped, and at most 20 are kept.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from ..spec import ToolSet

MAX_LESSONS = 20

CLASSES = ("wrong_tool", "bad_args", "tool_error", "state_mismatch", "answer_gap", "other")


def classify(result: Dict[str, Any]) -> str:
    """Deterministic cause, checked in priority order (a wrong tool usually
    explains the downstream check failures too)."""
    reasons = result.get("reasons") or []
    metrics = result.get("metrics") or {}
    txt = " | ".join(reasons)
    if "attempted write tool" in txt or "expected tools not covered" in txt:
        return "wrong_tool"
    if metrics.get("bad_calls"):
        return "bad_args"
    if "no_errors failed" in txt or metrics.get("errors"):
        return "tool_error"
    if "state check failed" in txt:
        return "state_mismatch"
    if "answer_contains failed" in txt or "judge:" in txt:
        return "answer_gap"
    return "other"


def _guidance(cls: str, result: Dict[str, Any], task) -> str:
    when = (task.prompt or "")[:120]
    m = result.get("metrics") or {}
    # join with ', ' (not bare ','): the stale-tool filter tokenizes guidance by
    # whitespace, so 'a,b' would form one unknown token and silently drop the lesson
    called = ", ".join(m.get("called") or []) or "no tools"
    expected = ", ".join(sorted({n for path in task.expected_tools for n in path})) or "the expected tools"
    if cls == "wrong_tool":
        return ("For requests like %r, use %s — the model called %s instead."
                % (when, expected, called))
    if cls == "bad_args":
        bc = (m.get("bad_calls") or [{}])[0]
        return ("Tool %s rejected the arguments (HTTP %s); match its parameter schema exactly."
                % (bc.get("tool", "?"), bc.get("status", "?")))
    if cls == "tool_error":
        return ("Calls to %s failed at runtime for %r; check the target API/auth before retrying this flow."
                % (called, when))
    if cls == "state_mismatch":
        check_tools = ",".join(c.get("tool", "?") for c in task.checks if c.get("type") == "state") or "a read tool"
        return ("After %r the expected result was missing — verify with %s before answering."
                % (when, check_tools))
    if cls == "answer_gap":
        detail = "; ".join(r for r in (result.get("reasons") or [])
                           if r.startswith(("answer_contains", "judge:")))[:160]
        return "For %r, ground the final answer in the tool results (%s)." % (when, detail or "answer unsupported")
    return "Task %r failed: %s" % (when, "; ".join(result.get("reasons") or [])[:160])


def build(rep: Dict[str, Any], tasks_by_id: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One lesson per graded failure (runner/infra failures teach nothing about
    the tool set, so they're excluded)."""
    out = []
    for r in rep.get("results", []):
        if r.get("success") or r.get("ungraded"):
            continue
        if any(x.startswith("runner:") for x in (r.get("reasons") or [])):
            continue
        task = tasks_by_id.get(r.get("task_id"))
        if task is None:
            continue
        cls = classify(r)
        out.append({"task_id": r["task_id"], "class": cls,
                    "when": (task.prompt or "")[:160],
                    "guidance": _guidance(cls, r, task)})
    return out


# ── persistence ──────────────────────────────────────────────────────────────

def load(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return [l for l in d.get("lessons", []) if isinstance(l, dict) and l.get("guidance")]
    except Exception:
        # corrupt file — start clean (next save regenerates it), but say so
        print("[lessons] ⚠ %s is corrupt — starting with no lessons" % path)
        return []


def _references_known_tools(lesson: Dict[str, Any], names: set) -> bool:
    """Drop lessons that mention tools the toolset no longer has (stale after
    a rescan/rename). Tool names are word-ish tokens; substring match on the
    guidance is enough at this size."""
    words = set(w.strip(",.;()'\"") for w in lesson.get("guidance", "").split())
    mentioned = [w for w in words if "_" in w]  # tool names are snake_case
    return all(m in names for m in mentioned) if mentioned else True


def merge_save(path: str, project: str, new_lessons: List[Dict[str, Any]],
               passed_task_ids: List[str], toolset: ToolSet) -> List[Dict[str, Any]]:
    """Load → drop lessons for now-passing tasks → upsert new (task_id wins) →
    drop stale tool references → keep the newest MAX_LESSONS → save."""
    names = set(toolset.by_name().keys())
    current = {l["task_id"]: l for l in load(path) if l.get("task_id")}
    for tid in passed_task_ids:
        current.pop(tid, None)
    for l in new_lessons:
        current[l["task_id"]] = l
    kept = [l for l in current.values() if _references_known_tools(l, names)]
    kept = kept[-MAX_LESSONS:]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"project": project, "version": 1, "lessons": kept},
                  f, ensure_ascii=False, indent=2)
    return kept


def render(lessons: List[Dict[str, Any]]) -> str:
    """System-note body injected at serve time. Hints only — never policy."""
    if not lessons:
        return ""
    return ("Operational guidance learned from evaluation runs (follow when "
            "relevant; it never overrides confirmation or authorization rules):\n"
            + "\n".join("- " + l["guidance"] for l in lessons))
