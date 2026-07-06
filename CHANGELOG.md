# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com); versions follow [SemVer](https://semver.org).

## [Unreleased]

### Added
- **Self-verification (`any2agent eval`)** — task-based eval harness: generates
  realistic multi-step tasks from the toolspec (LLM, with a deterministic
  no-key fallback), runs them through the *real* agent loop against the live
  API, and grades completion with deterministic checks (tools called, state
  re-read, answer content) plus an advisory LLM judge. Gates on completion
  rate (default ≥ 0.8), CI-friendly exit codes, `--json` report.
- **`task_eval` critic** (`verifier.py`) — 5th critic alongside
  coverage/accuracy/liveness/agent_e2e; skipped honestly without a key/target.
- **`connect --eval`** — runs the task eval as a final gate; failures feed the
  existing repair channels (description rewrite with the failure as context,
  parameter synthesis from 4xx calls) and the eval re-runs once.
- **Curatable eval sets** — tasks persist as `<project>.evals.json`; a curated
  file always wins over regeneration (`--regen` to override).
- **Write-task safety** — write tasks are opt-in (`--live-write` + interactive
  confirmation), must tag payloads with `[a2a-eval]`, run cleanup calls after
  grading, and report un-cleaned residue honestly. Danger tools are allowed in
  cleanup only.
- **Test suite** — pytest coverage for the grader, runner confirm policy,
  task generation/validation, `task_eval` gating math, and an integration test
  against a real local HTTP server.
- **Eval feedback loop** — every `eval` run is recorded to
  `.any2agent-state/<project>/eval-history.jsonl` (`--history` shows the trend);
  failures are classified into five deterministic causes (wrong_tool / bad_args /
  tool_error / state_mismatch / answer_gap) and printed as one actionable
  "what to fix" line each. Failures also persist as **lessons**
  (`<project>.eval-lessons.json`) that `serve` injects as a system note so the
  agent avoids repeating them; lessons self-clean when tasks pass or tools
  disappear. `eval --fix` applies the repair channels (description rewrite,
  param synthesis) immediately.
- **Eval console (web)** — read-only `GET /evals` (history + trend + lessons,
  files re-read per request so CLI runs show up without a restart), a trust
  badge in the chat header (`✅ 0.88 · 3 runs`, links to the console), and a
  single-file dashboard at `/evals/ui` showing status, sparkline trend,
  per-failure "what to fix" lines, run history, and active lessons. The server
  never runs or mutates evals — the CLI owns that.

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
