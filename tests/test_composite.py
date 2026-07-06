"""tool-composition phase 2: binding resolution, the composite executor (success
+ honest partial failure + flag inheritance), dispatch gating, proposal/approval,
and compatibility with the verifier / evals / shaping code paths."""
import json
from types import SimpleNamespace

import pytest

from any2agent import compose
from any2agent.core import composite as C
from any2agent.core import dispatch
from any2agent.spec import ToolSet, ToolSpec


def _tool(name, method, path, write=False, danger=False, params=None):
    return ToolSpec(name=name, description="%s %s" % (method, path),
                    parameters={"type": "object", "properties": params or {}},
                    backing={"method": method, "path": path},
                    write=write, danger=danger, domain="notes")


def _composite(name, steps, **kw):
    return ToolSpec(name=name, description=kw.pop("desc", "composite"),
                    parameters=kw.pop("parameters", {"type": "object", "properties": {}}),
                    backing={"composite": steps}, **kw)


@pytest.fixture
def leaves():
    return ToolSet("notes-api", [
        _tool("notes_list", "GET", "/notes"),
        _tool("notes_get", "GET", "/notes/{note_id}", params={"note_id": {"type": "string"}}),
        _tool("notes_create", "POST", "/notes", write=True),
        _tool("notes_delete", "DELETE", "/notes/{note_id}", write=True, danger=True,
              params={"note_id": {"type": "string"}}),
        _tool("health_get", "GET", "/health"),
    ])


class Recorder:
    """Adapter stub: canned per-tool responses, records the resolved args it saw."""
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def call(self, spec, args, ctx):
        self.calls.append((spec.name, dict(args or {})))
        return dict(self.responses.get(spec.name, {"ok": True, "status": 200, "data": {}}))


# ── binding resolution ─────────────────────────────────────────────────────────

def test_binding_input_and_step_paths():
    results = [{"ok": True, "status": 200, "data": [{"id": 7}, {"id": 9}]}]
    assert C.resolve_args("$input.note_id", {"note_id": "abc"}, []) == "abc"
    assert C.resolve_args("$steps[0].data[0].id", {}, results) == 7
    assert C.resolve_args("$steps[-1].data[1].id", {}, results) == 9
    # literals and nested structures pass through / resolve in place
    assert C.resolve_args({"a": "lit", "b": "$steps[0].data[1].id"}, {}, results) == {"a": "lit", "b": 9}
    assert C.resolve_args(["x", "$input.q"], {"q": 5}, []) == ["x", 5]


def test_binding_errors_are_explicit():
    results = [{"ok": True, "data": [{"id": 1}]}]
    for expr, needle in [("$steps[3].data", "out of range"),
                         ("$steps[0].data[9].id", "out of range"),
                         ("$steps[0].ok[0]", "not indexable"),   # ok is a bool, not a list
                         ("$steps[0].nope", "no key"),
                         ("$bogus.x", "must start with")]:
        with pytest.raises(C.BindingError) as e:
            C.resolve_args(expr, {}, results)
        assert needle in str(e.value)


# ── executor ─────────────────────────────────────────────────────────────────

def test_executor_success_binds_step_output_into_next_call(leaves):
    bn = leaves.by_name()
    rec = Recorder({"notes_list": {"ok": True, "status": 200, "data": [{"id": 42}]},
                    "notes_get": {"ok": True, "status": 200, "data": {"id": 42, "title": "hi"}}})
    comp = _composite("notes_open_first", [
        {"tool": "notes_list", "args": {}},
        {"tool": "notes_get", "args": {"note_id": "$steps[0].data[0].id"}},
    ])
    res = C.run(comp, {}, rec, by_name=bn)
    assert res["ok"] and res["completed"] == 2 and res["total"] == 2
    assert res["data"] == {"id": 42, "title": "hi"}
    assert rec.calls == [("notes_list", {}), ("notes_get", {"note_id": 42})]


def test_executor_input_binding(leaves):
    rec = Recorder({"notes_get": {"ok": True, "status": 200, "data": {"id": 5}}})
    comp = _composite("list_then_get", [
        {"tool": "notes_list", "args": {}},
        {"tool": "notes_get", "args": {"note_id": "$input.note_id"}},
    ])
    res = C.run(comp, {"note_id": 5}, rec, by_name=leaves.by_name())
    assert res["ok"] and rec.calls[-1] == ("notes_get", {"note_id": 5})


def test_executor_partial_failure_is_reported_and_stops(leaves):
    rec = Recorder({"notes_list": {"ok": True, "status": 200, "data": [{"id": 1}]},
                    "notes_get": {"ok": False, "status": 404, "error": "http_404"}})
    comp = _composite("notes_open_first", [
        {"tool": "notes_list", "args": {}},
        {"tool": "notes_get", "args": {"note_id": "$steps[0].data[0].id"}},
    ])
    res = C.run(comp, {}, rec, by_name=leaves.by_name())
    assert not res["ok"]
    assert res["completed"] == 1 and res["failed_step"] == 1 and res["failed_tool"] == "notes_get"
    assert res["error"] == "http_404" and res["rolled_back"] is False
    assert len(res["steps"]) == 2 and res["steps"][1]["ok"] is False


def test_executor_binding_error_fails_that_step(leaves):
    rec = Recorder({"notes_list": {"ok": True, "status": 200, "data": []}})  # empty -> [0] invalid
    comp = _composite("notes_open_first", [
        {"tool": "notes_list", "args": {}},
        {"tool": "notes_get", "args": {"note_id": "$steps[0].data[0].id"}},
    ])
    res = C.run(comp, {}, rec, by_name=leaves.by_name())
    assert not res["ok"] and res["failed_step"] == 1
    assert res["error"].startswith("binding_error")
    # the second call never happened
    assert [c[0] for c in rec.calls] == ["notes_list"]


def test_executor_reports_uncommitted_writes_on_later_failure(leaves):
    rec = Recorder({"notes_create": {"ok": True, "status": 200, "data": {"id": 3}},
                    "notes_get": {"ok": False, "status": 500, "error": "boom"}})
    comp = _composite("create_then_get", [
        {"tool": "notes_create", "args": {"title": "x"}},
        {"tool": "notes_get", "args": {"note_id": "$steps[0].data.id"}},
    ])
    res = C.run(comp, {}, rec, by_name=leaves.by_name())
    assert not res["ok"] and res["completed"] == 1
    assert "NOT rolled back" in res["note"] and "write step" in res["note"]


def test_executor_rejects_unknown_and_nested_steps(leaves):
    bn = leaves.by_name()
    r1 = C.run(_composite("c", [{"tool": "ghost", "args": {}}, {"tool": "notes_list", "args": {}}]),
               {}, Recorder(), by_name=bn)
    assert not r1["ok"] and r1["error"].startswith("unknown_tool")
    nested = _composite("inner", [{"tool": "notes_list", "args": {}}, {"tool": "notes_get", "args": {}}])
    bn2 = dict(bn); bn2["inner"] = nested
    r2 = C.run(_composite("outer", [{"tool": "inner", "args": {}}, {"tool": "notes_list", "args": {}}]),
               {}, Recorder(), by_name=bn2)
    assert not r2["ok"] and "nested composites" in r2["error"]


# ── effective flags + dispatch gate ────────────────────────────────────────────

def test_effective_flags_are_max_of_steps(leaves):
    bn = leaves.by_name()
    read_only = _composite("c1", [{"tool": "notes_list", "args": {}},
                                  {"tool": "notes_get", "args": {"note_id": "$input.id"}}])
    with_write = _composite("c2", [{"tool": "notes_list", "args": {}},
                                   {"tool": "notes_create", "args": {}}])
    assert C.effective_flags(read_only, bn) == (False, False)
    assert C.effective_flags(with_write, bn) == (True, False)


def test_dispatch_gates_write_composite_and_refuses_without_toolset(leaves):
    comp = _composite("list_then_create", [{"tool": "notes_list", "args": {}},
                                            {"tool": "notes_create", "args": {}}])
    ts = ToolSet("p", list(leaves.tools) + [comp])
    rec = Recorder()
    gated = dispatch.execute(comp, {}, rec, toolset=ts, confirmed=False)
    assert gated["confirm_required"] and gated["composite"] and gated["steps"] == 2
    assert rec.calls == []                       # nothing ran before confirmation
    ran = dispatch.execute(comp, {}, rec, toolset=ts, confirmed=True)
    assert ran["ok"] and [c[0] for c in rec.calls] == ["notes_list", "notes_create"]
    refused = dispatch.execute(comp, {}, rec, confirmed=True)   # no toolset
    assert not refused["ok"] and "requires a toolset" in refused["error"]


# ── proposal validation ────────────────────────────────────────────────────────

def test_validate_composite_rules(leaves):
    def spec(name, steps):
        s = ToolSpec(name=name, description="d", backing={"composite": steps})
        s.write, s.danger = C.effective_flags(s, leaves.by_name())
        return s
    ok, _ = compose.validate_composite(
        spec("good", [{"tool": "notes_list", "args": {}},
                      {"tool": "notes_get", "args": {"note_id": "$steps[0].data[0].id"}}]), leaves)
    assert ok
    checks = {
        "one_step": (spec("x", [{"tool": "notes_list", "args": {}}]), "needs >= 2"),
        "danger": (spec("x", [{"tool": "notes_list", "args": {}},
                              {"tool": "notes_delete", "args": {"note_id": "1"}}]), "danger"),
        "unknown": (spec("x", [{"tool": "ghost", "args": {}},
                              {"tool": "notes_list", "args": {}}]), "unknown tool"),
        "bad_bind": (spec("x", [{"tool": "notes_list", "args": {}},
                               {"tool": "notes_get", "args": {"note_id": "$oops"}}]), "bad binding"),
    }
    for _, (s, needle) in checks.items():
        ok, why = compose.validate_composite(s, leaves)
        assert not ok and needle in why, (needle, why)
    # name collision with an existing tool (or alias)
    dup = spec("notes_list", [{"tool": "notes_list", "args": {}},
                              {"tool": "notes_get", "args": {"note_id": "$input.id"}}])
    ok, why = compose.validate_composite(dup, leaves)
    assert not ok and "already exists" in why


# ── proposal (LLM stub + deterministic fallback + history mining) ───────────────

def test_propose_uses_llm_when_available(leaves, monkeypatch):
    cand = {"name": "notes_open_first", "description": "list then detail",
            "parameters": {"type": "object", "properties": {}},
            "composite": [{"tool": "notes_list", "args": {}},
                          {"tool": "notes_get", "args": {"note_id": "$steps[0].data[0].id"}}]}
    monkeypatch.setattr(compose, "_llm_propose", lambda *a, **k: [cand, "not-a-dict"])
    accepted, rejected = compose.propose(leaves)
    assert [s.name for s, _ in accepted] == ["notes_open_first"]
    assert accepted[0][1] == "llm"
    assert any(r["why"] == "candidate is not an object" for r in rejected)


def test_propose_deterministic_fallback_pairs_list_and_detail(leaves, monkeypatch):
    monkeypatch.setattr(compose, "_llm_propose", lambda *a, **k: None)   # force no-key path
    accepted, _ = compose.propose(leaves)
    names = [s.name for s, _ in accepted]
    assert "notes_get_first" in names
    spec = next(s for s, _ in accepted if s.name == "notes_get_first")
    steps = spec.backing["composite"]
    assert steps[0]["tool"] == "notes_list"
    assert steps[1] == {"tool": "notes_get", "args": {"note_id": "$steps[0].data[0].id"}}
    assert not spec.write and not spec.danger


def test_read_history_chains_aggregates(tmp_path):
    from any2agent.evals import history as H
    sd = str(tmp_path / "state")
    rep = lambda chains: {"results": [{"metrics": {"chain": c}} for c in chains]}
    H.append(sd, {"rate": 1.0}, )  # a run with no chains
    H.append(sd, rep([["notes_list", "notes_get"], ["health_get"]]))
    H.append(sd, rep([["notes_list", "notes_get"]]))
    chains = compose.read_history_chains(sd)
    assert chains and chains[0][0] == ["notes_list", "notes_get"] and chains[0][1] == 2


# ── compatibility with existing code paths ─────────────────────────────────────

def test_verifier_accuracy_accepts_valid_and_flags_bad_composite(leaves):
    from any2agent import verifier as V
    good = _composite("notes_open_first", [{"tool": "notes_list", "args": {}},
                                           {"tool": "notes_get", "args": {"note_id": "$steps[0].data[0].id"}}])
    ts = ToolSet("p", list(leaves.tools) + [good])
    assert V.accuracy(ts)["passed"], "a structurally sound composite must not fail accuracy"
    bad = _composite("bad_del", [{"tool": "notes_list", "args": {}},
                                 {"tool": "notes_delete", "args": {"note_id": "1"}}])
    ts2 = ToolSet("p", list(leaves.tools) + [bad])
    rep = V.accuracy(ts2)
    assert not rep["passed"] and any(b["name"] == "bad_del" and "danger" in b["why"] for b in rep["bad"])


def test_verifier_liveness_skips_composites(leaves):
    from any2agent import verifier as V
    comp = _composite("notes_open_first", [{"tool": "notes_list", "args": {}},
                                           {"tool": "notes_get", "args": {"note_id": "$input.id"}}])
    ts = ToolSet("p", list(leaves.tools) + [comp])
    rec = Recorder({"notes_list": {"ok": True, "status": 200, "data": []},
                    "health_get": {"ok": True, "status": 200, "data": {}}})
    rep = V.liveness(ts, rec)
    entry = next(r for r in rep["results"] if r["name"] == "notes_open_first")
    assert entry["status"] == "unprobed" and entry["reason"] == "composite"
    assert "notes_open_first" not in [c[0] for c in rec.calls]   # never smoke-called


def test_tasks_validate_accepts_composite_reference(leaves):
    from any2agent.evals import tasks as T
    from any2agent.evals.model import EvalTask
    comp = _composite("notes_open_first", [{"tool": "notes_list", "args": {}},
                                           {"tool": "notes_get", "args": {"note_id": "$input.id"}}])
    ts = ToolSet("p", list(leaves.tools) + [comp])
    task = EvalTask(id="t", prompt="open the first note",
                    expected_tools=[["notes_open_first"]],
                    checks=[{"type": "tool_called", "any_of": ["notes_open_first"]}])
    valid, invalid = T.validate([task], ts)
    assert valid and not invalid


def test_shape_does_not_promote_composite(leaves):
    from any2agent import shape
    comp = _composite("notes_open_first", [{"tool": "notes_list", "args": {}},
                                           {"tool": "notes_get", "args": {"note_id": "$input.id"}}])
    ts = ToolSet("p", [comp])
    shape.apply(ts)
    assert "limit" not in comp.parameters["properties"]   # not treated as a list tool


# ── approval UX + CLI ──────────────────────────────────────────────────────────

def test_approve_interactive_dry_run_never_mutates(leaves):
    comp = _composite("notes_open_first", [{"tool": "notes_list", "args": {}},
                                           {"tool": "notes_get", "args": {"note_id": "$input.id"}}])
    n0 = len(leaves.tools)
    adopted = compose.approve_interactive([(comp, "pair")], leaves, dry_run=True,
                                          in_fn=lambda *_: "y", out=lambda *_: None)
    assert adopted == [] and len(leaves.tools) == n0


def test_approve_interactive_yes_appends(leaves):
    comp = _composite("notes_open_first", [{"tool": "notes_list", "args": {}},
                                           {"tool": "notes_get", "args": {"note_id": "$input.id"}}])
    adopted = compose.approve_interactive([(comp, "pair")], leaves, dry_run=False,
                                          in_fn=lambda *_: "y", out=lambda *_: None)
    assert [s.name for s in adopted] == ["notes_open_first"]
    assert leaves.by_name()["notes_open_first"].name == "notes_open_first"


def test_run_compose_dry_run_leaves_toolspec_untouched(tmp_path, monkeypatch, leaves):
    monkeypatch.chdir(tmp_path)
    from any2agent.config import AgentConfig
    monkeypatch.setattr(compose, "_llm_propose", lambda *a, **k: None)  # deterministic fallback
    cfg = AgentConfig(project="notes-api", base_url="http://t")
    cfg.save()
    leaves.save(cfg.toolspec_path())
    before = open(cfg.toolspec_path()).read()
    compose.run_compose(SimpleNamespace(project="notes-api", n=6, model=None, dry_run=True))
    assert open(cfg.toolspec_path()).read() == before
    assert not (tmp_path / "notes-api.toolspec.precompose.json").exists()


def test_run_compose_adopts_and_backs_up(tmp_path, monkeypatch, leaves):
    monkeypatch.chdir(tmp_path)
    from any2agent.config import AgentConfig
    monkeypatch.setattr(compose, "_llm_propose", lambda *a, **k: None)
    monkeypatch.setattr("builtins.input", lambda *_: "y")
    cfg = AgentConfig(project="notes-api", base_url="http://t")
    cfg.save()
    leaves.save(cfg.toolspec_path())
    compose.run_compose(SimpleNamespace(project="notes-api", n=6, model=None, dry_run=False))
    assert (tmp_path / "notes-api.toolspec.precompose.json").exists()
    reloaded = ToolSet.load(cfg.toolspec_path())
    assert "notes_get_first" in {t.name for t in reloaded.tools}


def test_validate_rejects_nested_composite_directly(leaves):
    from any2agent.spec import ToolSpec
    inner = ToolSpec(name="inner_combo", description="",
                     backing={"composite": [{"tool": "notes_list"}, {"tool": "notes_get"}]})
    by_name = {**{t.name: t for t in leaves.tools}, "inner_combo": inner}
    outer = ToolSpec(name="outer", description="",
                     backing={"composite": [{"tool": "notes_list"}, {"tool": "inner_combo"}]})
    ok, why = C.validate(outer, by_name)
    assert not ok and "nests composite" in why


def test_success_steps_omit_intermediate_data(leaves):
    rec = Recorder({"notes_list": {"ok": True, "status": 200, "data": [{"id": 1}] * 500},
                    "notes_get": {"ok": True, "status": 200, "data": {"id": 1}}})
    spec = _composite("combo_a", [{"tool": "notes_list", "args": {}},
                       {"tool": "notes_get", "args": {"note_id": "$steps[0].data[0].id"}}])
    res = C.run(spec, {}, rec, by_name={t.name: t for t in leaves.tools})
    assert res["ok"] and res["data"] == {"id": 1}
    assert all("data" not in s for s in res["steps"]), \
        "intermediate payloads must never ride along in step records"


def test_failing_step_keeps_data_and_render_bounds_it(leaves):
    from any2agent import respond
    import json as _json
    rec = Recorder({"notes_list": {"ok": True, "status": 200, "data": [{"id": 1}]},
                    "notes_get": {"ok": False, "status": 404, "error": "http_404",
                                  "data": {"detail": "x" * 5000}}})
    spec = _composite("combo_b", [{"tool": "notes_list", "args": {}},
                       {"tool": "notes_get", "args": {"note_id": "$steps[0].data[0].id"}}])
    ts = ToolSet("p", leaves.tools + [spec])
    res = C.run(spec, {}, rec, by_name=ts.by_name())
    assert res["failed_step"] == 1 and "data" in res["steps"][1]
    out = _json.loads(respond.render(res, spec=spec, toolset=ts, cap=3000))
    assert len(_json.dumps(out)) <= 3200  # bounded (cap + envelope tolerance)
    assert "Composite step 1 (notes_get) failed" in out["hint"]
    assert "Call notes_list first" in out["hint"]  # sibling suggestion survives


def test_composite_error_hints_not_transport(leaves):
    from any2agent import respond
    base = {"ok": False, "composite": "combo", "failed_step": 1, "failed_tool": "notes_get"}
    h = respond.explain({**base, "error": "binding_error: index 0 out of range"})
    assert "binding" in h and "Could not reach" not in h
    h = respond.explain({**base, "error": "unknown_tool: ghost"})
    assert "re-run compose" in h
    h = respond.explain({**base, "error": "nested composites are not allowed"})
    assert "configuration error" in h
