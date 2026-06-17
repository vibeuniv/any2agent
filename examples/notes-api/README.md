# Example: Notes API (source-tree demo)

A tiny FastAPI app with JWT auth — the demo target for `any2agent connect`.
It shows the part other tools skip: **connect reads the source**, detects the
**auth scheme**, and classifies write/delete operations.

```bash
# from the repo root
any2agent connect --path examples/notes-api --base-url http://localhost:8000
```

You'll see:

```text
[connect] auth analysis: scheme=jwt-bearer carrier=bearer confidence=medium
  -> passthrough the user's Bearer token (header=Authorization)
[connect] scanning: examples/notes-api
  framework=fastapi  routes=5
[connect] verify round 1/4 ...
  [PASS] coverage  5/5 (100%)
  [PASS] accuracy  checked=5 bad=0
[connect] ✅ all checks passed
  wrote: notes-api.toolspec.json (tools=5 write=2 danger=1)
```

To actually chat, run the app and serve:

```bash
pip install fastapi uvicorn pyjwt
uvicorn main:app --reload          # in examples/notes-api/  → http://localhost:8000
export OPENAI_API_KEY=...           # any provider key
any2agent serve --project notes-api
```
