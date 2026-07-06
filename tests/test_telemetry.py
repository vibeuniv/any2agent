"""runtime-telemetry: record/rotation/no-raise, summary + drift suspects,
agent recording points, /evals runtime section."""
import json
import os

from fastapi.testclient import TestClient

from any2agent.config import AgentConfig
from any2agent.core import agent as core_agent
from any2agent.evals import telemetry as T
from any2agent.server.app import build_app


def test_record_load_and_fields(tmp_path):
    sd = str(tmp_path / "s")
    T.record(sd, "notes_list", ok=True, status=200, ms=120)
    T.record(sd, "notes_get", ok=False, status=403, ms=80, authz=True)
    entries = T.load(sd)
    assert len(entries) == 2
    assert entries[0]["tool"] == "notes_list" and entries[0]["ok"] is True
    assert entries[1]["authz"] is True
    # never records args/bodies/identity — schema is closed
    assert set(entries[0]) <= {"ts", "tool", "ok", "status", "ms", "authz"}


def test_record_never_raises_and_noop_without_state_dir(tmp_path):
    T.record("", "x", ok=True)                      # no state_dir → no-op
    blocked = tmp_path / "file"
    blocked.write_text("not a dir")
    T.record(str(blocked / "sub"), "x", ok=True)    # unwritable → absorbed
    assert T.load("") == []


def test_rotation_keeps_recent(tmp_path):
    sd = str(tmp_path / "s")
    os.makedirs(sd)
    with open(T.path(sd), "w") as f:
        for i in range(T.MAX_LINES):
            f.write(json.dumps({"ts": i, "tool": "t", "ok": True}) + "\n")
    T.record(sd, "t", ok=True)  # tips over MAX_LINES → rotate
    with open(T.path(sd)) as f:
        lines = f.readlines()
    assert len(lines) == T.KEEP
    assert json.loads(lines[-1])["tool"] == "t"


def test_summary_rates_and_suspect_lifecycle(tmp_path):
    sd = str(tmp_path / "s")
    # notes_get: 6 failures then... suspect
    for _ in range(4):
        T.record(sd, "notes_get", ok=True, status=200, ms=100)
    for _ in range(6):
        T.record(sd, "notes_get", ok=False, status=500, ms=100)
    s = T.summary(sd)
    tool = next(t for t in s["tools"] if t["tool"] == "notes_get")
    assert tool["calls"] == 10 and tool["errors"] == 6
    assert s["suspects"] and s["suspects"][0]["tool"] == "notes_get"
    # recovery: recent window fills with successes → suspect clears itself
    for _ in range(10):
        T.record(sd, "notes_get", ok=True, status=200)
    assert T.summary(sd)["suspects"] == []


def test_suspect_needs_sample_and_ignores_authz(tmp_path):
    sd = str(tmp_path / "s")
    for _ in range(4):  # below MIN_SAMPLE
        T.record(sd, "a", ok=False, status=500)
    assert T.summary(sd)["suspects"] == []
    for _ in range(10):  # RBAC denials are not errors
        T.record(sd, "b", ok=False, status=403, authz=True)
    s = T.summary(sd)
    assert s["suspects"] == []
    assert next(t for t in s["tools"] if t["tool"] == "b")["error_rate"] == 0.0


def test_record_call_helper_skips_nothing_and_marks_authz(tmp_path, monkeypatch):
    sd = str(tmp_path / "s")
    core_agent._record_call({"state_dir": sd}, "notes_list",
                            {"ok": False, "status": 403}, t0=0)
    core_agent._record_call({"state_dir": sd}, "notes_list",
                            {"ok": True, "status": 200}, t0=0)
    entries = T.load(sd)
    assert entries[0]["authz"] is True and entries[1].get("authz") is None


def test_confirm_and_run_records(toolset, tmp_path):
    sd = str(tmp_path / "s")

    class Spy:
        def call(self, spec, args, ctx):
            return {"ok": True, "status": 201, "data": {}}

    core_agent.confirm_and_run("post__notes", {"title": "x"}, toolset, Spy(),
                               ctx={"state_dir": sd})
    entries = T.load(sd)
    assert len(entries) == 1 and entries[0]["tool"] == "post__notes"


def test_evals_endpoint_exposes_runtime(toolset, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = AgentConfig(project="p", base_url="http://target")
    c = TestClient(build_app(cfg, toolset))
    # telemetry alone (no eval history) counts as "evaluated" data being present
    T.record(cfg.state_dir(), "notes_list", ok=True, status=200, ms=50)
    d = c.get("/evals").json()
    assert d["runtime"]["calls_total"] == 1
    assert d["runtime"]["tools"][0]["tool"] == "notes_list"

def test_run_chat_records_executed_only(toolset, tmp_path, monkeypatch):
    """FR-05 contract at the run_chat level: an executed tool = exactly one
    line; a gated confirm_required = zero lines."""
    from any2agent.core import registry, dispatch as dispatch_mod

    sd = str(tmp_path / "s")
    monkeypatch.setattr(registry, "resolve", lambda *a, **k: ({"id": "gpt"}, "m", "gpt"))
    monkeypatch.setattr(registry, "completion_kwargs", lambda e: {})

    class FakeDelta:
        def __init__(self, tc=None): self.content = None; self.tool_calls = tc
    class FakeTC:
        def __init__(self, name):
            self.index = 0
            self.function = type("F", (), {"name": name, "arguments": "{}"})()
    def fake_stream(name):
        class Choice:
            def __init__(self, d): self.delta = d
        class Chunk:
            def __init__(self, d): self.choices = [Choice(d)]
        return iter([Chunk(FakeDelta([FakeTC(name)]))])

    calls = {"n": 0}
    def fake_completion(model, msgs, tools=None, stream=True, extra=None):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("stop the loop")
        return fake_stream(fake_completion.tool)
    monkeypatch.setattr(registry, "completion", fake_completion)

    class Spy:
        def call(self, spec, args, ctx):
            return {"ok": True, "status": 200, "data": []}

    # executed read tool → one telemetry line
    fake_completion.tool = "get__notes"
    calls["n"] = 0
    list(core_agent.run_chat([{"role": "user", "content": "x"}], toolset, Spy(),
                             ctx={"state_dir": sd}))
    assert len(T.load(sd)) == 1 and T.load(sd)[0]["tool"] == "get__notes"

    # gated write tool (no auto_confirm) → confirm event, NO telemetry line
    fake_completion.tool = "post__notes"
    calls["n"] = 0
    events = list(core_agent.run_chat([{"role": "user", "content": "x"}], toolset, Spy(),
                                      ctx={"state_dir": sd}))
    assert any(e["type"] == "confirm" for e in events)
    assert len(T.load(sd)) == 1, "confirm_required must not be recorded"


def test_load_skips_corrupt_lines(tmp_path):
    sd = str(tmp_path / "s")
    T.record(sd, "a", ok=True, status=200, ms=40)
    with open(T.path(sd), "a") as f:
        f.write("{corrupt\n")
    T.record(sd, "a", ok=True, status=200, ms=60)
    entries = T.load(sd)
    assert len(entries) == 2
    # avg_ms computed over the surviving entries
    assert T.summary(sd)["tools"][0]["avg_ms"] == 50
