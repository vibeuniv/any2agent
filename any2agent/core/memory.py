"""Project- and owner-scoped memory: small declarative notes the agent can recall
across turns. Stored as JSON under .any2agent-state/<project>/memory/<owner>.json.
Never store secrets here.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Dict, List

_SECRET = re.compile(r"(password|secret|token|api[_-]?key|otp|credential)", re.I)


def _owner_file(state_dir: str, owner: str) -> str:
    d = os.path.join(state_dir, "memory")
    os.makedirs(d, exist_ok=True)
    h = hashlib.sha256((owner or "anon").encode()).hexdigest()[:16]
    return os.path.join(d, h + ".json")


def _load(path: str) -> List[str]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def remember(state_dir: str, owner: str, text: str) -> Dict:
    text = (text or "").strip()
    if not text:
        return {"ok": False, "reason": "empty"}
    if _SECRET.search(text):
        return {"ok": False, "reason": "looks_secret"}
    path = _owner_file(state_dir, owner)
    recs = _load(path)
    if text not in recs:
        recs.append(text)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(recs, f, ensure_ascii=False)
    return {"ok": True, "count": len(recs)}


def forget(state_dir: str, owner: str, query: str) -> Dict:
    path = _owner_file(state_dir, owner)
    recs = _load(path)
    q = (query or "").lower()
    kept = [r for r in recs if q not in r.lower()]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(kept, f, ensure_ascii=False)
    return {"ok": True, "removed": len(recs) - len(kept), "count": len(kept)}


def recall(state_dir: str, owner: str) -> List[str]:
    return _load(_owner_file(state_dir, owner))
