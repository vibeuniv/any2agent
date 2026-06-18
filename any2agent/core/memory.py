"""Cross-turn / cross-session memory: small declarative facts the agent can recall.

Design (RBAC-safe, no embedding key needed):
  - Physical isolation by owner: each owner's notes live in their OWN file, named
    by a hash of the owner key. recall/remember open exactly one owner's file —
    there is no cross-owner read path, so one user can never see another's notes.
  - Owner key comes from the embedding app (a stable user id forwarded as a header,
    configured by `memory_owner_header`). With no header it falls back to a single
    shared "anon" bucket — fine for local / single-user, NOT for multi-user.
  - Recall is keyword overlap scoring (no embeddings → no extra provider key).
  - Secrets are refused on write; per-owner record cap prevents unbounded growth.

Stored as JSON under .any2agent-state/<project>/memory/<owner-hash>.json.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Dict, List

_SECRET = re.compile(r"(password|passwd|secret|token|api[_-]?key|bearer|otp|credential|private[_-]?key)", re.I)
_WORD = re.compile(r"[a-z0-9]+")
_MAX_RECORDS = 200          # per-owner cap (drops oldest)
_MAX_LEN = 500              # per-note char cap


def _owner_file(state_dir: str, owner: str) -> str:
    d = os.path.join(state_dir, "memory")
    os.makedirs(d, exist_ok=True)
    h = hashlib.sha256((owner or "anon").encode()).hexdigest()[:16]
    return os.path.join(d, h + ".json")


def _load(path: str) -> List[str]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return [str(x) for x in data] if isinstance(data, list) else []
    except Exception:
        return []


def _save(path: str, recs: List[str]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(recs, f, ensure_ascii=False)
    os.replace(tmp, path)


def is_secret(text: str) -> bool:
    return bool(_SECRET.search(text or ""))


def remember(state_dir: str, owner: str, text: str) -> Dict:
    """Save one durable fact for this owner. Refuses empty/secret text, dedups,
    and caps the store (oldest dropped)."""
    text = (text or "").strip()[:_MAX_LEN]
    if not text:
        return {"ok": False, "reason": "empty"}
    if is_secret(text):
        return {"ok": False, "reason": "looks_secret"}
    path = _owner_file(state_dir, owner)
    recs = _load(path)
    if text in recs:
        return {"ok": True, "count": len(recs), "note": "already_known"}
    recs.append(text)
    if len(recs) > _MAX_RECORDS:
        recs = recs[-_MAX_RECORDS:]
    _save(path, recs)
    return {"ok": True, "count": len(recs)}


def forget(state_dir: str, owner: str, query: str) -> Dict:
    """Delete this owner's notes containing the query substring."""
    path = _owner_file(state_dir, owner)
    recs = _load(path)
    q = (query or "").lower().strip()
    kept = recs if not q else [r for r in recs if q not in r.lower()]
    _save(path, kept)
    return {"ok": True, "removed": len(recs) - len(kept), "count": len(kept)}


def recall(state_dir: str, owner: str, query: str = "", top_k: int = 6) -> List[str]:
    """Return this owner's most relevant notes. With a query, score by keyword
    overlap (substring match counts too); without one, return the most recent."""
    recs = _load(_owner_file(state_dir, owner))
    if not recs:
        return []
    q = (query or "").lower()
    qtokens = set(_WORD.findall(q))
    if not qtokens:
        return list(reversed(recs))[:top_k]        # most-recent first
    scored = []
    for r in recs:
        rl = r.lower()
        rtokens = set(_WORD.findall(rl))
        score = len(qtokens & rtokens)
        for t in qtokens:                          # substring bonus ("deploy" in "deployment")
            if len(t) >= 4 and t in rl:
                score += 1
        if score:
            scored.append((score, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:top_k]]


# ---- built-in agent tools (declarative defs; the LLM calls these by name) ----
REMEMBER_TOOL_NAME = "remember_note"
RECALL_TOOL_NAME = "recall_notes"
FORGET_TOOL_NAME = "forget_notes"

MEMORY_TOOLS_DEF = [
    {"type": "function", "function": {
        "name": REMEMBER_TOOL_NAME,
        "description": "Save a small, durable fact about THIS user or their stated "
                       "preferences so you can use it in later turns and future sessions. "
                       "Use only for stable facts the user wants remembered (e.g. their team, "
                       "a default they prefer). Never store secrets, passwords, or tokens.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "The fact to remember, one short sentence."}},
            "required": ["text"]}}},
    {"type": "function", "function": {
        "name": RECALL_TOOL_NAME,
        "description": "Look up facts you previously remembered about this user. "
                       "Relevant notes are already shown to you each turn; call this only "
                       "when you need to search for something specific.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Optional keywords to filter notes."}}}}},
    {"type": "function", "function": {
        "name": FORGET_TOOL_NAME,
        "description": "Delete previously remembered facts that match the query "
                       "(e.g. when the user says a fact is no longer true).",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Notes containing this text are removed."}},
            "required": ["query"]}}},
]


def handle(name: str, args: Dict, state_dir: str, owner: str):
    """Dispatch a built-in memory tool call by name. Returns (handled, result).
    These are LOCAL state ops (owner-scoped, no external effect) so they bypass the
    write/danger confirmation gate."""
    if name == REMEMBER_TOOL_NAME:
        return True, remember(state_dir, owner, args.get("text", ""))
    if name == RECALL_TOOL_NAME:
        return True, {"notes": recall(state_dir, owner, args.get("query", ""))}
    if name == FORGET_TOOL_NAME:
        return True, forget(state_dir, owner, args.get("query", ""))
    return False, None
