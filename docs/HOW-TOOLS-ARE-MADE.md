# How your API becomes agent tools

*A plain-language walkthrough of what actually happens between `any2agent
connect` and a working chat agent: how routes are found, how they become
tools, how authentication is carried, and what a single chat message triggers
at runtime. Companion to [HOW-EVAL-WORKS.md](HOW-EVAL-WORKS.md), which covers
how the result is verified.*

```
your project ──▶ 1 scan ──▶ 2 shape ──▶ 3 auth plan ──▶ 4 verify/repair ──▶ toolspec.json
                                                                                │
chat message ──▶ 5 expose to LLM ──▶ 6 confirm gate ──▶ 7 HTTP call ──▶ 8 render back
```

---

## 1. Scan — finding every operation your API has

Two inputs, one output (a list of tools). The scanner prefers a contract when
one exists, because contracts carry richer parameter schemas:

**OpenAPI / Swagger fast path** (`scan/openapi.py`). If the tree contains
`openapi.json|yaml` / `swagger.json|yaml` (or you pass `--openapi <url|file>`),
every operation becomes a tool directly: `operationId` → name, spec
`summary/description` → description, `parameters` + `requestBody` (with `$ref`
resolution into `components.schemas`, depth-capped) → a flat JSON-Schema of
inputs. Both OpenAPI 3.x and Swagger 2.0 are handled.

**Source-tree scan** (`scan/code.py`) — no contract needed. The framework is
detected first (package.json has `"next"`? `fastapi` imported? `express(`
called? `@RestController` present?), then ONLY that framework's extraction
pass runs — so e.g. `headers.get('authorization')` in a Next.js app is never
mis-read as an Express route. What each pass looks for:

| Framework | Signal |
|---|---|
| FastAPI / Flask | `@app.get("/notes")` decorators; Flask `methods=["POST"]` lists |
| Express | `router.get('/x', …)` calls (paths must start with `/`) |
| NestJS | `@Controller('base')` + `@Get('sub')` decorators, joined |
| Spring | `@RequestMapping` class prefix + `@GetMapping` methods |
| Next.js App Router | `app/**/route.ts` files exporting `GET/POST/…`; folder segments become the URL (`[id]` → `{id}`, route groups dropped) |

Path variables (`{note_id}`, `:id`) become required string parameters. The
scan also emits a **ground-truth route list** — kept separate from the tools
so the verifier can later prove nothing was missed.

## 2. What a "tool" actually is

One operation = one `ToolSpec` (persisted in `<project>.toolspec.json`,
editable):

```json
{
  "name": "notes_get",
  "description": "GET /notes/{note_id} …",
  "parameters": {"type": "object", "properties": {"note_id": {"type": "string"}},
                  "required": ["note_id"]},
  "backing": {"method": "GET", "path": "/notes/{note_id}"},
  "write": false, "danger": false,
  "domain": "notes",
  "aliases": ["get__notes_note_id"]
}
```

- `parameters` is exactly what the LLM sees as the tool's input schema.
- `backing` is how the runtime turns a call into HTTP — the LLM never sees it.
- `write`/`danger` come from the HTTP verb (GET/HEAD read · POST/PUT/PATCH
  write · DELETE danger) and drive the confirmation gate.
- A composite tool has `backing.composite` (a step list) instead — see §7.

## 3. Shape — from endpoint wrappers to agent-friendly tools

Raw scanner output is a mechanical 1-route-=-1-tool wrapper (`get__notes`).
The shaping pass (`shape.py`, skip with `--no-shape`) makes it something an
LLM picks correctly:

- **Renaming**: `<method>_<path>` → `<resource>_<action>` — `get__notes` →
  `notes_list`, `delete__notes_note_id` → `notes_delete`. Related tools group
  under a resource prefix; the intent is in the name. Old names are kept as
  `aliases` and keep resolving forever (curated names like OpenAPI
  operationIds are never touched; collisions keep the old name and are
  reported).
- **Collection-read promotion**: list-shaped reads gain `limit`,
  `response_format` (concise/detailed) and `fields` parameters plus a
  "prefer filters over fetching everything" nudge. The last two are
  **render-time controls** — popped before dispatch, never sent to your API.
- Idempotent and versioned via `meta.shaping`; re-running upgrades old
  toolspecs without renaming noise.

## 4. Auth — the agent never gets its own credentials

`scan/auth.py` reads the target's auth code in three layers: (1) known-scheme
heuristics (Supabase, NextAuth, Spring Security, Django, JWT bearer…), (2)
framework-agnostic **carrier extraction** — which cookies/headers does the
code actually read from inbound requests? — and (3) an optional LLM read of
the auth files when still unsure. The output is a **passthrough plan**:
"forward the logged-in user's `sb-*` cookies" or "forward the Authorization
bearer".

At runtime every tool call carries the *end user's own* session, so your
backend enforces roles exactly as it already does — a 401/403 is treated as
correct RBAC behavior, not an error. Signed-request schemes (HMAC/SigV4,
mTLS) can't be forwarded and are flagged for a custom adapter.

## 5. Verify → repair — proving the tool set before serving it

`connect` refuses to silently ship guesses. Static + live critics run in a
loop (details in [HOW-EVAL-WORKS.md](HOW-EVAL-WORKS.md)):

- **coverage** — every ground-truth route has a tool (the scan's separate
  route list makes this provable);
- **accuracy** — every tool is structurally sound (path vars present, object
  schemas; composites validated step-by-step);
- **liveness** (with `--live` consent) — read tools are smoke-called against
  the real API under the user's session;
- **agent_e2e / task eval** (with a key) — does a model actually pick and
  complete with these tools?

Failures trigger repair: missing routes appended, path params filled
deterministically, thin schemas/descriptions rewritten by an LLM (budgeted),
dead tools quarantined. The loop ends in ✅ or an **honest residual report**.

## 6. Runtime — what one chat message triggers

Say the user types **"show my notes"**:

1. **Expose** (`core/toolrag.py`): with ≤30 tools every schema
   (`ToolSpec.to_function()`) is sent to the LLM; larger sets send one
   representative per domain plus a `search_tools` meta-tool the model calls
   to discover more (keyword overlap by default; embedding similarity when
   `OPENAI_API_KEY` is set). Lessons learned from past eval failures are
   injected as a system note.
2. **The model picks a tool**: `notes_list(limit=10)`. Render-time params
   (`response_format`, `fields`) are popped here — they never reach your API.
3. **Safety gate** (`core/dispatch.py`): read tools execute immediately;
   write/danger tools return `confirm_required` and the UI shows a
   Run/Cancel card **before anything executes**. A composite's gate uses the
   MAX of its steps' flags, recomputed live.
4. **HTTP call** (`adapters/rest.py`): `backing.path` variables are filled
   from arguments; leftovers go to the query string (GET/DELETE) or JSON body
   (POST/PUT/PATCH); the passthrough headers from §4 are attached. The
   adapter returns a normalized `{ok, status, data, error}`.
5. **Render back** (`respond.py`): the model receives shaped, always-valid
   JSON — lists truncated item-by-item with `_meta.truncated` and a hint that
   names the tool's actual paging parameter; errors carry an actionable hint
   (a 404 on `notes_get` suggests calling `notes_list` first). The UI and
   eval traces keep the raw result; only the LLM message is shaped.
6. **Telemetry** (`evals/telemetry.py`): the call is logged (name, outcome,
   latency — never arguments or identity) so drift is caught later.
7. The tool result feeds back into the model, which either answers or chains
   the next call (up to 8 steps per turn).

## 7. Composites — one tool, several calls

`any2agent compose` proposes multi-step tools ("list then fetch the first
item") mined from your toolset and eval history; **every candidate needs your
interactive approval**. A composite's steps bind values deterministically
(`"note_id": "$steps[0].data[0].id"`), run server-side, and return only the
final result. Partial failure is honest: which steps ran, which failed,
`rolled_back: false` — a composite is not a transaction. Each executed step
also records telemetry under its own tool name.

## Where to look / poke

| Question | File |
|---|---|
| Why did my route (not) become a tool? | `<project>.toolspec.json` (edit it — it's yours), `scan/code.py` patterns |
| Why this tool name? | `meta.shaping.renamed` map in the toolspec; old name still works via `aliases` |
| What auth is being forwarded? | `[auth]` block in `<project>.any2agent.toml` |
| What does the LLM literally see? | `ToolSpec.to_function()` — name + description + parameters, nothing else |
| Did this actually work? | `any2agent eval` + `/evals/ui` — see HOW-EVAL-WORKS.md |
