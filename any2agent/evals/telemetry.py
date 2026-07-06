"""Runtime tool-call telemetry — the runtime counterpart of the build-time eval.

Every executed tool call gets one JSONL line (tool, ok, status, ms, ts — NEVER
args, response bodies, or user identity: same discipline as memory). From the
recent window we derive per-tool error rates and flag "suspects": tools that
keep failing in live use, which usually means the target API drifted and the
toolset needs re-verification (`any2agent eval`). Suspect status is computed
from the recent window only, so a recovered tool clears itself — no sticky
flags. Recording must never break a conversation: every exception is absorbed.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

_FILENAME = "tool-calls.jsonl"
MAX_LINES = 5000   # rotation trigger
KEEP = 2500        # lines kept after rotation
WINDOW = 10        # recent calls per tool considered for drift
MIN_SAMPLE = 5     # suspect needs at least this many recent calls
SUSPECT_RATE = 0.5


def path(state_dir: str) -> str:
    return os.path.join(state_dir, _FILENAME)


def record(state_dir: str, tool: str, ok: bool, status: Optional[int] = None,
           ms: Optional[int] = None, authz: bool = False) -> None:
    """Append one call record. No-op without a state_dir; never raises."""
    if not state_dir or not tool:
        return
    try:
        os.makedirs(state_dir, exist_ok=True)
        p = path(state_dir)
        entry = {"ts": int(time.time()), "tool": tool, "ok": bool(ok)}
        if status is not None:
            entry["status"] = status
        if ms is not None:
            entry["ms"] = int(ms)
        if authz:
            entry["authz"] = True   # RBAC denial — correct behavior, not an error
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _rotate(p)
    except Exception:
        pass  # telemetry is best-effort; the conversation always wins


def _rotate(p: str) -> None:
    try:
        with open(p, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > MAX_LINES:
            with open(p, "w", encoding="utf-8") as f:
                f.writelines(lines[-KEEP:])
    except Exception:
        pass


def load(state_dir: str, n: int = 2000) -> List[Dict[str, Any]]:
    p = path(state_dir)
    if not state_dir or not os.path.exists(p):
        return []
    out = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return out[-n:]


def summary(state_dir: str, window: int = WINDOW) -> Dict[str, Any]:
    """Per-tool aggregates over the recent record window + drift suspects."""
    entries = load(state_dir)
    per: Dict[str, List[Dict[str, Any]]] = {}
    for e in entries:
        per.setdefault(e.get("tool", "?"), []).append(e)

    tools, suspects = [], []
    for tool, es in sorted(per.items()):
        # authz denials are correct behavior for the user's role — not errors
        rated = [e for e in es if not e.get("authz")]
        errors = sum(1 for e in rated if not e.get("ok"))
        durs = [e["ms"] for e in es if isinstance(e.get("ms"), int)]
        recent = [e for e in rated[-window:]]
        recent_errors = sum(1 for e in recent if not e.get("ok"))
        tools.append({
            "tool": tool, "calls": len(es), "errors": errors,
            "error_rate": round(errors / len(rated), 3) if rated else 0.0,
            "avg_ms": int(sum(durs) / len(durs)) if durs else None,
            "last_ts": es[-1].get("ts"),
            "recent_errors": recent_errors,
        })
        if len(recent) >= MIN_SAMPLE and recent_errors / len(recent) >= SUSPECT_RATE:
            suspects.append({
                "tool": tool, "recent_errors": recent_errors, "recent_calls": len(recent),
                "hint": "failing in live use — run `any2agent eval` to re-verify the toolset",
            })
    return {"calls_total": len(entries), "tools": tools, "suspects": suspects}
