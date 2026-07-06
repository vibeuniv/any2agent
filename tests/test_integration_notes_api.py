"""Integration: RestAdapter + dispatch + grader state check against a real local
HTTP server (stdlib, no LLM needed) — the deterministic slice of the harness.
The full LLM-driven slice (`any2agent eval` on examples/notes-api) needs a
provider key and a running uvicorn; it is exercised manually / in CI with keys.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from any2agent.adapters.rest import RestAdapter
from any2agent.evals import grader as G
from any2agent.evals import runner as R
from any2agent.evals.model import EvalTask, EvalTrace

NOTES = [{"id": 1, "title": "[a2a-eval] integration note"}]


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/notes":
            body = json.dumps(NOTES).encode()
            self.send_response(200)
        elif self.path == "/notes/1":
            body = json.dumps(NOTES[0]).encode()
            self.send_response(200)
        else:
            body = b'{"detail":"not found"}'
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"deleted": 1}')

    def log_message(self, *a):  # keep pytest output clean
        pass


@pytest.fixture(scope="module")
def live_server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()


def test_state_check_against_live_server(toolset, live_server, monkeypatch):
    monkeypatch.setattr(G.registry, "resolve", lambda *a, **k: (None, None, None))
    adapter = RestAdapter(live_server, {"type": "none"})
    task = EvalTask(id="it", prompt="p",
                    checks=[{"type": "state", "tool": "get__notes", "args": {},
                             "expect_contains": "[a2a-eval] integration note"}])
    trace = EvalTrace(task_id="it", steps=[{"tool": "get__notes", "args": {},
                                            "ok": True, "status": 200, "error": ""}])
    r = G.grade(task, trace, toolset, adapter)
    assert r.success, r.reasons


def test_cleanup_against_live_server(toolset, live_server):
    adapter = RestAdapter(live_server, {"type": "none"})
    task = EvalTask(id="it", prompt="p", kind="write",
                    cleanup=[{"tool": "delete__notes_note_id", "args": {"note_id": "1"}}])
    assert R.run_cleanup(task, toolset, adapter) == []
    # a failing cleanup surfaces as residue, honestly
    task.cleanup = [{"tool": "get__notes_note_id", "args": {"note_id": "999"}}]
    residue = R.run_cleanup(task, toolset, adapter)
    assert residue and residue[0]["why"] == "http_404"
