# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com); versions follow [SemVer](https://semver.org).

## [Unreleased]

### Security
- **Cross-host credential leak fixed** — the REST adapter now strips
  `Cookie`/`Authorization` on any redirect that changes origin (stdlib
  `urlopen` re-sent them verbatim to the redirect target, incl. another host).
- **SSRF hardening** — LLM-controlled path params are percent-encoded and the
  built URL is origin-checked (http(s) + same scheme/host/port as base_url)
  before any request, so a tool arg can't reach another host or `file://`.
- **Code-leak opt-out** — `ANY2AGENT_NO_LLM_SOURCE=1` withholds raw source
  excerpts from the LLM (parameter-synthesis hints and layer-3 auth analysis);
  tool names/descriptions/schemas are still sent. Connect now prints a notice
  when source may be uploaded.
- **Confused-deputy guard** — `serve` refuses a non-loopback bind when auth is
  a standing-credential mode (bearer/api_key_header/cookie), since the server
  would attach its own token for every network client. Override with
  `ANY2AGENT_TRUST_NETWORK=1`; passthrough auth is exempt.
- Chat UI `esc()` now also escapes `"` (defense-in-depth).

### Changed
- **2 fewer runtime dependencies** (ponytail audit): the REST adapter now uses
  stdlib `urllib` (one synchronous JSON call never needed httpx), and pyyaml
  moved to an optional `[yaml]` extra (only YAML contracts need it — JSON
  contracts and source scans need nothing). httpx remains a dev/test dep for
  fastapi's TestClient.
- Ponytail-audit cleanup across the tree: dead branches/flags/params removed
  (`--token-env/--header/--cookie-name` on connect were silently ignored),
  duplicated verify-session builder unified into `config.verify_ctx_from_env`,
  stale docstrings dropped (~70 lines net). `AGENTS.md` now carries the
  decision ladder (adapted from ponytail, MIT) + this repo's invariants.

### Added
- **`any2agent migrate`** — modernize curated files after tool shaping:
  rewrites old tool-name references (from aliases + the shaping audit map) in
  `evals.json`, `eval-lessons.json`, and any `--files` JSON. `--dry-run`
  previews; real runs write `.premigrate.bak` backups and are idempotent.
- **Per-step composite telemetry** — steps inside a composite now also record
  under their own tool names, so drift detection points at the failing inner
  tool, not just the composite.
- **Drift alert webhook** — set `ANY2AGENT_ALERT_WEBHOOK` and a tool crossing
  into suspect state POSTs one JSON alert per drift episode (re-armed after
  recovery); fire-and-forget, never blocks or breaks a conversation.
- **Field projection** — collection reads gain a `fields` param (shaping v3):
  the model can request just the columns it needs (`fields="id,title"`);
  applied render-time only, never sent to your API.
- **Pagination steering** — when a truncated tool exposes `offset`/`page`/
  `cursor`-style params, the truncation hint names the exact next call
  ("pass offset=10 for the next page").
- **Embedding tool search** — with `OPENAI_API_KEY` set, `search_tools`
  ranking upgrades to embedding similarity (via litellm, no new dependency),
  with silent keyword fallback on any failure.
- **PDCA closing docs** — completion reports for all seven features
  (docs/04-report/) and the missing eval-console gap analysis.
- **Response shaping (`respond.py`)** — tool results reach the model as
  token-efficient, ALWAYS-valid JSON: lists truncate item-by-item with
  `_meta.truncated {shown,total}` and a "refine with filters/limit" hint
  (halving the budget on overflow — never a mid-structure slice), long text
  gets a marker, and `concise` (default) drops null/empty fields while
  `detailed` keeps everything for follow-up calls. Collection reads gain a
  `response_format` enum param (render-time only — popped before dispatch,
  never sent to the backend). Errors carry a deterministic actionable `hint`
  per status class (422 schema guidance + server detail, 401/403 "don't
  retry, tell the user", 404 suggests the sibling `*_list`/`*_search` tool,
  429/5xx/transport guidance). Raw data stays untouched for the UI, eval
  traces, and graders — only the LLM-facing message is shaped.
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
- **Deterministic tool shaping** (`shape.py`, on by default in `connect`,
  `--no-shape` to opt out) — stops shipping raw 1-route-=-1-tool wrappers:
  mechanical names become `resource_action` (`get__notes` → `notes_list`,
  `delete__notes_note_id` → `notes_delete`) so related tools group under a
  resource prefix, and collection reads gain a `limit` parameter plus a
  "prefer filters over fetching everything" nudge. Conservative: curated
  names (OpenAPI operationIds) and collisions are kept and reported, never
  mangled. Old names persist as **aliases** resolved everywhere (dispatch,
  evals, lessons, tool search), so existing toolspecs and curated eval tasks
  keep working. Idempotent via `meta.shaping`.
- **`eval --compare OLD_TOOLSPEC`** — A/B two toolsets on the same task set
  and print a verdict (non-inferior completion rate + call count); the old
  run is measurement-only and never pollutes history or lessons.
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
