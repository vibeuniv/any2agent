"""FR-07 migrate: rename-map building (aliases + meta.shaping.renamed), the three
rewriters (evals exact-name fields incl. nested paths, lessons whole-word guidance,
generic string-value tree), and the run_migrate orchestration — dry-run writes
nothing, real run backs up before writing, and a second run is a no-op."""
import json
import os
from types import SimpleNamespace

from any2agent import migrate as M
from any2agent.config import AgentConfig
from any2agent.spec import ToolSet, ToolSpec


def _toolset():
    """A shaped toolset: three tools whose old mechanical names live in aliases,
    mirrored in meta.shaping.renamed (new -> old)."""
    return ToolSet("notes-api", [
        ToolSpec(name="notes_list", description="list", backing={"method": "GET", "path": "/notes"},
                 aliases=["get__notes"]),
        ToolSpec(name="notes_get", description="get", backing={"method": "GET", "path": "/notes/{note_id}"},
                 aliases=["get__notes__note_id"]),
        ToolSpec(name="notes_delete", description="del", backing={"method": "DELETE", "path": "/notes/{note_id}"},
                 write=True, danger=True, aliases=["delete__notes__note_id"]),
    ], meta={"shaping": {"version": 2, "renamed": {
        "notes_list": "get__notes",
        "notes_get": "get__notes__note_id",
        "notes_delete": "delete__notes__note_id",
    }}})


# ── rename map ─────────────────────────────────────────────────────────────────

def test_build_rename_map_from_aliases_and_meta():
    assert M.build_rename_map(_toolset()) == {
        "get__notes": "notes_list",
        "get__notes__note_id": "notes_get",
        "delete__notes__note_id": "notes_delete",
    }


def test_rename_map_never_maps_a_live_canonical_name():
    # an alias that collides with a live tool name must not become an 'old' name —
    # references to it already resolve correctly, so rewriting would be wrong.
    ts = ToolSet("p", [
        ToolSpec(name="notes_list", description="", backing={}, aliases=["notes_get"]),
        ToolSpec(name="notes_get", description="", backing={}),
    ])
    assert "notes_get" not in M.build_rename_map(ts)


# ── evals.json rewrite ─────────────────────────────────────────────────────────

def test_rewrite_evals_covers_nested_paths_and_checks():
    rmap = {"get__notes": "notes_list", "get__notes__note_id": "notes_get",
            "delete__notes__note_id": "notes_delete"}
    doc = {"tasks": [{
        "id": "t1",
        "expected_tools": [["get__notes", "get__notes__note_id"], ["get__notes"]],
        "checks": [{"type": "tool_called", "any_of": ["get__notes", "notes_list"]},
                   {"type": "state", "tool": "get__notes__note_id", "args": {}}],
        "cleanup": [{"tool": "delete__notes__note_id", "args": {}}],
    }]}
    changes, samples = M.rewrite_evals_doc(doc, rmap)
    t = doc["tasks"][0]
    assert t["expected_tools"] == [["notes_list", "notes_get"], ["notes_list"]]
    assert t["checks"][0]["any_of"] == ["notes_list", "notes_list"]  # already-new name untouched
    assert t["checks"][1]["tool"] == "notes_get"
    assert t["cleanup"][0]["tool"] == "notes_delete"
    assert changes == 6 and samples  # 3 expected + 1 any_of + 1 state + 1 cleanup


# ── eval-lessons.json rewrite (whole word) ─────────────────────────────────────

def test_rewrite_lessons_is_whole_word_only():
    rmap = {"note": "annotation", "get__notes": "notes_list"}
    doc = {"lessons": [{"task_id": "a",
                        "guidance": "Use note and get__notes; not note_book or footnote."}]}
    changes, _ = M.rewrite_lessons_doc(doc, rmap)
    # standalone words replaced; substrings of longer tokens left intact
    assert doc["lessons"][0]["guidance"] == \
        "Use annotation and notes_list; not note_book or footnote."
    assert changes == 2


# ── generic tree rewrite ───────────────────────────────────────────────────────

def test_rewrite_generic_replaces_exact_string_values_only():
    rmap = {"get__notes": "notes_list", "get__notes__note_id": "notes_get"}
    node = {"pipeline": ["get__notes", "keep_me"],
            "cfg": {"tool": "get__notes__note_id", "note": "get__notes is fine here"},
            "get__notes": "as-a-key-stays"}  # dict KEYS are never rewritten
    out, changes = M.rewrite_generic(node, rmap, [])
    assert out["pipeline"] == ["notes_list", "keep_me"]
    assert out["cfg"]["tool"] == "notes_get"
    assert out["cfg"]["note"] == "get__notes is fine here"  # not an exact match → untouched
    assert "get__notes" in out  # key preserved
    assert changes == 2


# ── run_migrate orchestration ──────────────────────────────────────────────────

def _write_project(tmp_path):
    cfg = AgentConfig(project="notes-api", base_url="http://t")
    cfg.save(str(tmp_path / cfg.config_path()))
    _toolset().save(str(tmp_path / cfg.toolspec_path()))
    (tmp_path / "notes-api.evals.json").write_text(json.dumps({
        "project": "notes-api", "version": 1, "tasks": [{
            "id": "t1", "prompt": "open first note", "kind": "read",
            "expected_tools": [["get__notes", "get__notes__note_id"]],
            "checks": [{"type": "tool_called", "any_of": ["get__notes"]}],
            "cleanup": []}]}))
    (tmp_path / "notes-api.eval-lessons.json").write_text(json.dumps({
        "project": "notes-api", "version": 1, "lessons": [{
            "task_id": "t1", "class": "wrong_tool", "when": "x",
            "guidance": "For such requests use get__notes, not get__notes__note_id."}]}))


def test_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write_project(tmp_path)
    before = {p: (tmp_path / p).read_text() for p in
              ("notes-api.evals.json", "notes-api.eval-lessons.json")}
    code = M.run_migrate(SimpleNamespace(project="notes-api", dry_run=True, files=None))
    assert code == 0
    for p, txt in before.items():
        assert (tmp_path / p).read_text() == txt
    assert not (tmp_path / "notes-api.evals.json.premigrate.bak").exists()
    assert "dry-run" in capsys.readouterr().out


def test_real_run_backs_up_then_rewrites(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_project(tmp_path)
    orig = (tmp_path / "notes-api.evals.json").read_text()
    code = M.run_migrate(SimpleNamespace(project="notes-api", dry_run=False, files=None))
    assert code == 0
    # backups hold the pre-migration content
    assert (tmp_path / "notes-api.evals.json.premigrate.bak").read_text() == orig
    assert (tmp_path / "notes-api.eval-lessons.json.premigrate.bak").exists()
    # evals rewritten to current names
    doc = json.loads((tmp_path / "notes-api.evals.json").read_text())
    assert doc["tasks"][0]["expected_tools"] == [["notes_list", "notes_get"]]
    assert doc["tasks"][0]["checks"][0]["any_of"] == ["notes_list"]
    # lessons whole-word rewritten
    g = json.loads((tmp_path / "notes-api.eval-lessons.json").read_text())["lessons"][0]["guidance"]
    assert "notes_list" in g and "notes_get" in g and "get__notes" not in g


def test_second_run_is_idempotent_no_new_backup(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write_project(tmp_path)
    M.run_migrate(SimpleNamespace(project="notes-api", dry_run=False, files=None))
    os.remove(tmp_path / "notes-api.evals.json.premigrate.bak")
    os.remove(tmp_path / "notes-api.eval-lessons.json.premigrate.bak")
    capsys.readouterr()
    code = M.run_migrate(SimpleNamespace(project="notes-api", dry_run=False, files=None))
    assert code == 0
    # nothing left to change → no fresh backups written
    assert not (tmp_path / "notes-api.evals.json.premigrate.bak").exists()
    assert "0 reference(s) rewritten" in capsys.readouterr().out


def test_generic_files_and_missing_file_are_honest(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _write_project(tmp_path)
    (tmp_path / "curated.json").write_text(json.dumps(
        {"pipeline": ["get__notes", "keep_me"], "cfg": {"tool": "get__notes__note_id"}}))
    code = M.run_migrate(SimpleNamespace(project="notes-api", dry_run=False,
                                         files="curated.json,does_not_exist.json"))
    assert code == 0
    assert json.loads((tmp_path / "curated.json").read_text()) == \
        {"pipeline": ["notes_list", "keep_me"], "cfg": {"tool": "notes_get"}}
    assert "does_not_exist.json — not found" in capsys.readouterr().out


def test_missing_config_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code = M.run_migrate(SimpleNamespace(project="ghost", dry_run=False, files=None))
    assert code == 2
    assert "not found" in capsys.readouterr().err


def test_missing_toolspec_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    AgentConfig(project="notes-api", base_url="http://t").save(str(tmp_path / "notes-api.any2agent.toml"))
    code = M.run_migrate(SimpleNamespace(project="notes-api", dry_run=False, files=None))
    assert code == 2
    assert "not found" in capsys.readouterr().err
