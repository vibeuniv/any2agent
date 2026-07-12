"""eval --compare: paired McNemar verdict (better / worse / no-diff /
inconclusive) and corrupt-file handling — the A/B gate for tool-set changes."""
from types import SimpleNamespace

import pytest

from any2agent import cli, verifier
from any2agent.config import AgentConfig
from any2agent.core import registry
from any2agent.evals import tasks as T
from any2agent.evals.model import EvalTask


def _rep(results, rate, avg_tools=2.0, passed=True):
    """results: list of (task_id, success) — the per-task outcomes McNemar pairs on."""
    res = [{"task_id": t, "success": s, "ungraded": False, "reasons": [],
            "checks_passed": 0, "checks_total": 0,
            "metrics": {"tool_calls": 1, "called": [], "bad_calls": []}} for t, s in results]
    return {"passed": passed, "rate": rate, "threshold": 0.8, "rated": len(results),
            "rate_ci": [0.0, 1.0], "underpowered": False, "add_tasks_for_power": 0,
            "strict": False, "results": res,
            "failed": [t for t, s in results if not s], "skipped_write": 0, "skipped_budget": 0,
            "infra_errors": 0, "ungraded": 0, "residue": [],
            "metrics": {"avg_tool_calls": avg_tools, "wrong_tool_calls": 0, "tool_errors": 0}}


@pytest.fixture
def project(toolset, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = AgentConfig(project="p", base_url="http://target")
    cfg.save()
    toolset.save(cfg.toolspec_path())
    toolset.save("old.toolspec.json")
    T.save(cfg.evals_path(), "p",
           [EvalTask(id="t1", prompt="list notes", expected_tools=[["get__notes"]])])
    monkeypatch.setattr(registry, "llm_available", lambda: True)
    return cfg


def _run_compare(monkeypatch, old_rep, new_rep, capsys):
    reps = [old_rep, new_rep]  # cmd_eval runs OLD first, then CURRENT
    monkeypatch.setattr(verifier, "task_eval", lambda *a, **k: reps.pop(0))
    args = SimpleNamespace(project="p", n=8, regen=False, live_write=False, yes=True,
                           model=None, judge_model=None, threshold=0.8, budget=None,
                           strict=False, judge_votes=1, json=None, history=False, fix=False,
                           compare="old.toolspec.json")
    with pytest.raises(SystemExit) as e:
        cli.cmd_eval(args)
    return e.value.code, capsys.readouterr().out


def _tasks(passes):  # passes: list of bool
    return [("t%d" % i, p) for i, p in enumerate(passes)]


def test_verdict_significantly_better(project, monkeypatch, capsys):
    # 5 tasks old-failed→new-passed, 0 the other way: c=5,b=0 → McNemar p≈0.06? need bigger
    old = _rep(_tasks([False] * 6 + [True] * 2), 0.25)
    new = _rep(_tasks([True] * 6 + [True] * 2), 1.0)   # 6 flipped to pass, 0 back
    _, out = _run_compare(monkeypatch, old, new, capsys)
    assert "✅ new toolset significantly better" in out


def test_verdict_significantly_worse(project, monkeypatch, capsys):
    old = _rep(_tasks([True] * 6 + [False] * 2), 0.75)
    new = _rep(_tasks([False] * 6 + [False] * 2), 0.0, passed=False)  # 6 regressed
    code, out = _run_compare(monkeypatch, old, new, capsys)
    assert "❌ new toolset significantly worse" in out and code == 1


def test_verdict_inconclusive_when_few_changed(project, monkeypatch, capsys):
    # only 1 task changed verdict → not enough signal
    old = _rep(_tasks([True, True, True, False]), 0.75)
    new = _rep(_tasks([True, True, True, True]), 1.0)
    _, out = _run_compare(monkeypatch, old, new, capsys)
    assert "🤷 inconclusive" in out


def test_verdict_no_significant_difference(project, monkeypatch, capsys):
    # balanced discordant (2 worse, 2 better) → symmetric, no signal
    old = _rep(_tasks([True, True, False, False, True, True]), 0.67)
    new = _rep(_tasks([False, False, True, True, True, True]), 0.67)
    _, out = _run_compare(monkeypatch, old, new, capsys)
    assert "➖ no significant difference" in out


def test_compare_missing_and_corrupt_files_exit_2(project, monkeypatch):
    base = dict(project="p", n=8, regen=False, live_write=False, yes=True,
                model=None, judge_model=None, threshold=0.8, budget=None,
                strict=False, judge_votes=1, json=None, history=False, fix=False)
    with pytest.raises(SystemExit) as e:
        cli.cmd_eval(SimpleNamespace(**{**base, "compare": "nope.json"}))
    assert e.value.code == 2
    with open("corrupt.json", "w") as f:
        f.write("{not json")
    with pytest.raises(SystemExit) as e:
        cli.cmd_eval(SimpleNamespace(**{**base, "compare": "corrupt.json"}))
    assert e.value.code == 2
