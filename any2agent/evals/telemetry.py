"""Runtime tool-call telemetry — the runtime counterpart of the build-time eval.

Every executed tool call gets one JSONL line (tool, ok, status, ms, ts — NEVER
args, response bodies, or user identity: same discipline as memory). From the
recent window we derive per-tool error rates and flag "suspects": tools that
keep failing in live use, which usually means the target API drifted and the
toolset needs re-verification (`any2agent eval`). Suspect status is computed
from the recent window only, so a recovered tool clears itself — no sticky
flags. Recording must never break a conversation: every exception is absorbed.

Drift webhook (opt-in): set the env var ANY2AGENT_ALERT_WEBHOOK to a URL and
each time a tool *crosses into* suspect state one JSON alert is POSTed there
({tool, recent_errors, recent_calls, hint, ts}). Delivery is fire-and-forget in
a daemon thread with a 5s timeout — it never blocks or delays record(), and
every delivery exception is absorbed. Episodes are de-duplicated via a marker
file (<state_dir>/alerts.json): one alert per drift episode, re-armed once the
tool recovers below the suspect threshold. No config file involved — the env var
is the whole surface.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

_FILENAME = "tool-calls.jsonl"
_ALERTS_FILE = "alerts.json"   # per-tool drift-episode markers (webhook dedup)
ALERT_WEBHOOK_ENV = "ANY2AGENT_ALERT_WEBHOOK"
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
        # drift webhook: only a failing, non-authz call can push a tool into
        # suspect state, so that is the only case worth the recent-window check.
        if not ok and not authz:
            _maybe_alert(state_dir, tool)
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
        if _is_suspect(recent_errors, len(recent)):
            suspects.append({
                "tool": tool, "recent_errors": recent_errors, "recent_calls": len(recent),
                "hint": _SUSPECT_HINT,
            })
    return {"calls_total": len(entries), "tools": tools, "suspects": suspects}


# ── drift webhook ──────────────────────────────────────────────────────────────
# One JSON alert POSTed the moment a tool crosses into suspect state. Opt-in via
# ANY2AGENT_ALERT_WEBHOOK. The whole path is best-effort and off the hot thread;
# it can never delay, block, or raise out of record().

_SUSPECT_HINT = "failing in live use — run `any2agent eval` to re-verify the toolset"


def _is_suspect(recent_errors: int, recent_calls: int) -> bool:
    """The single suspect rule, shared by summary() and the alert path so the
    webhook fires on exactly the drift the /evals view would show."""
    return recent_calls >= MIN_SAMPLE and recent_errors / recent_calls >= SUSPECT_RATE


def _maybe_alert(state_dir: str, tool: str) -> None:
    """Called after a failing (non-authz) call. Fires one webhook alert when
    `tool` newly becomes a suspect, de-duplicated per drift episode via
    <state_dir>/alerts.json. When the tool drops back below the threshold its
    marker is cleared, so the next episode alerts again. Never raises."""
    url = os.environ.get(ALERT_WEBHOOK_ENV)
    if not url:
        return
    try:
        rated = [e for e in load(state_dir)
                 if e.get("tool") == tool and not e.get("authz")]
        recent = rated[-WINDOW:]
        recent_errors = sum(1 for e in recent if not e.get("ok"))
        suspect = _is_suspect(recent_errors, len(recent))
        marker = os.path.join(state_dir, _ALERTS_FILE)
        alerts = _load_alerts(marker)
        alerted = tool in alerts
        if suspect and not alerted:
            alerts[tool] = int(time.time())   # open the episode → dedups re-alerts
            _save_alerts(marker, alerts)
            _send_alert(url, {
                "tool": tool,
                "recent_errors": recent_errors,
                "recent_calls": len(recent),
                "hint": _SUSPECT_HINT,
                "ts": int(time.time()),
            })
        elif alerted and not suspect:
            alerts.pop(tool, None)            # episode closed → a fresh one re-alerts
            _save_alerts(marker, alerts)
    except Exception:
        pass  # alerting is best-effort; it never disturbs recording


def _load_alerts(marker: str) -> Dict[str, Any]:
    try:
        with open(marker, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_alerts(marker: str, alerts: Dict[str, Any]) -> None:
    try:
        with open(marker, "w", encoding="utf-8") as f:
            json.dump(alerts, f, ensure_ascii=False)
    except Exception:
        pass


def _send_alert(url: str, payload: Dict[str, Any]) -> None:
    """Delivery seam (tests monkeypatch this). Fires the POST in a daemon thread
    so record() returns immediately and never waits on the network."""
    import threading
    threading.Thread(target=_deliver, args=(url, payload), daemon=True).start()


def _deliver(url: str, payload: Dict[str, Any]) -> None:
    """The actual one-shot POST: 5s timeout, every exception absorbed (a dead or
    slow webhook must never surface anywhere near the conversation)."""
    try:
        import urllib.request
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).close()
    except Exception:
        pass
