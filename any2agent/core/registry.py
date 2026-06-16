"""Declarative multi-provider model registry (via LiteLLM).

A model appears only when its provider API key is present in the environment, so
the same build runs with one or many providers. No keyword-if routing — the
registry is pure data; selection is an explicit id.

Sampling caveat: reasoning models (gpt-5/o1/o3) and Claude reject `temperature`
(HTTP 400). We add `temperature` only for OpenAI non-reasoning models; everything
else omits it, and litellm.drop_params is a backstop.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

try:
    import litellm  # type: ignore
    litellm.drop_params = True
except Exception:  # pragma: no cover - import guarded so scan/serve work without it
    litellm = None  # type: ignore

# id -> provider entry. to_litellm prefixes the model id for litellm routing.
MODEL_REGISTRY: List[Dict[str, Any]] = [
    {"id": "gpt",    "label": "GPT (OpenAI)",      "key_env": "OPENAI_API_KEY",
     "model_env": "OPENAI_MODEL",    "default_model": "gpt-5-mini",      "prefix": ""},
    {"id": "kimi",   "label": "Kimi (Moonshot)",   "key_env": "MOONSHOT_API_KEY",
     "model_env": "KIMI_MODEL",      "default_model": "moonshot-v1-8k",  "prefix": "moonshot/"},
    {"id": "claude", "label": "Claude (Anthropic)","key_env": "ANTHROPIC_API_KEY",
     "model_env": "CLAUDE_MODEL",    "default_model": "claude-opus-4-8", "prefix": "anthropic/"},
    {"id": "gemini", "label": "Gemini (Google)",   "key_env": "GEMINI_API_KEY",
     "model_env": "GEMINI_MODEL",    "default_model": "gemini-2.0-flash","prefix": "gemini/"},
]
_BY_ID = {e["id"]: e for e in MODEL_REGISTRY}


def _resolved_model(entry: Dict[str, Any]) -> str:
    return os.getenv(entry["model_env"]) or entry["default_model"]


def _has_key(entry: Dict[str, Any]) -> bool:
    return bool(os.getenv(entry["key_env"]))


def llm_available() -> bool:
    return any(_has_key(e) for e in MODEL_REGISTRY)


def available_models() -> List[Dict[str, str]]:
    return [
        {"id": e["id"], "label": e["label"], "model": _resolved_model(e)}
        for e in MODEL_REGISTRY if _has_key(e)
    ]


def default_model_id(prefer: str = "") -> Optional[str]:
    avail = {m["id"] for m in available_models()}
    if not avail:
        return None
    if prefer and prefer in avail:
        return prefer
    if "gpt" in avail:
        return "gpt"
    for e in MODEL_REGISTRY:
        if e["id"] in avail:
            return e["id"]
    return None


def resolve(model_id: Optional[str], prefer_default: str = ""):
    """Return (entry, litellm_model_string, resolved_id). Falls back to default
    when model_id is missing/unavailable."""
    avail = {m["id"] for m in available_models()}
    rid = model_id if (model_id in avail) else default_model_id(prefer_default)
    if rid is None:
        return None, None, None
    entry = _BY_ID[rid]
    return entry, entry["prefix"] + _resolved_model(entry), rid


def completion_kwargs(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Per-provider extra kwargs. temperature only for OpenAI non-reasoning models."""
    kw: Dict[str, Any] = {}
    if entry["id"] == "gpt":
        m = _resolved_model(entry)
        if not m.startswith("gpt-5") and "o1" not in m and "o3" not in m:
            kw["temperature"] = 0.2
    return kw


def completion(model_string: str, messages, tools=None, stream=True, extra=None):
    """Thin wrapper over litellm.completion. Raises a clear error if litellm
    isn't installed."""
    if litellm is None:
        raise RuntimeError("litellm not installed — `pip install litellm` to enable chat.")
    kw = {"model": model_string, "messages": messages, "stream": stream}
    if tools:
        kw["tools"] = tools
    if extra:
        kw.update(extra)
    return litellm.completion(**kw)
