"""Grader: every check type, expected-tool paths, failure ordering, ungraded."""
import pytest

from any2agent.evals import grader as G
from any2agent.evals.model import EvalTask, EvalTrace


@pytest.fixture(autouse=True)
def no_judge(monkeypatch):
    """Tests must be deterministic even on a machine with provider keys set."""
    monkeypatch.setattr(G.registry, "resolve", lambda *a, **k: (None, None, None))


def _trace(task_id="t", steps=None, answer="", **kw):
    return EvalTrace(task_id=task_id, steps=steps or [], answer=answer, **kw)


def _step(tool, ok=True, status=200, args=None, error=""):
    return {"tool": tool, "args": args or {}, "ok": ok, "status": status, "error": error}


def test_expected_tools_or_of_and(toolset, stub_adapter):
    task = EvalTask(id="t", prompt="p",
                    expected_tools=[["get__notes", "get__notes_note_id"], ["get__health"]])
    # alternative path (health only) satisfies the OR
    r = G.grade(task, _trace(steps=[_step("get__health")]), toolset, stub_adapter)
    assert r.success
    # partial coverage of the first path fails
    r = G.grade(task, _trace(steps=[_step("get__notes")]), toolset, stub_adapter)
    assert not r.success and "expected tools not covered" in r.reasons[0]
    assert r.metrics["wrong_tool_calls"] == 0


def test_check_tool_called_and_no_errors(toolset, stub_adapter):
    task = EvalTask(id="t", prompt="p",
                    checks=[{"type": "tool_called", "any_of": ["get__notes"]},
                            {"type": "no_errors"}])
    ok = G.grade(task, _trace(steps=[_step("get__notes")]), toolset, stub_adapter)
    assert ok.success and ok.checks_passed == 2
    bad = G.grade(task, _trace(steps=[_step("get__notes", ok=False, status=500, error="http_500")]),
                  toolset, stub_adapter)
    assert not bad.success and any("no_errors" in x for x in bad.reasons)


def test_check_answer_contains_casefold(toolset, stub_adapter):
    task = EvalTask(id="t", prompt="p", checks=[{"type": "answer_contains", "value": "Grocery"}])
    assert G.grade(task, _trace(steps=[_step("get__notes")], answer="the GROCERY note"),
                   toolset, stub_adapter).success


def test_check_state_recalls_read_tool(toolset, stub_adapter):
    stub_adapter.responses["get__notes"] = {"ok": True, "status": 200,
                                            "data": [{"title": "[a2a-eval] hello"}]}
    task = EvalTask(id="t", prompt="p",
                    checks=[{"type": "state", "tool": "get__notes", "args": {},
                             "expect_contains": "[a2a-eval] hello"}])
    r = G.grade(task, _trace(steps=[_step("post__notes")]), toolset, stub_adapter)
    assert r.success
    assert stub_adapter.calls and stub_adapter.calls[-1][0] == "get__notes"


def test_runner_error_and_write_blocked_fail_before_checks(toolset, stub_adapter):
    task = EvalTask(id="t", prompt="p", checks=[{"type": "no_errors"}])
    r = G.grade(task, _trace(error="LLM call error: boom"), toolset, stub_adapter)
    assert not r.success and r.reasons[0].startswith("runner:")
    r = G.grade(task, _trace(write_blocked="post__notes"), toolset, stub_adapter)
    assert not r.success and "attempted write tool" in r.reasons[0]


def test_ungraded_when_no_signal_and_no_judge(toolset, stub_adapter):
    task = EvalTask(id="t", prompt="p")  # no expected_tools, no checks
    r = G.grade(task, _trace(steps=[_step("get__notes")]), toolset, stub_adapter)
    assert r.ungraded and not r.success


def test_bad_calls_metric_feeds_repair(toolset, stub_adapter):
    task = EvalTask(id="t", prompt="p", expected_tools=[["post__notes"]])
    trace = _trace(steps=[_step("post__notes", ok=False, status=422,
                                args={"wrong_field": 1}, error="http_422")])
    r = G.grade(task, trace, toolset, stub_adapter)
    assert r.metrics["bad_calls"] == [{"tool": "post__notes", "status": 422,
                                       "args": '{"wrong_field": 1}'}]
