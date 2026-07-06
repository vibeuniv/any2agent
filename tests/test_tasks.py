"""EvalTask generation, validation, and curation-file preference."""
import json

from any2agent.evals import tasks as T
from any2agent.evals.model import EvalTask, EVAL_MARKER


def test_fallback_generates_pair_and_single_read_tasks(toolset):
    out = T.generate_fallback(toolset, n=8)
    assert out, "fallback must produce tasks from read tools"
    # multi-step pair: list -> detail
    pair = [t for t in out if t.id.startswith("fb-pair")]
    assert pair and pair[0].expected_tools == [["get__notes", "get__notes_note_id"]]
    # never generates write tasks (safety without an LLM)
    assert all(t.kind == "read" for t in out)


def test_validate_rejects_unknown_tools_and_unsafe_writes(toolset):
    ts = [
        EvalTask(id="ok", prompt="list notes", expected_tools=[["get__notes"]]),
        EvalTask(id="ghost", prompt="x", expected_tools=[["no_such_tool"]]),
        EvalTask(id="w-nomark", prompt="create a note", kind="write",
                 expected_tools=[["post__notes"]]),
        EvalTask(id="w-danger", prompt="create %s note" % EVAL_MARKER, kind="write",
                 expected_tools=[["delete__notes_note_id"]]),
        EvalTask(id="badcheck", prompt="x", checks=[{"type": "nope"}]),
    ]
    valid, invalid = T.validate(ts, toolset)
    assert [t.id for t in valid] == ["ok"]
    whys = {iv["id"]: iv["why"] for iv in invalid}
    assert "no_such_tool" in whys["ghost"]
    assert EVAL_MARKER in whys["w-nomark"]
    assert "danger" in whys["w-danger"]
    assert "unknown check" in whys["badcheck"]


def test_curated_file_wins_over_generation(toolset, tmp_path, monkeypatch):
    path = str(tmp_path / "notes-api.evals.json")
    curated = EvalTask(id="manual-1", prompt="my curated task",
                       expected_tools=[["get__notes"]], source="manual")
    T.save(path, "notes-api", [curated])
    called = {"llm": False}
    monkeypatch.setattr(T, "generate_llm", lambda *a, **k: called.__setitem__("llm", True))
    valid, invalid = T.load_or_generate(toolset, path)
    assert [t.id for t in valid] == ["manual-1"] and not invalid
    assert called["llm"] is False, "curated evals.json must never be regenerated implicitly"


def test_generate_persists_for_curation(toolset, tmp_path, monkeypatch):
    path = str(tmp_path / "notes-api.evals.json")
    monkeypatch.setattr(T, "generate_llm", lambda *a, **k: None)  # no key -> fallback
    valid, _ = T.load_or_generate(toolset, path)
    assert valid
    with open(path, encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["project"] == "notes-api" and len(saved["tasks"]) == len(valid)
