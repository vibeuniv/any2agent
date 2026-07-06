"""eval-feedback: failure classification, lesson lifecycle, history/trend,
runtime guidance injection, and the --fix repair path."""
import json
from types import SimpleNamespace

import pytest

from any2agent.evals import history as H
from any2agent.evals import lessons as L
from any2agent.evals.model import EvalTask
from any2agent.core import agent as core_agent
from any2agent.spec import ToolSet


def _result(task_id="t1", success=False, reasons=None, metrics=None, ungraded=False):
    return {"task_id": task_id, "success": success, "ungraded": ungraded,
            "reasons": reasons or [], "metrics": metrics or {}}


# ── classify ─────────────────────────────────────────────────────────────────

def test_classify_all_classes_and_priority():
    assert L.classify(_result(reasons=["expected tools not covered (called: x)"])) == "wrong_tool"
    assert L.classify(_result(reasons=["attempted write tool: post__notes"])) == "wrong_tool"
    assert L.classify(_result(metrics={"bad_calls": [{"tool": "t", "status": 422}]})) == "bad_args"
    assert L.classify(_result(reasons=["no_errors failed: t(500)"])) == "tool_error"
    assert L.classify(_result(reasons=["state check failed: 'x' not in notes_list response"])) == "state_mismatch"
    assert L.classify(_result(reasons=["judge: answer not grounded"])) == "answer_gap"
    assert L.classify(_result(reasons=["something odd"])) == "other"
    # wrong_tool wins over downstream check failures it caused
    assert L.classify(_result(reasons=["expected tools not covered (called: x)",
                                       "state check failed: y"])) == "wrong_tool"


# ── build / merge_save lifecycle ─────────────────────────────────────────────

def test_build_skips_passes_ungraded_and_runner_failures(toolset):
    task = EvalTask(id="t1", prompt="list my notes", expected_tools=[["get__notes"]])
    rep = {"results": [
        _result("t1", reasons=["expected tools not covered (called: post__notes)"],
                metrics={"called": ["post__notes"]}),
        _result("t2", success=True),
        _result("t3", ungraded=True),
        _result("t4", reasons=["runner: LLM call error"]),
    ]}
    built = L.build(rep, {"t1": task})
    assert [l["task_id"] for l in built] == ["t1"]
    assert built[0]["class"] == "wrong_tool"
    assert "get__notes" in built[0]["guidance"]


def test_merge_save_upsert_pass_removal_and_stale_tools(toolset, tmp_path):
    path = str(tmp_path / "p.eval-lessons.json")
    l_old = {"task_id": "t1", "class": "wrong_tool", "when": "w",
             "guidance": "use get__notes for listing"}
    l_stale = {"task_id": "t9", "class": "wrong_tool", "when": "w",
               "guidance": "use ghost_tool for x"}
    L.merge_save(path, "p", [l_old, l_stale], [], toolset)
    kept = L.load(path)
    assert [l["task_id"] for l in kept] == ["t1"], "stale tool reference must be dropped"
    # the task now passes -> its lesson is removed
    kept = L.merge_save(path, "p", [], ["t1"], toolset)
    assert kept == []


def test_merge_save_caps_at_max_lessons(toolset, tmp_path):
    path = str(tmp_path / "p.eval-lessons.json")
    many = [{"task_id": "t%d" % i, "class": "other", "when": "w",
             "guidance": "guidance number %d" % i} for i in range(L.MAX_LESSONS + 5)]
    kept = L.merge_save(path, "p", many, [], toolset)
    assert len(kept) == L.MAX_LESSONS
    assert kept[-1]["task_id"] == "t%d" % (L.MAX_LESSONS + 4)  # newest survive


def test_merge_save_survives_corrupt_file(toolset, tmp_path):
    path = str(tmp_path / "p.eval-lessons.json")
    with open(path, "w") as f:
        f.write("{not json")
    assert L.load(path) == []
    kept = L.merge_save(path, "p", [{"task_id": "t1", "class": "other", "when": "w",
                                     "guidance": "plain guidance"}], [], toolset)
    assert len(kept) == 1 and json.load(open(path))["lessons"]


# ── history ──────────────────────────────────────────────────────────────────

def test_history_append_load_trend(tmp_path):
    sd = str(tmp_path / "state")
    assert H.trend_line([]) == ""
    H.append(sd, {"rate": 0.5, "rated": 4, "passed": False, "failed": ["a"]})
    entries = H.load(sd)
    assert len(entries) == 1 and "first recorded run" in H.trend_line(entries)
    H.append(sd, {"rate": 0.75, "rated": 4, "passed": False, "failed": ["a"]})
    line = H.trend_line(H.load(sd))
    assert "0.75" in line and "▲" in line and "0.25" in line


def test_history_skips_corrupt_lines(tmp_path):
    sd = str(tmp_path / "state")
    H.append(sd, {"rate": 1.0, "rated": 2, "passed": True})
    with open(H.path(sd), "a") as f:
        f.write("garbage\n")
    assert len(H.load(sd)) == 1


# ── runtime injection ────────────────────────────────────────────────────────

def test_inject_lessons_prepends_system_note():
    msgs = [{"role": "user", "content": "hi"}]
    out = core_agent._inject_lessons(msgs, ["use notes_get for detail lookups"])
    assert out[0]["role"] == "system"
    assert "use notes_get for detail lookups" in out[0]["content"]
    assert "never overrides confirmation or authorization" in out[0]["content"]
    assert core_agent._inject_lessons(msgs, []) is msgs  # no-op without lessons


def test_render_empty_and_nonempty():
    assert L.render([]) == ""
    txt = L.render([{"guidance": "g1"}, {"guidance": "g2"}])
    assert "- g1" in txt and "- g2" in txt


def test_multi_tool_guidance_survives_stale_filter(toolset):
    # regression (found by the first real-key run): a wrong_tool lesson naming
    # SEVERAL expected tools must not be dropped by the stale filter
    task = EvalTask(id="t1", prompt="list then read",
                    expected_tools=[["get__notes", "get__notes_note_id"]])
    rep = {"results": [{"task_id": "t1", "success": False, "ungraded": False,
                        "reasons": ["expected tools not covered (called: get__notes)"],
                        "metrics": {"called": ["get__notes"]}}]}
    built = L.build(rep, {"t1": task})
    kept = L.merge_save(str(__import__("tempfile").mkdtemp()) + "/l.json", "p",
                        built, [], toolset)
    assert kept, "multi-tool guidance must survive persistence"


def test_stale_detection_resolves_aliases(toolset):
    # a lesson written pre-shaping (old tool name) must survive stale filtering
    toolset.tools[0].aliases.append("legacy__notes")
    lesson = {"task_id": "t1", "class": "wrong_tool", "when": "w",
              "guidance": "use legacy__notes for listing"}
    names = set(toolset.by_name().keys())   # includes aliases
    assert L._references_known_tools(lesson, names)


# ── eval --fix saves repaired toolspec ───────────────────────────────────────

def test_eval_fix_saves_toolspec(toolset, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from any2agent import cli, verifier, connect
    from any2agent.config import AgentConfig
    from any2agent.core import registry
    from any2agent.evals import tasks as T

    cfg = AgentConfig(project="p", base_url="http://target")
    cfg.save()
    toolset.save(cfg.toolspec_path())
    T.save(cfg.evals_path(), "p",
           [EvalTask(id="t1", prompt="list notes", expected_tools=[["get__notes"]])])

    monkeypatch.setattr(registry, "llm_available", lambda: True)
    failed_rep = {"passed": False, "rate": 0.0, "threshold": 0.8, "rated": 1,
                  "results": [{"task_id": "t1", "success": False, "ungraded": False,
                               "reasons": ["expected tools not covered (called: post__notes)"],
                               "checks_passed": 0, "checks_total": 0,
                               "metrics": {"tool_calls": 1, "called": ["post__notes"],
                                           "bad_calls": []}}],
                  "failed": ["t1"], "skipped_write": 0, "skipped_budget": 0,
                  "infra_errors": 0, "ungraded": 0, "residue": [],
                  "metrics": {"avg_tool_calls": 1, "wrong_tool_calls": 1, "tool_errors": 0}}
    monkeypatch.setattr(verifier, "task_eval", lambda *a, **k: failed_rep)

    def fake_repair(ts, rep, by_id):
        ts.tools[0].description = "REPAIRED"
        return 1
    monkeypatch.setattr(connect, "_eval_repair", fake_repair)

    args = SimpleNamespace(project="p", n=8, regen=False, live_write=False, yes=True,
                           model=None, judge_model=None, threshold=0.8, budget=None,
                           json=None, history=False, fix=True)
    with pytest.raises(SystemExit) as e:
        cli.cmd_eval(args)
    assert e.value.code == 1, "gate still fails this run — fix asks for a re-run"
    assert ToolSet.load("p.toolspec.json").tools[0].description == "REPAIRED"
