# any2agent — User Guide

Point it at your project, get a working, **verified** AI agent. This guide walks
through every command a user actually runs, in the order you'd run them.

```
connect  →  eval  →  serve  →  (chat + eval console)  →  eval --fix / lessons
   scan·verify·repair   measure    use it            improve, repeat
```

---

## 1. Install & prerequisites

```bash
pip install git+https://github.com/vibeuniv/any2agent
```

You need **one** LLM provider key (any of these):

```bash
export OPENAI_API_KEY=...      # or ANTHROPIC_API_KEY / GEMINI_API_KEY /
                               # MISTRAL_API_KEY / GROQ_API_KEY / DEEPSEEK_API_KEY /
                               # XAI_API_KEY / MOONSHOT_API_KEY / ... (13 providers)
```

No key? `scan`/`init`/`connect`(static checks) still work — only chat, the
task eval, and LLM repair need a model.

---

## 2. Connect your project

```bash
cd /path/to/your-project
any2agent connect
```

The wizard: guesses your API base URL from source, detects **how your app
authenticates users** (cookie/bearer — the agent passes the user's own session
through, so your RBAC stays in force), scans routes (or an OpenAPI spec if one
exists), then runs a **verify → repair loop** until the tool set passes or it
prints an honest residual report.

Useful flags (all optional — the wizard asks otherwise):

| Flag | What it does |
|---|---|
| `--path`, `--project`, `--base-url` | skip the questions |
| `--live` / `--no-live` | consent to live **read-only** probing of your API |
| `--no-shape` | keep raw mechanical tool names (skip `resource_action` renaming + list promotion) |
| `--session-cookie` / `--session-bearer` | verify under a real user session (RBAC-aware); or env `ANY2AGENT_VERIFY_COOKIE` / `ANY2AGENT_VERIFY_BEARER` |
| `--eval` | after the loop, run the task-based eval as a final gate |
| `--no-input` | CI mode: flags/defaults only |

Output (all named after your project):

| File | What it is |
|---|---|
| `yourapp.toolspec.json` | the tools — editable, re-runnable |
| `yourapp.any2agent.toml` | config: base URL, auth, default model |
| `yourapp.evals.json` | eval tasks (after first eval) — **curate these**; your edits always win |
| `yourapp.eval-lessons.json` | guidance learned from eval failures (auto-managed) |

Already have an OpenAPI spec? Skip the wizard: `any2agent init --openapi ./openapi.json --base-url https://api.yourapp.com`

---

## 3. Verify it actually works — `any2agent eval`

This is the self-verification step: realistic multi-step tasks are generated
from your tool set, run through the **real** agent loop against your live API,
and graded deterministically (which tools ran, state re-read via your API,
answer content) plus an advisory LLM judge.

```bash
any2agent eval --project yourapp
```

What you'll see:

```
[eval] tasks=8 (read=6 write=2)  target=http://localhost:3000
  [PASS] notes-pair-1        tools=2 checks 2/2
  [FAIL] notes-read-2        tools=1 checks 1/2
[eval] rate=0.75 (threshold 0.80)  rated=4  wrong_tool=1  ...
[eval] history: rate 0.75 (prev 0.88 ▼0.13, 5 runs)      ← better or worse than last time
[eval] what to fix:                                       ← one action line per failure
  - notes-read-2 [wrong_tool] For requests like '...', use notes_health — the model called notes_list instead.
[eval] lessons -> yourapp.eval-lessons.json (1 active; injected as guidance at serve time)
[eval] ❌ below threshold — failed: notes-read-2
```

| Option | What it does |
|---|---|
| `--history` | show the last 10 recorded runs + trend, then exit |
| `--fix` | auto-apply repairs for fixable failures (tool description rewrite with the failure as context, parameter synthesis from 4xx calls), then tells you to re-run |
| `--live-write` | allow WRITE tasks (asks you to confirm it's not production; payloads are tagged `[a2a-eval]` and cleaned up afterwards) |
| `--regen` | regenerate tasks, ignoring your curated `evals.json` |
| `--n 8` / `--threshold 0.8` / `--budget 40` | task count / gate / LLM call cap |
| `--json report.json` | full report for CI artifacts |
| `--model` / `--judge-model` | model under test / judge model |
| `--compare old.toolspec.json` | A/B an older toolset on the same tasks; prints a keep/revert verdict |

**Exit codes (CI-ready):** `0` gate passed · `1` below threshold · `2` cannot run
(no key / no base_url). Failure classes you'll see: `wrong_tool`, `bad_args`,
`tool_error`, `state_mismatch`, `answer_gap`.

The improvement loop is: `eval` → read *what to fix* → `eval --fix` (or edit
`toolspec.json` / `evals.json` yourself) → `eval` again — the history line
tells you if you're getting better.

---

## 3.5 Composite tools — `any2agent compose`

Chain frequent multi-step flows (list → get, find → update) into ONE tool the
agent can call, per the guide's "consolidate functionality" principle:

```bash
any2agent compose --project yourapp            # propose → review → approve each
any2agent compose --project yourapp --dry-run  # look, don't touch
```

- Candidates come from an LLM (or a deterministic list→detail fallback without
  a key) plus your eval history's frequent tool chains.
- **Every candidate needs your interactive approval — there is no `--yes`.**
  Danger tools can never be composed.
- Steps bind intermediate values deterministically
  (`"note_id": "$steps[0].data[0].id"`); a composite containing any write step
  still hits the confirm gate as a whole.
- Partial failures are honest: which steps ran, which failed,
  `rolled_back: false` — a composite is not a transaction.
- On adoption it backs up `yourapp.toolspec.precompose.json` and prints the
  exact `eval --compare` command — run it to prove the composite helps.

Large results? Collection reads also accept `response_format`
(`concise`/`detailed`) — the agent picks it per call; it never reaches your API.

## 4. Serve the agent

```bash
any2agent serve --project yourapp
# → http://127.0.0.1:8800
```

The chat UI gives you:

- **Multi-model picker** — every provider whose key is set appears in the menu.
- **Safety gate** — read tools run instantly; write/delete actions pause with a
  confirm card (Run / Cancel) before anything is executed.
- **Session passthrough** — every tool call carries the logged-in user's own
  cookie/token, so your backend's permissions apply unchanged.
- **Trust badge** (header) — `✅ 0.88 · 3 runs` from your latest eval, or
  `— not evaluated`. Click it to open the eval console.
- **Learned guidance** — active lessons are injected into every turn, so
  mistakes found by eval aren't repeated in chat.
- 👍/👎 feedback buttons; a 👎 with a correction is remembered per user.

Embed it in your app: `<iframe src="http://127.0.0.1:8800/" style="width:380px;height:560px;border:0"></iframe>`

---

## 5. Eval console (web)

Open **`/evals/ui`** (or click the trust badge). Read-only, no setup:

- **Status** — latest rate, gate result, trend sparkline
- **What to fix** — the action line for each failure in the latest run
- **History** — recent runs (time, rate, pass/fail, failed task ids)
- **Active lessons** — the guidance currently injected into chat

Raw JSON at `GET /evals`. Files are re-read on every request, so a CLI eval run
shows up in the browser immediately — no server restart.

---

## 6. Everyday cheat-sheet

```bash
any2agent connect                          # onboard a project (wizard)
any2agent eval --project myapp             # verify: can the agent do real tasks?
any2agent eval --project myapp --history   # am I getting better?
any2agent eval --project myapp --fix       # auto-repair what's fixable
any2agent serve --project myapp            # chat UI + eval console
```

CI gate example:

```bash
any2agent eval --project myapp --json eval-report.json || exit 1
```

---

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `no LLM provider key set` (exit 2) | export one provider key (see §1) |
| `base_url is empty` (exit 2) | set `base_url` in `yourapp.any2agent.toml` |
| Everything 401/403 during verify/eval | your API requires login — pass a session: `ANY2AGENT_VERIFY_COOKIE` or `ANY2AGENT_VERIFY_BEARER` (401/403 under a real session = your RBAC working, reported as `authz`, not failure) |
| `infra=N` in eval output | provider/transport errors — not agent failures; they're excluded from the rate so the score isn't distorted |
| Badge says `— not evaluated` | run `any2agent eval --project yourapp` once |
| Tasks feel unrealistic | edit `yourapp.evals.json` by hand — curated tasks are never overwritten (only `--regen` regenerates) |
