"""Shared fixtures: a small toolset mirroring examples/notes-api, a canned-
response stub adapter, and a fresh eval budget per test (module-global state).
"""
import pytest

from any2agent.spec import ToolSet, ToolSpec
from any2agent.evals import budget


def _tool(name, method, path, write=False, danger=False, params=None, required=None):
    p = {"type": "object", "properties": params or {}}
    if required:
        p["required"] = required
    return ToolSpec(name=name, description="%s %s" % (method, path),
                    parameters=p, backing={"method": method, "path": path},
                    write=write, danger=danger, domain="notes")


@pytest.fixture
def toolset():
    return ToolSet("notes-api", [
        _tool("get__notes", "GET", "/notes"),
        _tool("get__notes_note_id", "GET", "/notes/{note_id}",
              params={"note_id": {"type": "string"}}, required=["note_id"]),
        _tool("post__notes", "POST", "/notes", write=True,
              params={"title": {"type": "string"}}),
        _tool("delete__notes_note_id", "DELETE", "/notes/{note_id}", write=True, danger=True,
              params={"note_id": {"type": "string"}}, required=["note_id"]),
        _tool("get__health", "GET", "/health"),
    ])


class StubAdapter:
    """Canned-response adapter; records calls for assertions."""

    def __init__(self, responses=None, default=None):
        self.responses = responses or {}          # tool name -> result dict
        self.default = default or {"ok": True, "status": 200, "data": {}}
        self.calls = []

    def call(self, spec, args, ctx):
        self.calls.append((spec.name, dict(args or {}), dict(ctx or {})))
        return dict(self.responses.get(spec.name, self.default))


@pytest.fixture
def stub_adapter():
    return StubAdapter()


@pytest.fixture(autouse=True)
def fresh_budget():
    budget.reset(40)
    yield
    budget.reset(40)
