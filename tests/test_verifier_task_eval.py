"""task_eval critic: gating math, skip conventions, denominator hygiene."""
import pytest

from any2agent import verifier as V
from any2agent.evals.model import EvalTask, EvalResult, EvalTrace


def _tasks(n=4, kind="read"):
    return [EvalTask(id="t%d" % i, prompt="p%d" % i, kind=kind,
                     expected_tools=[["get__notes"]]) for i in range(n)]


def test_skips_without_adapter_key_or_tasks(toolset, stub_adapter, monkeypatch):
    assert V.task_eval(toolset, None, _tasks())["passed"] is None
    monkeypatch.setattr(V.registry, "llm_available", lambda: False)
    assert V.task_eval(toolset, stub_adapter, _tasks())["skipped"] == "no provider key"
    monkeypatch.setattr(V.registry, "llm_available", lambda: True)
    assert V.task_eval(toolset, stub_adapter, [])["skipped"] == "no tasks"


def _wire(monkeypatch, results_by_id, traces_by_id=None):
    """Stub runner/grader so task_eval's aggregation is tested in isolation."""
    from any2agent.evals import runner, grader
    monkeypatch.setattr(V.registry, "llm_available", lambda: True)
    monkeypatch.setattr(runner, "run_task",
                        lambda task, *a, **k: (traces_by_id or {}).get(task.id) or EvalTrace(task_id=task.id))
    monkeypatch.setattr(runner, "run_cleanup", lambda task, *a, **k: [])
    monkeypatch.setattr(grader, "grade", lambda task, trace, *a, **k: results_by_id[task.id])


def test_rate_gate_and_failed_list(toolset, stub_adapter, monkeypatch):
    tasks = _tasks(4)
    results = {t.id: EvalResult(task_id=t.id, success=(t.id != "t3"),
                                reasons=[] if t.id != "t3" else ["expected tools not covered"])
               for t in tasks}
    _wire(monkeypatch, results)
    rep = V.task_eval(toolset, stub_adapter, tasks, threshold=0.8)
    assert rep["rate"] == 0.75 and rep["passed"] is False and rep["failed"] == ["t3"]
    rep = V.task_eval(toolset, stub_adapter, tasks, threshold=0.7)
    assert rep["passed"] is True


def test_infra_and_ungraded_leave_the_denominator(toolset, stub_adapter, monkeypatch):
    tasks = _tasks(3)
    results = {
        "t0": EvalResult(task_id="t0", success=True),
        "t1": EvalResult(task_id="t1", success=False, reasons=["runner: LLM call error: boom"]),
        "t2": EvalResult(task_id="t2", success=False, ungraded=True,
                         reasons=["ungraded: no deterministic checks and judge unavailable"]),
    }
    traces = {"t1": EvalTrace(task_id="t1", error="LLM call error: boom")}
    _wire(monkeypatch, results, traces)
    rep = V.task_eval(toolset, stub_adapter, tasks, threshold=0.8)
    assert rep["rated"] == 1 and rep["rate"] == 1.0 and rep["passed"] is True
    assert rep["infra_errors"] == 1 and rep["ungraded"] == 1


def test_skipped_budget_is_a_distinct_bucket(toolset, stub_adapter, monkeypatch):
    tasks = _tasks(2)
    results = {
        "t0": EvalResult(task_id="t0", success=True),
        "t1": EvalResult(task_id="t1", success=False, reasons=["runner: skipped_budget"]),
    }
    traces = {"t1": EvalTrace(task_id="t1", error="skipped_budget")}
    _wire(monkeypatch, results, traces)
    rep = V.task_eval(toolset, stub_adapter, tasks)
    # budget exhaustion is not an infra failure and leaves the denominator
    assert rep["skipped_budget"] == 1 and rep["infra_errors"] == 0 and rep["rated"] == 1


def test_write_tasks_skipped_without_consent(toolset, stub_adapter, monkeypatch):
    tasks = _tasks(2) + _tasks(1, kind="write")
    results = {t.id: EvalResult(task_id=t.id, success=True) for t in tasks}
    _wire(monkeypatch, results)
    rep = V.task_eval(toolset, stub_adapter, tasks, write_ok=False)
    assert rep["skipped_write"] == 1 and rep["rated"] == 2


def test_run_all_without_eval_tasks_is_unchanged(toolset):
    routes = [{"method": "GET", "path": "/notes"}]
    rep = V.run_all(toolset, routes, None, [], live=False)
    assert [r["name"] for r in rep["reports"]] == ["coverage", "accuracy"]
    assert rep["thresholds"]["task_eval_rate"] == 0.8
