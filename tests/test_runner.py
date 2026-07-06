"""Runner: confirm policy (read vs write), infra detection, budget, cleanup."""
from any2agent.evals import budget, runner as R
from any2agent.evals.model import EvalTask


def _fake_run_chat(events, ctx_sink=None):
    def gen(messages, toolset, adapter, model_id=None, prefer_default="", ctx=None):
        if ctx_sink is not None:
            ctx_sink.update(ctx or {})
        for ev in events:
            yield ev
    return gen


def test_read_task_records_write_blocked(toolset, stub_adapter, monkeypatch):
    events = [{"type": "delta", "text": "creating..."},
              {"type": "confirm", "name": "post__notes", "args": {}, "danger": False},
              {"type": "done", "model": "gpt"}]
    seen_ctx = {}
    monkeypatch.setattr(R.core_agent, "run_chat", _fake_run_chat(events, seen_ctx))
    trace = R.run_task(EvalTask(id="t", prompt="p"), toolset, stub_adapter)
    assert trace.write_blocked == "post__notes"
    assert "auto_confirm" not in seen_ctx, "read tasks must never auto-confirm"


def test_write_task_sets_auto_confirm_only_with_consent(toolset, stub_adapter, monkeypatch):
    events = [{"type": "tool", "name": "post__notes", "args": {"title": "x"},
               "result": {"ok": True, "status": 201, "data": {}}},
              {"type": "done", "model": "gpt"}]
    seen_ctx = {}
    monkeypatch.setattr(R.core_agent, "run_chat", _fake_run_chat(events, seen_ctx))
    task = EvalTask(id="t", prompt="p", kind="write")
    R.run_task(task, toolset, stub_adapter, write_ok=True)
    assert seen_ctx.get("auto_confirm") is True
    seen_ctx.clear()
    R.run_task(task, toolset, stub_adapter, write_ok=False)
    assert "auto_confirm" not in seen_ctx


def test_steps_answer_and_infra_error(toolset, stub_adapter, monkeypatch):
    events = [{"type": "delta", "text": "LLM call error: boom"},
              {"type": "done", "model": "gpt"}]
    monkeypatch.setattr(R.core_agent, "run_chat", _fake_run_chat(events))
    trace = R.run_task(EvalTask(id="t", prompt="p"), toolset, stub_adapter)
    assert trace.error.startswith("LLM call error") and not trace.steps

    events = [{"type": "tool", "name": "get__notes", "args": {},
               "result": {"ok": True, "status": 200, "data": []}},
              {"type": "delta", "text": "done: empty list"},
              {"type": "done", "model": "gpt"}]
    monkeypatch.setattr(R.core_agent, "run_chat", _fake_run_chat(events))
    trace = R.run_task(EvalTask(id="t", prompt="p"), toolset, stub_adapter)
    assert trace.steps == [{"tool": "get__notes", "args": {}, "ok": True, "status": 200, "error": ""}]
    assert trace.answer == "done: empty list" and not trace.error
    assert trace.rounds == 2  # tool round + answer round (best-effort)


def test_budget_exhaustion_is_reported_not_silent(toolset, stub_adapter):
    budget.reset(0)
    trace = R.run_task(EvalTask(id="t", prompt="p"), toolset, stub_adapter)
    assert trace.error == "skipped_budget"


def test_cleanup_runs_confirmed_and_reports_residue(toolset, stub_adapter):
    stub_adapter.responses["delete__notes_note_id"] = {"ok": False, "status": 404, "error": "http_404"}
    task = EvalTask(id="t", prompt="p", kind="write",
                    cleanup=[{"tool": "delete__notes_note_id", "args": {"note_id": "1"}},
                             {"tool": "no_such"}])
    residue = R.run_cleanup(task, toolset, stub_adapter)
    assert {r["why"] for r in residue} == {"http_404", "unknown_tool"}
    # danger tool executed without a confirm gate (dispatch called confirmed=True)
    assert stub_adapter.calls[0][0] == "delete__notes_note_id"
