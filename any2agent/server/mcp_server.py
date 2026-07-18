"""Expose a verified tool set as an MCP server (stdio).

This is an OUTPUT of any2agent, not a new engine: the scan → verify → repair →
eval pipeline is unchanged. Here we just take the tool set it already produced and
serve it over the Model Context Protocol so it plugs straight into Cursor, Claude
Desktop, or any MCP client — instead of only any2agent's own chat UI.

Mapping is 1:1 with the existing ToolSpec:
    ToolSpec.name/description/parameters -> MCP Tool name/description/inputSchema
    ToolSpec.write / ToolSpec.danger      -> ToolAnnotations readOnlyHint / destructiveHint
    dispatch.execute(...)                 -> MCP call_tool handler

Confirmation: MCP clients are themselves the human-in-the-loop (they show each
tool call for approval), so we dispatch with confirmed=True and rely on the
destructiveHint annotation to warn. any2agent's own confirm gate is for its chat
UI; under MCP the client owns that gate.

Requires the `mcp` package (Python 3.10+): install with `any2agent[mcp]`.
The import is deferred so the rest of any2agent keeps working on Python 3.9.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

from ..adapters.rest import RestAdapter
from ..config import AgentConfig
from ..core import dispatch
from ..spec import ToolSet


def _require_mcp():
    try:
        import mcp.types as types  # noqa: F401
        from mcp.server import Server  # noqa: F401
        from mcp.server.stdio import stdio_server  # noqa: F401
    except ModuleNotFoundError as e:  # pragma: no cover - env-dependent
        raise SystemExit(
            "The MCP server needs the 'mcp' package (Python 3.10+).\n"
            "Install it with:  pip install 'any2agent[mcp]'"
        ) from e


def build_server(cfg: AgentConfig, toolset: ToolSet, adapter):
    """Build (but don't run) an MCP Server backed by a verified tool set."""
    import mcp.types as types
    from mcp.server import Server

    server = Server(cfg.project or "any2agent")
    by_name = toolset.by_name()

    @server.list_tools()
    async def list_tools() -> List["types.Tool"]:
        tools: List[types.Tool] = []
        for spec in toolset.tools:
            read_only = not (spec.write or spec.danger)
            tools.append(
                types.Tool(
                    name=spec.name,
                    description=spec.description or spec.name,
                    inputSchema=spec.parameters or {"type": "object", "properties": {}},
                    annotations=types.ToolAnnotations(
                        readOnlyHint=read_only,
                        destructiveHint=bool(spec.danger),
                    ),
                )
            )
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any] | None):
        spec = by_name.get(name)
        if spec is None:
            return [types.TextContent(type="text", text=json.dumps(
                {"ok": False, "error": "unknown tool: %s" % name}, ensure_ascii=False))]
        # The MCP client already gate-keeps the call; run it (confirmed=True).
        # dispatch.execute stays sync; adapter.call is stdlib urllib, so run it in
        # a thread to avoid blocking the asyncio event loop.
        result = await asyncio.to_thread(
            dispatch.execute, spec, arguments or {}, adapter, {}, True, toolset
        )
        text = json.dumps(result, ensure_ascii=False, default=str)
        return [types.TextContent(type="text", text=text)]

    return server


def serve_mcp(cfg: AgentConfig, toolset: ToolSet) -> None:
    """Run the MCP stdio server. Blocks until the client disconnects."""
    _require_mcp()
    from mcp.server.stdio import stdio_server

    adapter = RestAdapter(cfg.base_url, cfg.auth)
    server = build_server(cfg, toolset, adapter)

    async def _run() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(_run())
