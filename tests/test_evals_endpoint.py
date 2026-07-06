"""eval-console: GET /evals contract — empty, populated, per-request re-read,
and the fixes field flowing from history into the API."""
import json

import pytest
from fastapi.testclient import TestClient

from any2agent.config import AgentConfig
from any2agent.evals import history as H
from any2agent.server.app import build_app


@pytest.fixture
def client(toolset, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = AgentConfig(project="p", base_url="http://target")
    return TestClient(build_app(cfg, toolset)), cfg


def test_evals_empty_returns_200_not_evaluated(client):
    c, _ = client
    r = c.get("/evals")
    assert r.status_code == 200
    assert r.json() == {"evaluated": False, "project": "p"}


def test_evals_populated_with_fixes_and_lessons(client):
    c, cfg = client
    fixes = [{"task_id": "t1", "class": "wrong_tool", "guidance": "use get__notes"}]
    H.append(cfg.state_dir(), {"rate": 0.5, "rated": 2, "passed": False,
                               "failed": ["t1"]}, fixes=fixes)
    with open(cfg.lessons_path(), "w") as f:
        json.dump({"project": "p", "version": 1,
                   "lessons": [{"task_id": "t1", "class": "wrong_tool",
                                "guidance": "use get__notes"}]}, f)
    with open(cfg.evals_path(), "w") as f:
        json.dump({"project": "p", "version": 1, "tasks": [{"id": "t1", "prompt": "x"}]}, f)

    d = c.get("/evals").json()
    assert d["evaluated"] is True
    assert d["latest"]["fixes"] == fixes and d["latest"]["rate"] == 0.5
    assert d["lessons"][0]["guidance"] == "use get__notes"
    assert d["tasks_total"] == 1 and "0.50" in d["trend"]


def test_evals_rereads_files_per_request(client):
    c, cfg = client
    assert c.get("/evals").json()["evaluated"] is False
    # an eval run happens AFTER server start — must show up without restart
    H.append(cfg.state_dir(), {"rate": 1.0, "rated": 2, "passed": True})
    d = c.get("/evals").json()
    assert d["evaluated"] is True and d["latest"]["passed"] is True


def test_evals_ui_serves_dashboard(client):
    c, _ = client
    r = c.get("/evals/ui")
    assert r.status_code == 200
    assert "eval console" in r.text and "{{PROJECT}}" not in r.text


def test_history_append_without_fixes_keeps_old_schema(tmp_path):
    sd = str(tmp_path / "s")
    e = H.append(sd, {"rate": 1.0, "rated": 1, "passed": True})
    assert "fixes" not in e  # backward compatible: no empty-list noise