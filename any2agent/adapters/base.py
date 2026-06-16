"""Adapter interface. An adapter knows how to actually invoke a ToolSpec against
a concrete system (transport + auth). The core stays transport-agnostic; swap in
new adapters (gRPC, GraphQL, MCP) without touching the runtime.
"""
from __future__ import annotations

from typing import Any, Dict

from ..spec import ToolSpec


class Adapter:
    def call(self, spec: ToolSpec, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the tool. Return a normalized dict, conventionally:
        {"ok": bool, "status": int|None, "data": Any, "error": str|None}."""
        raise NotImplementedError
