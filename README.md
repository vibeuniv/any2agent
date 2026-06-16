# any2agent

**Point it at your project. Get a working AI agent.**

`any2agent` reads your app's API — from an OpenAPI contract *or straight from the
source code* — figures out the routes **and the authentication**, builds a tool
set, **verifies it against your live API**, and serves a chat agent. No glue code.

```
your codebase ──▶ any2agent connect ──▶ a chat agent that calls your API
                  (scan → verify → repair, until it works)
```

Most "OpenAPI → tool" projects stop at "you bring a spec." `any2agent` is different:

- 🔎 **Reads source, not just specs** — no OpenAPI file? It scans your code
  (FastAPI, Flask, Express, NestJS, Spring, Next.js…) and extracts the routes.
- 🔐 **Understands your auth** — detects how you log users in (Supabase, NextAuth,
  Spring Security, JWT, session cookies, custom headers…) and **passes the
  end-user's own session through**, so your existing roles/permissions (RBAC)
  apply. The agent gets **no extra privileges**.
- ✅ **Verifies itself** — a generate → verify → repair loop checks coverage,
  correctness, and live calls, and fixes gaps until it passes (or tells you
  honestly what's left).
- 🤖 **Any model** — OpenAI, Claude, Gemini, Kimi. A model shows up only when its
  API key is set.

---

## 60-second quickstart

```bash
# 1. Install
pip install any2agent          # or: pip install git+https://github.com/<you>/any2agent

# 2. Set ONE model key (any of these)
export OPENAI_API_KEY=...     # or ANTHROPIC_API_KEY / GEMINI_API_KEY / MOONSHOT_API_KEY

# 3. Connect your project — interactive wizard does the rest
any2agent connect
#   ? project path           → ./my-app
#   ? live API base URL      → http://localhost:3000   (it even guesses this)
#   ? verify with live calls → y
#   → scans, builds tools, verifies, and starts a chat agent
```

Open the printed URL and chat with your API. Done.

> Prefer one command? `any2agent connect --path ./my-app --base-url http://localhost:3000 --live`

---

## Already have an OpenAPI spec?

Skip straight to tools — no source scan needed:

```bash
any2agent init --openapi https://petstore3.swagger.io/api/v3/openapi.json \
             --project petstore \
             --base-url https://petstore3.swagger.io/api/v3
any2agent serve --project petstore        # → http://127.0.0.1:8800
```

A full runnable example lives in [`examples/petstore`](examples/petstore).

---

## What it generates (named after your project)

| File | What it is |
|------|------------|
| `myapp.toolspec.json` | the tools (one per API operation) — re-runnable, editable |
| `myapp.any2agent.toml`  | config: base URL, auth method, default model |

Re-run `connect`/`init` anytime your API changes.

---

## Choosing a model

Set the key, and that model appears in the chat UI's model picker:

| Provider | Set this env var | Override model with |
|----------|------------------|---------------------|
| OpenAI   | `OPENAI_API_KEY`    | `OPENAI_MODEL` |
| Anthropic (Claude) | `ANTHROPIC_API_KEY` | `CLAUDE_MODEL` |
| Google (Gemini) | `GEMINI_API_KEY` | `GEMINI_MODEL` |
| Moonshot (Kimi) | `MOONSHOT_API_KEY` | `KIMI_MODEL` |

Copy `.env.example` → `.env` and fill in what you have. **A blank key = that
model is simply hidden.** No key at all? Scanning and verifying still work; only
the chat needs a model.

---

## Authentication & permissions (the important part)

`any2agent` never invents access. It carries the **logged-in user's own
credential** to every API call (this is *passthrough*), so your backend enforces
roles exactly as it already does:

- The wizard inspects your code and proposes the right mode automatically
  (cookie like `sb-*` / `JSESSIONID`, `Authorization: Bearer`, a custom header…).
- A `403`/`401` from your API is treated as **"not allowed for this user"** —
  correct behavior, not an error.
- Write/delete operations **pause for one-click confirmation** before running.

You set credentials with environment variables, named in `myapp.any2agent.toml`.
**Secrets never go in the repo.**

---

## Safety

- **Read vs write/destructive** is auto-classified (by HTTP method). Writes and
  deletes require explicit confirmation in the chat.
- During verification, only **read** endpoints are probed live; writes are never
  auto-called.
- Tools that can't be made to work are **quarantined**, not shipped broken — and
  reported honestly.

---

## How it works

```
1. scan      OpenAPI contract OR source tree  → tools + route ground-truth
2. auth      detect login/session scheme      → passthrough plan
3. verify    coverage · correctness · live calls · agent tool-selection
4. repair    fill gaps (params, descriptions, missing routes); re-verify
5. serve     chat UI + /chat API  (multi-model, confirm gate)
```

Exit criteria are explicit: it stops when **all checks pass**, or on a budget/
no-progress limit — and then prints exactly what's still unverified.

---

## Requirements

- Python 3.9+
- One LLM provider key (for chatting and optional smart repair)
- Your target API reachable at a base URL (for live verification & runtime)

## License

[Apache-2.0](LICENSE). Ships with **no** vendor data or proprietary tool
catalogs — bring your own API.
