"""Task-based self-verification (eval) harness.

Measures what the other critics can't: whether the generated agent actually
COMPLETES realistic user tasks against the live API — not just whether tools
exist (coverage), are well-formed (accuracy), respond (liveness), or get
selected (agent_e2e). Entry points: `any2agent eval` (CLI) and
`verifier.task_eval` (5th critic).
"""
from .model import EvalTask, EvalTrace, EvalResult  # noqa: F401
