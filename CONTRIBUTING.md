# Contributing to any2agent

Thanks for your interest! any2agent turns any API-backed project into a verified
chat agent. Contributions of all sizes are welcome — new framework scanners, auth
detectors, transport adapters, docs, and bug fixes especially.

## Quick start (dev)

```bash
git clone https://github.com/vibeuniv/any2agent
cd any2agent
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dotenv]"

# sanity check (offline, no API key needed)
python -m compileall any2agent
any2agent init --openapi examples/petstore/openapi-min.json --project petstore \
  --base-url https://petstore3.swagger.io/api/v3
```

## Project layout

```
any2agent/
  cli.py            entry point: init / scan / serve / connect
  connect.py        agentic onboarding: scan → verify → repair loop
  verifier.py       checks: coverage · accuracy · liveness · agent_e2e
  llm_repair.py     optional LLM repair: descriptions, parameter synthesis
  spec.py config.py tool spec + per-project config
  scan/   auth.py · code.py · openapi.py     input analyzers
  core/   agent.py (main agent loop) · registry · toolrag · dispatch · memory
  adapters/  rest.py (+ base interface for new transports)
  server/    app.py (FastAPI: /chat, /confirm, /info) + web/chat.html
```

## High-value contributions

- **New framework scanner** — add detection + route extraction in `scan/code.py`
  (Django, Rails, Laravel, Go, …). Routes become the coverage ground-truth.
- **New auth detector** — add a scheme to `scan/auth.py` (heuristic + carrier
  extraction). Keep RBAC server-enforced; we only carry the user's credential.
- **New transport adapter** — implement `adapters/base.Adapter` (gRPC, GraphQL, MCP).

## Ground rules

- **No hardcoding** of hosts/ports/models/secrets — config + env only.
- **Safety**: writes/deletes stay behind the confirm gate; never auto-call them in
  verification.
- **Honesty**: the verify loop must never silently claim success — report residual gaps.
- Keep it dependency-light and Python 3.9+ compatible.

## Pull requests

1. Branch from `main`.
2. Make sure `python -m compileall any2agent` passes and `any2agent init` on
   `examples/petstore/openapi-min.json` still produces a tool set.
3. Describe what you changed and (for scanners/auth) which stack you tested against.

## License

By contributing you agree your contributions are licensed under [Apache-2.0](LICENSE).
