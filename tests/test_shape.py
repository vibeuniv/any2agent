"""tool-consolidation phase 1: deterministic shaping — renaming table, alias
resolution, list promotion, idempotency, conservative fallbacks."""
from any2agent import shape
from any2agent.spec import ToolSet, ToolSpec


def _tool(name, method, path, write=False, danger=False, desc=None, params=None):
    return ToolSpec(name=name, description=desc or "%s %s" % (method, path),
                    parameters={"type": "object", "properties": params or {}},
                    backing={"method": method, "path": path},
                    write=write, danger=danger)


def _notes_ts():
    return ToolSet("notes-api", [
        _tool("get__notes", "GET", "/notes"),
        _tool("get__notes_note_id", "GET", "/notes/{note_id}",
              params={"note_id": {"type": "string"}}),
        _tool("post__notes", "POST", "/notes", write=True),
        _tool("delete__notes_note_id", "DELETE", "/notes/{note_id}", write=True, danger=True,
              params={"note_id": {"type": "string"}}),
        _tool("get__health", "GET", "/health"),
    ])


def test_renaming_table_notes_api():
    ts = _notes_ts()
    res = shape.apply(ts)
    names = {t.name for t in ts.tools}
    assert names == {"notes_list", "notes_get", "notes_create", "notes_delete", "health_get"}
    assert res["renamed"] == 5 and not res["skipped"]
    # old names preserved as aliases and resolvable
    bn = ts.by_name()
    assert bn["get__notes"].name == "notes_list"
    assert bn["delete__notes_note_id"].name == "notes_delete"
    # safety flags untouched
    assert bn["notes_delete"].danger and bn["notes_create"].write


def test_action_table_put_patch_nested_and_rpc_post():
    ts = ToolSet("p", [
        _tool("put__notes_note_id", "PUT", "/notes/{note_id}"),
        _tool("patch__notes_note_id", "PATCH", "/notes/{note_id}"),
        _tool("get__users_user_id_posts", "GET", "/users/{user_id}/posts"),
        _tool("post__notes_note_id", "POST", "/notes/{note_id}", write=True),
    ])
    res = shape.apply(ts)
    # PATCH and RPC-POST both propose notes_update: the first claims the _by_
    # variant, the second conservatively keeps its old name (reported, not mangled)
    assert {t.name for t in ts.tools} == {
        "notes_replace", "notes_update_by_note_id", "users_posts_list", "post__notes_note_id"}
    assert any("collision" in s["why"] for s in res["skipped"])


def test_conservative_fallbacks():
    ts = ToolSet("p", [
        _tool("listPets", "GET", "/pets"),               # curated operationId — not mechanical
        _tool("get__", "GET", "/"),                       # no resource
    ])
    res = shape.apply(ts)
    assert {t.name for t in ts.tools} == {"listPets", "get__"}
    whys = " ".join(s["why"] for s in res["skipped"])
    assert "not a mechanical name" in whys and "no resource" in whys
    assert all(not t.aliases for t in ts.tools)


def test_collision_falls_back_to_keeping_old_name():
    # two mechanical routes that would both become pets_list
    ts = ToolSet("p", [
        _tool("get__pets", "GET", "/pets"),
        _tool("get__pets_2", "GET", "/pets/"),  # dup path variant
    ])
    res = shape.apply(ts)
    names = {t.name for t in ts.tools}
    assert len(names) == 2, "no silent merge"
    assert any("collision" in s["why"] for s in res["skipped"])


def test_list_promotion_adds_limit_and_nudge_once():
    ts = _notes_ts()
    shape.apply(ts)
    lst = ts.by_name()["notes_list"]
    assert lst.parameters["properties"]["limit"]["type"] == "integer"
    assert "Prefer filters/limit" in lst.description
    # detail/read-singleton/writes are untouched
    assert "limit" not in ts.by_name()["notes_get"].parameters["properties"]
    assert "limit" not in ts.by_name()["health_get"].parameters["properties"]
    # existing limit param is preserved, not overwritten
    ts2 = ToolSet("p", [_tool("get__pets", "GET", "/pets",
                              params={"limit": {"type": "string", "description": "legacy"}})])
    shape.apply(ts2)
    assert ts2.by_name()["pets_list"].parameters["properties"]["limit"]["description"] == "legacy"


def test_idempotent_via_meta():
    ts = _notes_ts()
    r1 = shape.apply(ts)
    r2 = shape.apply(ts)
    assert r1["renamed"] == 5 and r2 == {"renamed": 0, "promoted": 0, "skipped": [], "noop": True}
    assert ts.meta["shaping"]["renamed"]["notes_list"] == "get__notes"


def test_aliases_survive_serialization_roundtrip(tmp_path):
    ts = _notes_ts()
    shape.apply(ts)
    p = str(tmp_path / "t.json")
    ts.save(p)
    loaded = ToolSet.load(p)
    assert loaded.by_name()["get__notes"].name == "notes_list"
    assert shape.apply(loaded)["noop"] is True  # meta.shaping persisted


def test_old_name_references_still_validate_in_evals():
    from any2agent.evals import tasks as T
    from any2agent.evals.model import EvalTask
    ts = _notes_ts()
    shape.apply(ts)
    old_style = EvalTask(id="t1", prompt="list notes", expected_tools=[["get__notes"]],
                         checks=[{"type": "tool_called", "any_of": ["get__notes"]}])
    valid, invalid = T.validate([old_style], ts)
    assert valid and not invalid, "aliases must keep curated evals working"