"""Code-leak opt-out (no source to the LLM) and the confused-deputy network
bind guard."""
import pytest

from any2agent.config import AgentConfig, llm_source_allowed
from any2agent.server import app as server_app


def test_llm_source_opt_out(monkeypatch):
    monkeypatch.delenv("ANY2AGENT_NO_LLM_SOURCE", raising=False)
    assert llm_source_allowed() is True
    for v in ("1", "true", "yes"):
        monkeypatch.setenv("ANY2AGENT_NO_LLM_SOURCE", v)
        assert llm_source_allowed() is False


def test_auth_layer3_skipped_when_source_disabled(tmp_path, monkeypatch):
    # low-confidence project would normally invoke the LLM auth reader (which
    # uploads auth-source excerpts); with the opt-out it must not be called
    from any2agent.scan import auth
    (tmp_path / "app.py").write_text("x = 1  # no recognizable auth scheme\n")
    called = {"llm": False}
    monkeypatch.setattr(auth, "_llm_auth", lambda *a, **k: called.__setitem__("llm", True) or None)
    monkeypatch.setenv("ANY2AGENT_NO_LLM_SOURCE", "1")
    auth.analyze(str(tmp_path), use_llm=True)
    assert called["llm"] is False, "source excerpts must not reach the LLM when opted out"


def _serve_guard(host, auth_type, trust_env, monkeypatch):
    if trust_env is None:
        monkeypatch.delenv("ANY2AGENT_TRUST_NETWORK", raising=False)
    else:
        monkeypatch.setenv("ANY2AGENT_TRUST_NETWORK", trust_env)
    ran = {"served": False}
    monkeypatch.setattr(server_app, "build_app", lambda *a, **k: object())
    import sys
    fake_uvicorn = type(sys)("uvicorn")
    fake_uvicorn.run = lambda *a, **k: ran.__setitem__("served", True)
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    cfg = AgentConfig(project="p", base_url="http://t", auth={"type": auth_type})
    server_app.serve(cfg, object(), host=host, port=0)
    return ran["served"]


def test_network_bind_with_standing_cred_is_refused(monkeypatch):
    with pytest.raises(SystemExit):
        _serve_guard("0.0.0.0", "bearer", None, monkeypatch)


def test_loopback_and_passthrough_and_trust_flag_are_allowed(monkeypatch):
    assert _serve_guard("127.0.0.1", "bearer", None, monkeypatch)        # loopback: fine
    assert _serve_guard("0.0.0.0", "passthrough", None, monkeypatch)     # no standing cred: fine
    assert _serve_guard("0.0.0.0", "bearer", "1", monkeypatch)           # explicit trust: fine
