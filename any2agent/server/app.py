"""FastAPI server exposing the agent over a project's toolspec.

  GET  /info     -> {project, llm, models, default_model_id, tools}
  POST /chat     -> SSE stream of agent events (delta/tool/confirm/done)
  POST /confirm  -> execute a previously gated write/danger tool
  GET  /evals    -> read-only eval status (history + trend + lessons); files are
                    re-read per request so a CLI eval run shows up immediately
  GET  /evals/ui -> single-file eval dashboard
  GET  /         -> minimal chat UI (project-named)

Everything is keyed by the project name loaded from <project>.any2agent.toml.
The server never runs or mutates evals — `any2agent eval` (CLI) owns that.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from ..config import AgentConfig
from ..spec import ToolSet
from ..adapters.rest import RestAdapter
from ..core import registry, agent, memory


# Defined at module level (FastAPI resolves body models by type hints; closure-local
# Pydantic models can be mis-read as query params).
class ChatBody(BaseModel):
    messages: List[Dict[str, Any]]
    model_id: Optional[str] = None


class ConfirmBody(BaseModel):
    name: str
    args: Dict[str, Any] = {}
    model_id: Optional[str] = None


class FeedbackBody(BaseModel):
    rating: str = ""                 # "up" | "down"
    correction: str = ""             # on 👎: what the user actually wanted (stored as a note)


def build_app(cfg: AgentConfig, toolset: ToolSet) -> FastAPI:
    app = FastAPI(title="%s Agent" % cfg.project)
    adapter = RestAdapter(cfg.base_url, cfg.auth)
    base_ctx: Dict[str, Any] = {"project": cfg.project, "state_dir": cfg.state_dir()}

    # eval-derived guidance (if `any2agent eval` left lessons): injected as a
    # system note per turn so past evaluation failures aren't repeated.
    from ..evals import lessons as eval_lessons
    _lessons = eval_lessons.load(cfg.lessons_path())
    if _lessons:
        base_ctx["lessons"] = [l["guidance"] for l in _lessons]

    web_dir = os.path.join(os.path.dirname(__file__), "web")

    def _req_ctx(request: Request) -> Dict[str, Any]:
        # passthrough: carry the caller's OWN session/token to tool calls so the
        # target's RBAC applies to the user's role (the agent holds no creds).
        c = dict(base_ctx)
        c["in_headers"] = {k.lower(): v for k, v in request.headers.items()}
        c["cookie"] = request.headers.get("cookie", "")
        # optional explicit forward header (embedding app injects the user session)
        xs = request.headers.get("x-agent-session")
        if xs:
            c["cookie"] = (c.get("cookie", "") + "; " + xs).strip("; ")
        # memory owner: a stable per-user id the embedding app forwards. Without a
        # configured header, memory is a single shared "anon" bucket (local/single-user).
        c["memory_enabled"] = cfg.memory_enabled
        owner = ""
        if cfg.memory_owner_header:
            owner = request.headers.get(cfg.memory_owner_header.lower(), "")
        c["owner"] = owner or "anon"
        return c

    @app.get("/info")
    def info():
        return {
            "project": cfg.project,
            "llm": registry.llm_available(),
            "models": registry.available_models(),
            "default_model_id": registry.default_model_id(cfg.default_model_id),
            "tools": len(toolset.tools),
        }

    @app.post("/chat")
    def chat(body: ChatBody, request: Request):
        rctx = _req_ctx(request)
        def gen():
            for ev in agent.run_chat(body.messages, toolset, adapter,
                                       model_id=body.model_id,
                                       prefer_default=cfg.default_model_id, ctx=rctx):
                yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/confirm")
    def confirm(body: ConfirmBody, request: Request):
        res = agent.confirm_and_run(body.name, body.args, toolset, adapter, ctx=_req_ctx(request))
        return JSONResponse(res)

    @app.post("/feedback")
    def feedback(body: FeedbackBody, request: Request):
        # Tier-1 self-learning: an explicit 👎 + correction becomes a durable,
        # owner-scoped preference note (data, never policy — see core.memory).
        rctx = _req_ctx(request)
        if not rctx.get("memory_enabled"):
            return JSONResponse({"ok": False, "reason": "memory_disabled"})
        res = memory.capture_feedback(rctx.get("state_dir", ""), rctx.get("owner", "anon"),
                                      body.rating, body.correction)
        return JSONResponse(res)

    @app.get("/evals")
    def evals():
        # re-read files on every request: an eval run after server start must
        # show up without a restart (no boot-time caching)
        from ..evals import history as eval_history
        from ..evals import telemetry as eval_telemetry
        entries = eval_history.load(cfg.state_dir(), n=20)
        lessons = eval_lessons.load(cfg.lessons_path())
        runtime = eval_telemetry.summary(cfg.state_dir())
        if not entries and not lessons and not runtime["calls_total"]:
            return {"evaluated": False, "project": cfg.project}
        tasks_total = 0
        task_prompts = {}
        try:
            if os.path.exists(cfg.evals_path()):
                with open(cfg.evals_path(), encoding="utf-8") as f:
                    _tasks = (json.load(f) or {}).get("tasks", [])
                tasks_total = len(_tasks)
                # id -> prompt so the console can show WHAT failed in the
                # user's own words, not an opaque task id
                task_prompts = {t.get("id"): t.get("prompt", "") for t in _tasks if t.get("id")}
        except Exception:
            pass
        return {
            "evaluated": bool(entries),
            "project": cfg.project,
            "latest": entries[-1] if entries else None,
            "trend": eval_history.trend_line(entries),
            "history": entries,
            "lessons": [{"task_id": l.get("task_id"), "class": l.get("class"),
                         "guidance": l.get("guidance")} for l in lessons],
            "tasks_total": tasks_total,
            "task_prompts": task_prompts,
            "runtime": runtime,
        }

    @app.get("/evals/ui", response_class=HTMLResponse)
    def evals_ui():
        with open(os.path.join(web_dir, "evals.html"), encoding="utf-8") as f:
            return f.read().replace("{{PROJECT}}", cfg.project)

    @app.get("/", response_class=HTMLResponse)
    def index():
        path = os.path.join(web_dir, "chat.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        return html.replace("{{PROJECT}}", cfg.project)

    return app


def serve(cfg: AgentConfig, toolset: ToolSet, host: Optional[str] = None, port: Optional[int] = None):
    import uvicorn
    app = build_app(cfg, toolset)
    uvicorn.run(app, host=host or cfg.host, port=int(port or cfg.port))
