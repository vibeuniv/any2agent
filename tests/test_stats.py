"""stats.py — reference-value checks for the inference primitives, plus the
--strict gate and judge voting wired through task_eval/grader."""
import pytest

from any2agent.evals import stats


# ── stats primitives (reference values) ──────────────────────────────────────

def test_wilson_reference_and_bounds():
    lo, hi = stats.wilson(8, 10)
    assert 0.47 < lo < 0.51 and 0.92 < hi < 0.95     # standard ref ≈ [0.49, 0.94]
    assert stats.wilson(0, 0) == (0.0, 1.0)          # no data → total ignorance
    lo, hi = stats.wilson(10, 10)
    assert hi == 1.0 and lo < 1.0                    # stays in [0,1] at the boundary


def test_underpowered_and_tasks_needed():
    assert stats.underpowered(3, 3)                  # too few
    assert stats.underpowered(9, 10)                 # 10 @0.9 still ~±0.19
    assert not stats.underpowered(45, 50)
    assert stats.tasks_needed(3, 3) > 0
    assert stats.tasks_needed(45, 50) == 0           # already powered


def test_mcnemar_exact():
    assert stats.mcnemar_exact(0, 0) == 1.0          # no change
    assert abs(stats.mcnemar_exact(0, 3) - 0.25) < 1e-9
    assert stats.mcnemar_exact(6, 0) < 0.05          # 6-0 sweep is significant
    assert stats.mcnemar_exact(5, 0) > 0.05          # 5-0 is not (p≈0.06)
    assert stats.mcnemar_exact(4, 4) > 0.9           # symmetric → no signal


def test_beta_binom_posterior():
    assert stats.beta_binom_gt(0, 10, 0.5) < 0.02    # all good → almost surely not degraded
    assert stats.beta_binom_gt(10, 10, 0.5) > 0.97   # all bad → almost surely degraded
    assert 0.6 < stats.beta_binom_gt(6, 10, 0.5) < 0.85
    # monotone in errors
    assert stats.beta_binom_gt(7, 10) > stats.beta_binom_gt(5, 10)


def test_vote():
    assert stats.vote([True, True, False]) == (True, 2 / 3)
    assert stats.vote([False, False, False]) == (False, 1.0)
    assert stats.vote([True, False]) == (False, 0.5)   # tie → fail (conservative)
    assert stats.vote([True]) == (True, 1.0)


# ── --strict gate + judge votes through task_eval ────────────────────────────

def _wire(monkeypatch, success):
    """success: a bool every graded task gets (task_eval may run the set twice)."""
    from any2agent import verifier as V
    from any2agent.evals import runner, grader
    from any2agent.evals.model import EvalResult, EvalTrace
    monkeypatch.setattr(V.registry, "llm_available", lambda: True)
    monkeypatch.setattr(runner, "run_task", lambda task, *a, **k: EvalTrace(task_id=task.id))
    monkeypatch.setattr(runner, "run_cleanup", lambda *a, **k: [])
    monkeypatch.setattr(grader, "grade",
                        lambda task, *a, **k: EvalResult(task_id=task.id, success=success))
    return V


def test_strict_gate_blocks_underpowered(monkeypatch, toolset, stub_adapter):
    from any2agent.evals.model import EvalTask
    V = _wire(monkeypatch, True)                      # 3/3 = rate 1.0 but n=3
    tasks = [EvalTask(id="t%d" % i, prompt="p", expected_tools=[["get__notes"]]) for i in range(3)]
    default = V.task_eval(toolset, stub_adapter, tasks, threshold=0.8, strict=False)
    assert default["passed"] is True                 # legacy gate: 1.0 >= 0.8
    strict = V.task_eval(toolset, stub_adapter, tasks, threshold=0.8, strict=True)
    assert strict["passed"] is False and strict["underpowered"] is True
    assert strict["add_tasks_for_power"] > 0
    assert strict["rate_ci"][0] < 0.8                # CI lower bound is what fails it


def test_strict_gate_passes_when_powered(monkeypatch, toolset, stub_adapter):
    from any2agent.evals.model import EvalTask
    V = _wire(monkeypatch, True)
    tasks = [EvalTask(id="t%d" % i, prompt="p", expected_tools=[["get__notes"]]) for i in range(30)]
    rep = V.task_eval(toolset, stub_adapter, tasks, threshold=0.8, strict=True)
    assert rep["passed"] is True and rep["underpowered"] is False
    assert rep["rate_ci"][0] >= 0.8


def test_judge_votes_majority(monkeypatch, toolset, stub_adapter):
    # 3 judge draws, 2 pass / 1 fail → majority pass, agreement 2/3
    from any2agent.evals import grader
    from any2agent.evals.model import EvalTask, EvalTrace
    seq = iter([{"pass": True, "reason": "a"}, {"pass": False, "reason": "b"},
                {"pass": True, "reason": "c"}])
    monkeypatch.setattr(grader, "_judge", lambda *a, **k: next(seq, None))
    task = EvalTask(id="t", prompt="did it work?")   # no det checks → judge decides
    r = grader.grade(task, EvalTrace(task_id="t"), toolset, stub_adapter, judge_votes=3)
    assert r.judge["pass"] is True and r.judge["agreement"] == 0.67 and r.judge["n"] == 3
