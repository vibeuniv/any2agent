"""Composite (multi-step) tool executor — the FR-05 half of tool-composition.

A composite ToolSpec has no single HTTP backing; instead `backing.composite` is a
sequence of steps, each naming a constituent tool and its args. The executor runs
them in order, binding later steps' args to earlier steps' results, and returns a
single result. This lives in the dispatch layer (dispatch delegates here), NOT in
the adapter — the adapter stays a single-call transport.

Design commitments (see docs/02-design/features/tool-composition.design.md):
  - Deterministic binding syntax (no LLM at runtime): `$input.<path>` reads the
    composite's own input args; `$steps[i].<path>` reads step i's result dict
    ({ok,status,data,error}). Path = chained `.key` / `[index]` (negative ok).
  - MAX flag inheritance: a composite's effective write/danger is the OR of its
    steps' flags, computed from the live toolset (not a possibly-stale stored
    field) so the confirm gate can't be understated by a hand-edited spec.
  - Honest partial failure: on the first failing step, execution STOPS; the result
    reports which steps ran, which failed, and that nothing was rolled back. A
    composite is not a transaction and never pretends to be.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ..spec import ToolSpec


class BindingError(Exception):
    """A `$...` binding could not be resolved against the input/step results."""


def is_composite(spec: ToolSpec) -> bool:
    """Canonical check used by dispatch, the verifier, and shaping."""
    steps = (spec.backing or {}).get("composite")
    return isinstance(steps, list) and len(steps) >= 1


def steps_of(spec: ToolSpec) -> List[Dict[str, Any]]:
    return list((spec.backing or {}).get("composite") or [])


def effective_flags(spec: ToolSpec, by_name: Dict[str, ToolSpec]) -> Tuple[bool, bool]:
    """(write, danger) = OR of the constituent steps' flags — the MAX the confirm
    gate must honor. Steps that don't resolve are ignored here (validation/exec
    surface them); a valid composite never contains a danger step (FR-04)."""
    write = danger = False
    for step in steps_of(spec):
        t = by_name.get(step.get("tool", ""))
        if t is not None:
            write = write or bool(t.write)
            danger = danger or bool(t.danger)
    return write, danger


# ── binding resolution ────────────────────────────────────────────────────────

_ROOT = re.compile(r"^\$(input|steps\[(-?\d+)\])")
_SEG = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(-?\d+)\]")


def _navigate(cur: Any, key: Optional[str], idx: Optional[str], expr: str) -> Any:
    if key is not None:
        if not isinstance(cur, dict) or key not in cur:
            raise BindingError("no key %r while resolving %r" % (key, expr))
        return cur[key]
    n = int(idx)  # idx is not None here
    if not isinstance(cur, (list, tuple)):
        raise BindingError("value not indexable at [%d] while resolving %r" % (n, expr))
    try:
        return cur[n]
    except IndexError:
        raise BindingError("index %d out of range while resolving %r" % (n, expr))


def _resolve_one(expr: str, input_args: Dict[str, Any], results: List[Dict[str, Any]]) -> Any:
    m = _ROOT.match(expr)
    if not m:
        raise BindingError("binding must start with $input or $steps[i]: %r" % expr)
    if m.group(1) == "input":
        cur: Any = input_args
    else:
        i = int(m.group(2))
        try:
            cur = results[i]
        except IndexError:
            raise BindingError("step index %d out of range (only %d ran) in %r"
                               % (i, len(results), expr))
    rest, pos = expr[m.end():], 0
    while pos < len(rest):
        sm = _SEG.match(rest, pos)
        if not sm:
            raise BindingError("cannot parse binding path near %r in %r" % (rest[pos:], expr))
        cur = _navigate(cur, sm.group(1), sm.group(2), expr)
        pos = sm.end()
    return cur


def resolve_args(args: Any, input_args: Dict[str, Any], results: List[Dict[str, Any]]) -> Any:
    """Recursively resolve `$...` bindings in a step's args. Non-`$` values (and
    non-strings) pass through as literals."""
    if isinstance(args, dict):
        return {k: resolve_args(v, input_args, results) for k, v in args.items()}
    if isinstance(args, list):
        return [resolve_args(v, input_args, results) for v in args]
    if isinstance(args, str) and args.startswith("$"):
        return _resolve_one(args, input_args, results)
    return args


def _binding_syntax_error(expr: str) -> Optional[str]:
    """Parse-only check (no data): is this a well-formed binding expression?"""
    m = _ROOT.match(expr)
    if not m:
        return "binding must start with $input or $steps[i]: %r" % expr
    rest, pos = expr[m.end():], 0
    while pos < len(rest):
        sm = _SEG.match(rest, pos)
        if not sm:
            return "cannot parse binding path near %r in %r" % (rest[pos:], expr)
        pos = sm.end()
    return None


def _walk_bindings(args: Any, errors: List[str], path: str = "args") -> None:
    if isinstance(args, dict):
        for k, v in args.items():
            _walk_bindings(v, errors, "%s.%s" % (path, k))
    elif isinstance(args, list):
        for i, v in enumerate(args):
            _walk_bindings(v, errors, "%s[%d]" % (path, i))
    elif isinstance(args, str) and args.startswith("$"):
        err = _binding_syntax_error(args)
        if err:
            errors.append("%s (%s)" % (path, err))


def validate(spec: ToolSpec, by_name: Dict[str, ToolSpec]) -> Tuple[bool, str]:
    """Structural validation of a composite: >= 2 steps, each step tool resolves,
    no danger step (FR-04), no nesting, all bindings well-formed. Returns
    (ok, reason). Policy checks (name collision) live in compose.validate_composite."""
    defs = steps_of(spec)
    if len(defs) < 2:
        return False, "needs >= 2 steps"
    for i, step in enumerate(defs):
        name = step.get("tool", "")
        t = by_name.get(name)
        if t is None:
            return False, "step %d references unknown tool %r" % (i, name)
        if t.danger:
            return False, "step %d uses danger tool %r (not allowed)" % (i, name)
        if is_composite(t):
            return False, "step %d nests composite %r (not allowed)" % (i, name)
        errs: List[str] = []
        _walk_bindings(step.get("args") or {}, errs)
        if errs:
            return False, "step %d bad binding: %s" % (i, "; ".join(errs))
    return True, ""


# ── executor ───────────────────────────────────────────────────────────────────

def _report(spec: ToolSpec, records: List[Dict[str, Any]], total: int,
            failed_step: Optional[int] = None, failed_tool: str = "",
            error: Optional[str] = None,
            final_data: Any = None) -> Dict[str, Any]:
    completed = sum(1 for r in records if r["ok"])
    out: Dict[str, Any] = {
        "ok": error is None,
        "composite": spec.name,
        "steps": records,
        "completed": completed,
        "total": total,
        "error": error,
    }
    if error is None:
        # the composite's result is the LAST step's data (records stay slim)
        out["data"] = final_data
        return out
    # partial failure: be explicit that this is not a transaction
    out["failed_step"] = failed_step
    out["failed_tool"] = failed_tool
    out["rolled_back"] = False
    applied_writes = sum(1 for r in records if r["ok"] and r.get("write"))
    note = "steps that already ran are NOT rolled back"
    if applied_writes:
        note += " — %d write step(s) already applied to the target" % applied_writes
    out["note"] = note
    return out


def run(spec: ToolSpec, input_args: Dict[str, Any], adapter,
        ctx: Optional[Dict[str, Any]] = None, confirmed: bool = False,
        by_name: Optional[Dict[str, ToolSpec]] = None) -> Dict[str, Any]:
    """Run a composite's steps in order. The write/danger confirm decision has
    already been made at the composite level (dispatch), so steps execute directly
    against the adapter. Stops at the first failing step and reports honestly."""
    ctx = ctx or {}
    by_name = by_name or {}
    defs = steps_of(spec)
    results: List[Dict[str, Any]] = []   # raw adapter results, for binding
    records: List[Dict[str, Any]] = []   # honest per-step report
    total = len(defs)

    for i, step in enumerate(defs):
        name = step.get("tool", "")
        target = by_name.get(name)
        if target is None:
            return _report(spec, records, total, i, name, "unknown_tool: %s" % (name or "?"))
        if is_composite(target):
            return _report(spec, records, total, i, target.name, "nested composites are not allowed")
        try:
            call_args = resolve_args(step.get("args") or {}, input_args, results)
        except BindingError as e:
            return _report(spec, records, total, i, target.name, "binding_error: %s" % e)

        res = adapter.call(target, call_args if isinstance(call_args, dict) else {}, ctx)
        ok = bool(res.get("ok"))
        # step records deliberately omit successful intermediate `data` — that is
        # the whole point of a composite (execute server-side, return only the
        # final result; §4.2). Binding reads from `results`, not records. The
        # FAILING step keeps its data for diagnostics.
        rec = {"tool": target.name, "args": call_args, "ok": ok,
               "status": res.get("status"), "write": bool(target.write),
               "error": res.get("error")}
        if not ok:
            rec["data"] = res.get("data")
        records.append(rec)
        results.append(res)
        if not ok:
            why = res.get("error") or ("http_%s" % res.get("status") if res.get("status") else "step_failed")
            return _report(spec, records, total, i, target.name, str(why))

    return _report(spec, records, total,
                   final_data=(results[-1].get("data") if results else None))
