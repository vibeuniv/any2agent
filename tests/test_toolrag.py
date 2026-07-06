"""tool discovery ranking: keyword overlap (default, key-free) and the optional
embedding upgrade — deterministic vectors, cache invalidation, silent fallback.

No network: litellm.embedding is monkeypatched. The autouse fixture guarantees
each test starts with no provider key and an empty embedding cache, so the
keyword path is the baseline unless a test opts into a key."""
import litellm
import pytest

from any2agent.core import toolrag
from any2agent.spec import ToolSpec


def _tool(name, desc, path="/x"):
    return ToolSpec(name=name, description=desc,
                    parameters={"type": "object", "properties": {}},
                    backing={"method": "GET", "path": path})


def _tools():
    # 'lookup_customers' shares the lexical token "locate" with the query, so
    # keyword ranks it first; the embedding fake below puts the semantically
    # closer 'search_users' first instead — a clean reordering signal.
    return [
        _tool("search_users", "find users by name", "/users"),
        _tool("lookup_customers", "locate customer accounts", "/customers"),
    ]


def _fake_embedding(calls):
    """Deterministic 2-D vectors: a 'people' axis and a 'customer' axis, keyed off
    substrings. Records each call's input list so cache behaviour is observable."""
    def fake(model=None, input=None, **kwargs):
        calls.append(list(input))
        vecs = []
        for text in input:
            t = text.lower()
            people = 1.0 if ("user" in t or "people" in t or "person" in t) else 0.0
            cust = 1.0 if ("customer" in t or "account" in t) else 0.0
            vecs.append([people, cust])
        return {"data": [{"embedding": v} for v in vecs]}
    return fake


@pytest.fixture(autouse=True)
def _clean_emb_state(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    toolrag._emb_cache.clear()
    yield
    toolrag._emb_cache.clear()


def test_keyword_path_without_key_and_reranks_with_embeddings(monkeypatch):
    calls = []
    monkeypatch.setattr(litellm, "embedding", _fake_embedding(calls))
    tools = _tools()

    # keyword path (no key): lexical "locate" overlap surfaces the customers tool
    kw = toolrag.search("locate people", tools)
    assert [t.name for t in kw] == ["lookup_customers"]
    assert calls == []  # embedding never touched without a key

    # embedding path (key set): semantic match flips the top result
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    toolrag._emb_cache.clear()
    emb = toolrag.search("locate people", tools)
    assert [t.name for t in emb] == ["search_users"]
    assert calls, "embedding path was exercised"


def test_embedding_exception_falls_back_to_keyword(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def boom(model=None, input=None, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(litellm, "embedding", boom)
    tools = _tools()
    hits = toolrag.search("locate people", tools)
    # identical to the keyword result — the failure is silent, discovery survives
    assert [t.name for t in hits] == ["lookup_customers"]


def test_embedding_cache_invalidates_on_toolset_change(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    calls = []
    monkeypatch.setattr(litellm, "embedding", _fake_embedding(calls))
    tools = _tools()

    toolrag.search("locate people", tools)
    toolrag.search("locate people", tools)  # same toolset -> cached, no re-embed
    # a "toolset embed" is a call whose input holds >1 text (the per-query embed
    # is a single-element input); it must have happened exactly once so far
    assert len([c for c in calls if len(c) > 1]) == 1

    # change a description -> content signature changes -> re-embed
    changed = list(tools)
    changed[0] = _tool("search_users", "CHANGED locate members", "/users")
    toolrag.search("locate people", changed)
    assert len([c for c in calls if len(c) > 1]) == 2


def test_embedding_respects_top_k(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    calls = []
    monkeypatch.setattr(litellm, "embedding", _fake_embedding(calls))
    # two tools both on the 'people' axis so both score > 0 under the query
    tools = [_tool("users_a", "user directory people", "/a"),
             _tool("users_b", "person profiles users", "/b")]
    hits = toolrag.search("people person", tools, top_k=1)
    assert len(hits) == 1


def test_search_signature_and_score_unchanged_without_key():
    # public API stays keyword-deterministic when no key is present
    tools = _tools()
    assert toolrag.score("locate customer", tools[1]) > toolrag.score("locate customer", tools[0])
    assert [t.name for t in toolrag.search("locate customer", tools)] == ["lookup_customers"]
