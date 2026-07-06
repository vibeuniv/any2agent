"""Data contracts for the eval harness.

An EvalTask is a realistic natural-language request the agent should complete
with the tool set (often multi-step). Tasks persist as <project>.evals.json so
a team can curate them into a regression asset. Grading prefers deterministic
checks; the LLM judge is advisory (see grader.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# write-task payloads must carry this marker so state checks / cleanup can find
# (and only ever touch) eval-created records on the target system.
EVAL_MARKER = "[a2a-eval]"


@dataclass
class EvalTask:
    id: str
    prompt: str                      # the user request, verbatim
    kind: str = "read"               # "read" | "write"
    # OR-of-AND: each inner list is one valid solution path (order-free subset
    # of the tools that must have been called). Empty = don't constrain tools.
    expected_tools: List[List[str]] = field(default_factory=list)
    # deterministic checks (see grader.CHECK_TYPES) + optional {"type":"judge"}
    checks: List[Dict[str, Any]] = field(default_factory=list)
    # write tasks: calls that undo side effects, run confirmed after grading
    cleanup: List[Dict[str, Any]] = field(default_factory=list)
    source: str = "generated"        # "generated" | "manual" (curation tracking)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "EvalTask":
        return EvalTask(
            id=str(d.get("id", "")),
            prompt=d.get("prompt", ""),
            kind=d.get("kind", "read"),
            expected_tools=[list(p) for p in (d.get("expected_tools") or [])],
            checks=list(d.get("checks") or []),
            cleanup=list(d.get("cleanup") or []),
            source=d.get("source", "manual"),
        )


@dataclass
class EvalTrace:
    """What actually happened when the runner played a task through run_chat."""
    task_id: str
    steps: List[Dict[str, Any]] = field(default_factory=list)  # {"tool","args","ok","status","error"}
    answer: str = ""                 # accumulated assistant text
    rounds: int = 0                  # best-effort LLM round count (0 = unknown)
    error: str = ""                  # runner-level failure (LLM/infra), not a graded fail
    write_blocked: str = ""          # tool name a READ task tried to write with


@dataclass
class EvalResult:
    task_id: str
    success: bool
    reasons: List[str] = field(default_factory=list)  # failure reasons (honest report)
    checks_passed: int = 0
    checks_total: int = 0
    judge: Optional[Dict[str, Any]] = None            # {"pass","reason"} | None (skipped)
    metrics: Dict[str, Any] = field(default_factory=dict)
    # no deterministic signal AND judge unavailable -> excluded from the rate
    # denominator (never a silent pass)
    ungraded: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
