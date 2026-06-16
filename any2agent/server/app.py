"""FastAPI server exposing the agent over a project's toolspec.

  GET  /info     -> {project, llm, models, default_model_id, tools}
  POST /chat     -> SSE stream of agent events (delta/tool/confirm/done)
  POST /confirm  -> execute a previously gated write/danger tool
  GET  /         -> minimal chat UI (project-named)

Everything is keyed by the project name loaded from <project>.any2agent.toml.
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
from ..core import registry, agent


# Defined at module level (FastAPI resolves body models by type hints; closure-local
# Pydantic models can be mis-read as query params).
class ChatBody(BaseModel):
    messages: List[Dict[str, Any]]
    model_id: Optional[str] = None


class ConfirmBody(BaseModel):
    name: str
    args: Dict[str, Any] = {}
    model_id: Optional[str] = None


def build_app(cfg: AgentConfig, toolset: ToolSet) -> FastAPI:
    app = FastAPI(title="%s Agent" % cfg.project)
    adapter = RestAdapter(cfg.base_url, cfg.auth)
    base_ctx: Dict[str, Any] = {"project": cfg.project, "state_dir": cfg.state_dir()}

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
