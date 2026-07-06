"""Eval-only LLM budget — same discipline as llm_repair's repair budget, but an
independent counter so an eval run and the connect repair loop can't starve
each other. One unit = one eval-initiated LLM interaction (a task generation
call, a judge call, or one task run through the agent loop). Exhaustion is
reported by the caller (skipped_budget), never silent.
"""
from __future__ import annotations

_CALL_BUDGET = 40
_calls_made = 0


def reset(n: int = 40) -> None:
    global _CALL_BUDGET, _calls_made
    _CALL_BUDGET, _calls_made = n, 0


def left() -> int:
    return max(0, _CALL_BUDGET - _calls_made)


def spend() -> bool:
    """Consume one unit; False when the budget is already exhausted."""
    global _calls_made
    if left() <= 0:
        return False
    _calls_made += 1
    return True
