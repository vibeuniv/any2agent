"""Tool dispatch with a safety gate. Read tools execute immediately; write/danger
tools require explicit confirmation (the server surfaces a confirm card, then
re-invokes with confirmed=True). Transport is delegated to the adapter, so this
stays system-agnostic.

Composite tools (backing.composite — a multi-step sequence) are delegated to
core.composite; their effective write/danger is the MAX of their steps, so the
same confirm gate applies to the composite as a whole before any step runs.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..adapters.base import Adapter
from ..spec import ToolSpec, ToolSet
from . import composite


def execute(spec: ToolSpec, args: Dict[str, Any], adapter: Adapter,
            ctx: Dict[str, Any] | None = None, confirmed: bool = False,
            toolset: Optional[ToolSet] = None) -> Dict[str, Any]:
    ctx = ctx or {}

    if composite.is_composite(spec):
        if toolset is None:
            # can't resolve step tools -> refuse rather than silently succeed
            return {"ok": False, "composite": spec.name,
                    "error": "composite requires a toolset to resolve steps"}
        by_name = toolset.by_name()
        write, danger = composite.effective_flags(spec, by_name)
        if (write or danger) and not confirmed:
            return {
                "confirm_required": True,
                "tool": spec.name,
                "danger": bool(danger),
                "args": args,
                "composite": True,
                "steps": len(composite.steps_of(spec)),
                "message": ("⚠️ Destructive / irreversible action" if danger
                            else "⚠️ Multi-step write action")
                           + " — confirmation required before running.",
            }
        return composite.run(spec, args or {}, adapter, ctx=ctx, confirmed=confirmed, by_name=by_name)

    if (spec.write or spec.danger) and not confirmed:
        return {
            "confirm_required": True,
            "tool": spec.name,
            "danger": bool(spec.danger),
            "args": args,
            "message": ("⚠️ Destructive / irreversible action" if spec.danger else "⚠️ Write action")
                       + " — confirmation required before running.",
        }
    return adapter.call(spec, args or {}, ctx)
