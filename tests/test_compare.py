"""eval --compare: verdict branching (keep / revert / tie) and corrupt-file
handling — the A/B gate for tool-shaping changes."""
import json
from types import SimpleNamespace

import pytest

from any2agent import cli, verifier
from any2agent.config import AgentConfig
from any2agent.core import registry
from any2agent.evals import tasks as T
from any2agent.evals.model import EvalTask


def _rep(rate, avg_tools, passed=True):
    return {"passed": passed, "rate": rate, "threshold": 0.8, "rated": 2,
            "results": [], "failed": [], "skipped_write": 0, "skipped_budget": 0,
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
                           json=None, history=False, fix=False, compare="old.toolspec.json")
    with pytest.raises(SystemExit) as e:
        cli.cmd_eval(args)
    return e.value.code, capsys.readouterr().out


def test_verdict_keep(project, monkeypatch, capsys):
    code, out = _run_compare(monkeypatch, _rep(0.80, 3.0), _rep(0.88, 2.0), capsys)
    assert "✅ non-inferior rate AND no more calls" in out and code == 0


def test_verdict_revert_on_rate_regression(project, monkeypatch, capsys):
    code, out = _run_compare(monkeypatch, _rep(0.90, 2.0), _rep(0.70, 1.5, passed=False), capsys)
    assert "❌ completion rate regressed" in out and code == 1


def test_verdict_tie_when_calls_grew(project, monkeypatch, capsys):
    _, out = _run_compare(monkeypatch, _rep(0.85, 1.5), _rep(0.85, 2.5), capsys)
    assert "⚠ rate held but call count grew" in out


def test_compare_missing_and_corrupt_files_exit_2(project, monkeypatch, capsys):
    base = SimpleNamespace(project="p", n=8, regen=False, live_write=False, yes=True,
                           model=None, judge_model=None, threshold=0.8, budget=None,
                           json=None, history=False, fix=False)
    with pytest.raises(SystemExit) as e:
        cli.cmd_eval(SimpleNamespace(**{**base.__dict__, "compare": "nope.json"}))
    assert e.value.code == 2
    with open("corrupt.json", "w") as f:
        f.write("{not json")
    with pytest.raises(SystemExit) as e:
        cli.cmd_eval(SimpleNamespace(**{**base.__dict__, "compare": "corrupt.json"}))
    assert e.value.code == 2
