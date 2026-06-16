"""Tool dispatch with a safety gate. Read tools execute immediately; write/danger
tools require explicit confirmation (the server surfaces a confirm card, then
re-invokes with confirmed=True). Transport is delegated to the adapter, so this
stays system-agnostic.
"""
from __future__ import annotations

from typing import Any, Dict

from ..adapters.base import Adapter
from ..spec import ToolSpec


def execute(spec: ToolSpec, args: Dict[str, Any], adapter: Adapter,
            ctx: Dict[str, Any] | None = None, confirmed: bool = False) -> Dict[str, Any]:
    ctx = ctx or {}
    if (spec.write or spec.danger) and not confirmed:
        return {
            "confirm_required": True,
            "tool": spec.name,
            "danger": bool(spec.danger),
            "args": args,
            "message": ("⚠️ 위험·비가역 작업" if spec.danger else "⚠️ 변경 작업")
                       + " — 실행하려면 확인이 필요합니다.",
        }
    return adapter.call(spec, args or {}, ctx)
