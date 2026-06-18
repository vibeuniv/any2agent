"""Declarative multi-provider model registry (via LiteLLM).

LiteLLM is the gateway, so any of its 100+ providers can be used. Two layers:

  1. Curated registry — the major platforms below. Each appears in the picker
     ONLY when its API key is set, so the same build runs with one or many.
  2. Generic hook — set ANY2AGENT_MODELS to a comma-separated list of raw LiteLLM
     model strings (e.g. "azure/my-deploy, bedrock/anthropic.claude-3-5-sonnet,
     ollama/llama3.2") to expose anything LiteLLM supports with no code change.
     LiteLLM reads each provider's own credential env vars by convention.

No keyword-if routing — the registry is pure data; selection is an explicit id.
Model ids are env-overridable, so a stale default is never load-bearing.

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

# Curated major platforms. id → entry. `prefix` is the LiteLLM provider route;
# `key_env` gates visibility; `model_env` overrides the default model id.
MODEL_REGISTRY: List[Dict[str, Any]] = [
    {"id": "gpt",        "label": "GPT (OpenAI)",        "key_env": "OPENAI_API_KEY",
     "model_env": "OPENAI_MODEL",     "default_model": "gpt-5-mini",                 "prefix": ""},
    {"id": "claude",     "label": "Claude (Anthropic)",  "key_env": "ANTHROPIC_API_KEY",
     "model_env": "CLAUDE_MODEL",     "default_model": "claude-opus-4-8",            "prefix": "anthropic/"},
    {"id": "gemini",     "label": "Gemini (Google)",     "key_env": "GEMINI_API_KEY",
     "model_env": "GEMINI_MODEL",     "default_model": "gemini-2.0-flash",           "prefix": "gemini/"},
    {"id": "mistral",    "label": "Mistral",             "key_env": "MISTRAL_API_KEY",
     "model_env": "MISTRAL_MODEL",    "default_model": "mistral-large-latest",       "prefix": "mistral/"},
    {"id": "groq",       "label": "Groq",                "key_env": "GROQ_API_KEY",
     "model_env": "GROQ_MODEL",       "default_model": "llama-3.3-70b-versatile",    "prefix": "groq/"},
    {"id": "deepseek",   "label": "DeepSeek",            "key_env": "DEEPSEEK_API_KEY",
     "model_env": "DEEPSEEK_MODEL",   "default_model": "deepseek-chat",              "prefix": "deepseek/"},
    {"id": "grok",       "label": "Grok (xAI)",          "key_env": "XAI_API_KEY",
     "model_env": "XAI_MODEL",        "default_model": "grok-2-latest",              "prefix": "xai/"},
    {"id": "kimi",       "label": "Kimi (Moonshot)",     "key_env": "MOONSHOT_API_KEY",
     "model_env": "KIMI_MODEL",       "default_model": "moonshot-v1-8k",             "prefix": "moonshot/"},
    {"id": "cohere",     "label": "Cohere",              "key_env": "COHERE_API_KEY",
     "model_env": "COHERE_MODEL",     "default_model": "command-r-plus",             "prefix": "cohere/"},
    {"id": "perplexity", "label": "Perplexity",          "key_env": "PERPLEXITYAI_API_KEY",
     "model_env": "PERPLEXITY_MODEL", "default_model": "sonar",                      "prefix": "perplexity/"},
    {"id": "together",   "label": "Together AI",         "key_env": "TOGETHERAI_API_KEY",
     "model_env": "TOGETHER_MODEL",   "default_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "prefix": "together_ai/"},
    {"id": "openrouter", "label": "OpenRouter (200+ models)", "key_env": "OPENROUTER_API_KEY",
     "model_env": "OPENROUTER_MODEL", "default_model": "openrouter/auto",            "prefix": ""},
    # Local models via Ollama — no API key; gated on OLLAMA_HOST being set.
    {"id": "ollama",     "label": "Ollama (local)",      "key_env": "OLLAMA_HOST",
     "model_env": "OLLAMA_MODEL",     "default_model": "llama3.2",                   "prefix": "ollama/"},
]
_BY_ID = {e["id"]: e for e in MODEL_REGISTRY}


def _resolved_model(entry: Dict[str, Any]) -> str:
    return os.getenv(entry["model_env"]) or entry["default_model"]


def _has_key(entry: Dict[str, Any]) -> bool:
    return bool(os.getenv(entry["key_env"]))


def _extra_models() -> List[Dict[str, str]]:
    """Generic hook: ANY2AGENT_MODELS=comma-separated raw LiteLLM model strings.
    Exposes anything LiteLLM supports (Azure, Bedrock, Vertex, custom) with no
    code change. id = the litellm string; label = a readable form."""
    raw = os.getenv("ANY2AGENT_MODELS", "")
    out = []
    for m in [x.strip() for x in raw.split(",") if x.strip()]:
        out.append({"id": "x:" + m, "label": m, "model": m, "_litellm": m})
    return out


def llm_available() -> bool:
    return any(_has_key(e) for e in MODEL_REGISTRY) or bool(_extra_models())


def available_models() -> List[Dict[str, str]]:
    models = [{"id": e["id"], "label": e["label"], "model": _resolved_model(e)}
              for e in MODEL_REGISTRY if _has_key(e)]
    models += [{"id": x["id"], "label": x["label"], "model": x["model"]} for x in _extra_models()]
    return models


def default_model_id(prefer: str = "") -> Optional[str]:
    """Pick the default model id. Precedence: explicit config (`prefer`) →
    DEFAULT_MODEL_ID env → gpt → first available."""
    avail = [m["id"] for m in available_models()]
    if not avail:
        return None
    for cand in (prefer, os.getenv("DEFAULT_MODEL_ID", "")):
        if cand and cand in avail:
            return cand
    if "gpt" in avail:
        return "gpt"
    return avail[0]


def resolve(model_id: Optional[str], prefer_default: str = ""):
    """Return (entry, litellm_model_string, resolved_id). Falls back to default
    when model_id is missing/unavailable. Supports curated ids and generic
    'x:<litellm-string>' ids."""
    avail = {m["id"] for m in available_models()}
    rid = model_id if (model_id in avail) else default_model_id(prefer_default)
    if rid is None:
        return None, None, None
    if rid.startswith("x:"):                       # generic LiteLLM model
        s = rid[2:]
        return {"id": rid, "generic": True}, s, rid
    entry = _BY_ID[rid]
    return entry, entry["prefix"] + _resolved_model(entry), rid


def completion_kwargs(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Per-provider extra kwargs. temperature only for OpenAI non-reasoning models."""
    kw: Dict[str, Any] = {}
    if entry.get("id") == "gpt":
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
