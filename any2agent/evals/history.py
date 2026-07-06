"""Eval run history — one JSONL line per run under the project's state dir, so
`eval` can show a trend ("did the tool set get better or worse?") without any
dashboard. Corrupt lines are skipped, never fatal.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

_FILENAME = "eval-history.jsonl"


def path(state_dir: str) -> str:
    return os.path.join(state_dir, _FILENAME)


def append(state_dir: str, rep: Dict[str, Any], fixes=None) -> Dict[str, Any]:
    """Reduce a task_eval report to a one-line summary and append it. `fixes`
    carries the per-failure lesson lines so the web console can show "what to
    fix" for past runs, not just the latest lessons file."""
    entry = {
        "ts": int(time.time()),
        "rate": rep.get("rate", 0.0),
        "rated": rep.get("rated", 0),
        "passed": bool(rep.get("passed")),
        "failed": rep.get("failed", []),
        "skipped_write": rep.get("skipped_write", 0),
        "skipped_budget": rep.get("skipped_budget", 0),
        "infra": rep.get("infra_errors", 0),
        "ungraded": rep.get("ungraded", 0),
    }
    if fixes:
        entry["fixes"] = [{"task_id": f.get("task_id"), "class": f.get("class"),
                           "guidance": f.get("guidance")} for f in fixes]
    os.makedirs(state_dir, exist_ok=True)
    with open(path(state_dir), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def load(state_dir: str, n: int = 10) -> List[Dict[str, Any]]:
    p = path(state_dir)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue  # corrupt line — skip, never fatal
    return out[-n:]


def trend_line(entries: List[Dict[str, Any]]) -> str:
    """One line the user actually needs: current vs previous rate."""
    if not entries:
        return ""
    cur = entries[-1]
    if len(entries) == 1:
        return "rate %.2f (first recorded run)" % cur.get("rate", 0.0)
    prev = entries[-2]
    d = cur.get("rate", 0.0) - prev.get("rate", 0.0)
    arrow = "▲" if d > 0 else ("▼" if d < 0 else "=")
    return "rate %.2f (prev %.2f %s%.2f, %d runs)" % (
        cur.get("rate", 0.0), prev.get("rate", 0.0), arrow, abs(d), len(entries))
