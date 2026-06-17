# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com); versions follow [SemVer](https://semver.org).

## [0.1.0] — initial release

First public release. Point it at a project, get a verified chat agent.

### Added
- **`connect`** — agentic onboarding: scan a source tree (or OpenAPI contract),
  detect the auth scheme, build a tool set, and run a generate → verify → repair
  loop until measurable criteria pass (or an honest residual report).
- **Scanners** — OpenAPI 3 / Swagger 2 (`scan/openapi.py`) and source-tree route
  extraction for FastAPI, Flask, Express, NestJS, Spring, and Next.js App Router
  (`scan/code.py`).
- **Auth analysis** (`scan/auth.py`) — 3-layer detection: known schemes
  (Supabase, NextAuth, Spring Security, Django, Laravel, JWT bearer) + generic
  cookie/header carrier extraction from code + optional LLM fallback.
- **Passthrough RBAC** — the agent carries the logged-in user's own session/token
  to every call; the backend enforces roles. `401/403` treated as authorization
  outcomes, not failures.
- **Verifier** (`verifier.py`) — coverage, accuracy (with hard/warn split),
  liveness (read-only live probes), and agent tool-selection (`agent_e2e`).
- **Repair** (`llm_repair.py`) — deterministic path-param fill + LLM parameter
  synthesis and description rewriting, with a call budget. Dead tools quarantined.
- **Multi-model serving (every major platform)** — OpenAI, Anthropic, Gemini, Mistral,
  Groq, DeepSeek, xAI (Grok), Moonshot, Cohere, Perplexity, Together, OpenRouter,
  and local Ollama via LiteLLM — a model appears only when its key is set. Plus a
  generic `ANY2AGENT_MODELS` hook for Azure/Bedrock/Vertex/any LiteLLM model. Write/danger confirm gate.
- **CLI** — `init`, `scan`, `serve`, `connect`. Artifacts named per project
  (`<project>.toolspec.json`, `<project>.any2agent.toml`).
- Petstore example, Apache-2.0 license.
