"""response-shaping: structure-aware truncation, concise/detailed, error hints,
render validity guarantees, response_format promotion + runtime pop."""
import json

from any2agent import respond, shape
from any2agent.spec import ToolSet, ToolSpec
from any2agent.core import agent as core_agent


def _items(n, extra=None):
    return [{"id": i, "title": "note %d" % i, "body": None, "tags": [],
             **(extra or {})} for i in range(n)]


# ── shape() ──────────────────────────────────────────────────────────────────

def test_concise_truncates_and_drops_empty_fields():
    shaped, notes, trunc = respond.shape(_items(30), mode="concise")
    assert trunc == [{"shown": 10, "total": 30}]
    assert len(shaped) == 10
    assert "body" not in shaped[0] and "tags" not in shaped[0]  # null/empty dropped
    assert shaped[0]["id"] == 0 and shaped[0]["title"] == "note 0"
    assert any("truncated to 10 of 30" in n for n in notes)


def test_detailed_keeps_fields_wider_budget():
    shaped, notes, _ = respond.shape(_items(60), mode="detailed")
    assert len(shaped) == 50
    assert "body" in shaped[0], "detailed keeps all fields (ids for follow-ups)"
    assert notes


def test_nested_and_long_strings():
    data = {"wrapper": {"rows": _items(15)}, "blob": "x" * 900}
    shaped, notes, _ = respond.shape(data, mode="concise")
    assert len(shaped["wrapper"]["rows"]) == 10
    assert shaped["blob"].endswith("…[truncated]") and len(shaped["blob"]) < 900
    assert any("long text" in n for n in notes)


def test_no_truncation_no_notes():
    shaped, notes, trunc = respond.shape(_items(3), mode="concise")
    assert len(shaped) == 3 and notes == [] and trunc == []


# ── render() success path ────────────────────────────────────────────────────

def test_render_always_valid_json_with_meta_hint():
    out = respond.render({"ok": True, "status": 200, "data": _items(40)})
    d = json.loads(out)
    assert d["data"]["_meta"]["hint"].startswith("list truncated")
    assert d["data"]["_meta"]["truncated"] == {"shown": 10, "total": 40}
    assert len(d["data"]["items"]) == 10


def test_render_halves_until_fit_never_slices():
    big = [{"id": i, "text": "y" * 400} for i in range(50)]
    out = respond.render({"ok": True, "status": 200, "data": big}, cap=3000)
    d = json.loads(out)  # must parse — the old code would slice mid-structure
    assert len(d["data"]["items"]) < 10


def test_render_omits_as_last_resort():
    huge = {"blob1": "z" * 3000, "blob2": "z" * 3000, "blob3": "z" * 3000}
    out = respond.render({"ok": True, "status": 200, "data": [huge]}, cap=500)
    d = json.loads(out)
    assert d["data"]["_meta"]["omitted"] is True


# ── explain() error hints ────────────────────────────────────────────────────

def _shaped_ts():
    ts = ToolSet("p", [
        ToolSpec(name="get__notes", description="", backing={"method": "GET", "path": "/notes"}),
        ToolSpec(name="get__notes_note_id", description="",
                 parameters={"type": "object", "properties": {"note_id": {"type": "string"}}},
                 backing={"method": "GET", "path": "/notes/{note_id}"}),
    ])
    shape.apply(ts)
    return ts


def test_404_suggests_sibling_reader_on_shaped_names():
    ts = _shaped_ts()
    spec = ts.by_name()["notes_get"]
    hint = respond.explain({"ok": False, "status": 404}, spec, ts)
    assert "Call notes_list first" in hint


def test_404_no_false_suggestion_on_mechanical_names(toolset):
    spec = toolset.by_name()["get__notes_note_id"]  # unshaped fixture
    hint = respond.explain({"ok": False, "status": 404}, spec, toolset)
    assert "Call " not in hint and "not found" in hint


def test_hint_table_classes():
    e = respond.explain
    assert "re-check required parameters" in e({"ok": False, "status": 422, "data": {"detail": "bad"}})
    assert "RBAC" in e({"ok": False, "status": 403})
    assert "Method not allowed" in e({"ok": False, "status": 405})
    assert "Rate limited" in e({"ok": False, "status": 429})
    assert "failed internally" in e({"ok": False, "status": 503})
    assert "Could not reach" in e({"ok": False, "error": "Connection refused"})


def test_render_attaches_hint_and_bounds_error_body():
    out = respond.render({"ok": False, "status": 422, "error": "http_422",
                          "data": {"detail": "x" * 2000}})
    d = json.loads(out)
    assert d["hint"].startswith("The arguments were rejected")
    assert len(json.dumps(d["data"])) < 1200  # error body shaped too


# ── integration: shape.py promotion + agent pop ──────────────────────────────

def test_shape_v2_promotes_response_format():
    ts = _shaped_ts()
    props = ts.by_name()["notes_list"].parameters["properties"]
    assert props["response_format"]["enum"] == ["concise", "detailed"]
    assert "response_format" not in ts.by_name()["notes_get"].parameters["properties"]


def test_shape_v1_toolspec_gets_v2_promotion_without_rename_noise():
    ts = _shaped_ts()
    ts.meta["shaping"] = {"version": 1, "renamed": ts.meta["shaping"]["renamed"]}
    # strip v2 additions to simulate a v1 artifact
    ts.by_name()["notes_list"].parameters["properties"].pop("response_format")
    res = shape.apply(ts)
    assert res.get("noop") is not True
    assert "response_format" in ts.by_name()["notes_list"].parameters["properties"]
    assert res["renamed"] == 0
    # our own previously-shaped names must not appear as skipped noise
    assert res["skipped"] == []
    assert ts.meta["shaping"]["version"] == 2
    assert ts.meta["shaping"]["renamed"]["notes_list"] == "get__notes"  # audit trail kept


def test_tool_msg_valid_json_and_format_pop(toolset):
    msg = core_agent._tool_msg(0, "notes_list",
                               {"ok": True, "status": 200, "data": _items(40)},
                               spec=None, toolset=toolset, response_format="detailed")
    d = json.loads(msg["content"])
    assert len(d["data"]) == 40, "detailed budget is 50 — 40 items pass untruncated"


def test_confirm_and_run_pops_response_format(toolset):
    calls = {}

    class Spy:
        def call(self, spec, args, ctx):
            calls["args"] = dict(args)
            return {"ok": True, "status": 200, "data": {}}

    core_agent.confirm_and_run("post__notes", {"title": "x", "response_format": "concise"},
                               toolset, Spy())
    assert "response_format" not in calls["args"], "must never reach the backend"
    assert calls["args"] == {"title": "x"}


def test_unknown_tool_hint_not_transport():
    hint = respond.explain({"ok": False, "error": "unknown_tool"})
    assert "search_tools" in hint and "Could not reach" not in hint
    # real transport failures still get the transport hint
    assert "Could not reach" in respond.explain({"ok": False, "error": "Connection refused"})