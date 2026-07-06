"""EvalTask generation + persistence.

Preference order: (1) a curated <project>.evals.json on disk always wins —
regeneration must be explicit (--regen); (2) LLM generation from the toolspec
when a provider key is set; (3) a deterministic fallback built from read tools,
so the harness still produces tasks with no key. Every loaded/generated task is
validated against the toolset; tasks referencing unknown tools (or unsafe write
tasks) are excluded AND counted — never silently dropped.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from ..spec import ToolSet
from ..core import registry
from . import budget
from .model import EvalTask, EVAL_MARKER

_KNOWN_CHECKS = {"tool_called", "state", "answer_contains", "no_errors", "judge"}


# ── persistence ──────────────────────────────────────────────────────────────

def load(path: str) -> List[EvalTask]:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return [EvalTask.from_dict(t) for t in d.get("tasks", [])]


def save(path: str, project: str, tasks: List[EvalTask]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"project": project, "version": 1,
                   "tasks": [t.to_dict() for t in tasks]},
                  f, ensure_ascii=False, indent=2)


# ── validation (silent-drop is forbidden; callers report the invalid count) ──

def validate(tasks: List[EvalTask], toolset: ToolSet) -> Tuple[List[EvalTask], List[Dict[str, str]]]:
    """Split into (valid, invalid). Invalid = references a tool the toolset
    doesn't have, an unknown check type, a write task without the eval marker
    in its prompt, or a danger tool anywhere outside cleanup."""
    by_name = toolset.by_name()
    valid, invalid = [], []

    def _bad(t: EvalTask, why: str):
        invalid.append({"id": t.id, "why": why})

    for t in tasks:
        if not t.id or not t.prompt:
            _bad(t, "missing id/prompt"); continue
        if t.kind not in ("read", "write"):
            _bad(t, "kind must be read|write"); continue
        refs = [n for path in t.expected_tools for n in path]
        refs += [c.get("tool") for c in t.checks if c.get("type") in ("state",)]
        refs += [n for c in t.checks if c.get("type") == "tool_called" for n in (c.get("any_of") or [])]
        refs += [c.get("tool") for c in t.cleanup]
        unknown = [r for r in refs if r and r not in by_name]
        if unknown:
            _bad(t, "unknown tool(s): %s" % ",".join(sorted(set(unknown)))); continue
        bad_check = [c.get("type") for c in t.checks if c.get("type") not in _KNOWN_CHECKS]
        if bad_check:
            _bad(t, "unknown check type(s): %s" % ",".join(str(x) for x in bad_check)); continue
        # write safety: marker required; danger tools only allowed in cleanup
        if t.kind == "write" and EVAL_MARKER not in t.prompt:
            _bad(t, "write task without %s marker" % EVAL_MARKER); continue
        risky = [n for path in t.expected_tools for n in path
                 if n in by_name and by_name[n].danger]
        if risky:
            _bad(t, "danger tool(s) in expected_tools: %s" % ",".join(risky)); continue
        valid.append(t)
    return valid, invalid


# ── deterministic fallback (no provider key needed) ──────────────────────────

def generate_fallback(toolset: ToolSet, n: int = 8) -> List[EvalTask]:
    """Read-only template tasks. Two shapes: single-tool fetch+summarize, and a
    2-step list→detail pair when a read tool's path extends another's with a
    path variable. Write tasks are never generated without an LLM (safety)."""
    reads = [t for t in toolset.tools if not t.write and not t.danger
             and not (t.defaults or {}).get("_disabled")]
    out: List[EvalTask] = []

    # 2-step pairs first (multi-call tasks are what the harness is for)
    simple = [t for t in reads if "{" not in (t.backing.get("path") or "")]
    detailed = [t for t in reads if "{" in (t.backing.get("path") or "")]
    for lst in simple:
        base = (lst.backing.get("path") or "").rstrip("/")
        for det in detailed:
            if len(out) >= n:
                break
            if base and (det.backing.get("path") or "").startswith(base + "/"):
                out.append(EvalTask(
                    id="fb-pair-%d" % (len(out) + 1),
                    prompt=("First fetch the list for '%s', then pick one item from it "
                            "and fetch its detail. Summarize what you found."
                            % (lst.description or lst.name)),
                    expected_tools=[[lst.name, det.name]],
                    checks=[{"type": "tool_called", "any_of": [lst.name]},
                            {"type": "tool_called", "any_of": [det.name]}],
                ))

    # single-tool fetch tasks for remaining budget
    for t in simple:
        if len(out) >= n:
            break
        required = (t.parameters or {}).get("required") or []
        if required:            # can't synthesize required args deterministically
            continue
        if any(t.name in path for task in out for path in task.expected_tools):
            continue
        out.append(EvalTask(
            id="fb-read-%d" % (len(out) + 1),
            prompt="Fetch this information and summarize it briefly: %s"
                   % (t.description or t.name),
            expected_tools=[[t.name]],
            checks=[{"type": "tool_called", "any_of": [t.name]},
                    {"type": "no_errors"}],
        ))
    return out


# ── LLM generation ────────────────────────────────────────────────────────────

_GEN_PROMPT = """You are building an evaluation set for an AI agent that calls the tools below.
Write %d realistic USER TASKS that test whether the agent can complete real work.

Rules:
- Prefer tasks that need 2 or more tool calls (multi-step), not single lookups.
- Each task: {"id": "<slug>", "prompt": "<user request>", "kind": "read"|"write",
  "expected_tools": [["toolA","toolB"], ["altToolC"]],   // valid solution paths
  "checks": [...], "cleanup": [...]}
- Check types: {"type":"tool_called","any_of":["name"]}, {"type":"no_errors"},
  {"type":"answer_contains","value":"..."},
  {"type":"state","tool":"<read tool>","args":{...},"expect_contains":"..."},
  {"type":"judge","rubric":"..."}  (use judge sparingly; prefer deterministic checks)
- WRITE tasks: the prompt MUST tell the agent to include the literal marker "%s"
  in any created/updated content; add a "state" check that looks for that marker
  and a "cleanup" entry [{"tool":"<tool>","args":{...}}] that removes what was
  created. Never use destructive (danger) tools except in cleanup.
- Only reference tool names from the catalog. Output ONLY a JSON array. No prose.

Tool catalog:
%s"""


def generate_llm(toolset: ToolSet, n: int = 8, model_id: Optional[str] = None) -> Optional[List[EvalTask]]:
    entry, model_string, _ = registry.resolve(model_id)
    if not entry or not budget.spend():
        return None
    catalog = [{"name": t.name, "description": t.description,
                "parameters": t.parameters, "write": t.write, "danger": t.danger,
                "domain": t.domain}
               for t in toolset.tools if not (t.defaults or {}).get("_disabled")]
    try:
        resp = registry.completion(
            model_string,
            [{"role": "user", "content": _GEN_PROMPT
              % (n, EVAL_MARKER, json.dumps(catalog, ensure_ascii=False)[:12000])}],
            tools=None, stream=False, extra=registry.completion_kwargs(entry))
        txt = (resp.choices[0].message.content or "").strip()
    except Exception:
        return None
    i, j = txt.find("["), txt.rfind("]")
    if i < 0 or j < 0:
        return None
    try:
        arr = json.loads(txt[i:j + 1])
    except Exception:
        return None
    if not isinstance(arr, list):
        return None
    tasks = []
    for k, d in enumerate(arr[:n]):
        if isinstance(d, dict):
            d.setdefault("id", "gen-%d" % (k + 1))
            tasks.append(EvalTask.from_dict({**d, "source": "generated"}))
    return tasks or None


# ── entry point ───────────────────────────────────────────────────────────────

def load_or_generate(toolset: ToolSet, evals_path: str, n: int = 8, regen: bool = False,
                     model_id: Optional[str] = None) -> Tuple[List[EvalTask], List[Dict[str, str]]]:
    """Returns (valid_tasks, invalid_reports). A curated file wins unless regen;
    generated sets are persisted so the team can curate them."""
    if os.path.exists(evals_path) and not regen:
        return validate(load(evals_path), toolset)
    tasks = generate_llm(toolset, n=n, model_id=model_id) or generate_fallback(toolset, n=n)
    valid, invalid = validate(tasks, toolset)
    if valid:
        save(evals_path, toolset.project, valid)
    return valid, invalid
