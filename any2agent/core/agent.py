"""Agent runtime: the LLM tool-calling loop. Provider-agnostic (LiteLLM). Streams
events as plain dicts the server turns into SSE:
  {"type":"delta","text":...}        incremental assistant text
  {"type":"tool","name","args","result"}   a tool ran (read)
  {"type":"confirm","name","args","danger"}  write/danger awaiting confirmation
  {"type":"done","model"}            end of turn
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterator, List, Optional

from ..spec import ToolSet
from . import registry, toolrag, dispatch, memory
from ..adapters.base import Adapter

MAX_STEPS = 8


def _tools_payload(seed, mem_on: bool = False):
    out = [t.to_function() for t in seed]
    out.append(toolrag.SEARCH_TOOLS_DEF)
    if mem_on:
        out.extend(memory.MEMORY_TOOLS_DEF)
    return out


def _inject_lessons(msgs, lessons):
    """Prepend eval-derived guidance (see evals/lessons.py). Hints for tool
    selection only — the confirm/auth gates never read this."""
    if not lessons:
        return msgs
    from ..evals import lessons as _lessons  # lazy: keeps core import-light
    blob = _lessons.render([{"guidance": str(l)} for l in lessons])
    return [{"role": "system", "content": blob}] + msgs


def _inject_memory(msgs, state_dir: str, owner: str):
    """Prepend a system note with the user's relevant remembered facts (scored by
    the latest user message). No-op when nothing is remembered."""
    last_user = ""
    for m in reversed(msgs):
        if m.get("role") == "user":
            c = m.get("content")
            last_user = c if isinstance(c, str) else ""
            break
    # keyword-ranked when the message shares words with a note; else recent profile
    notes = memory.recall(state_dir, owner, last_user) or memory.recall(state_dir, owner, "")
    if not notes:
        return msgs
    blob = ("Facts you previously remembered about this user (use if relevant; "
            "they may be outdated, and you can update them with the memory tools):\n"
            + "\n".join("- " + n for n in notes))
    return [{"role": "system", "content": blob}] + msgs


def run_chat(messages: List[Dict[str, Any]], toolset: ToolSet, adapter: Adapter,
             model_id: Optional[str] = None, prefer_default: str = "",
             ctx: Optional[Dict[str, Any]] = None) -> Iterator[Dict[str, Any]]:
    ctx = ctx or {}
    entry, model_string, rid = registry.resolve(model_id, prefer_default)
    if not entry:
        yield {"type": "delta", "text": "No LLM provider key is set. "
               "Set a provider key (e.g. OPENAI_API_KEY) to enable chat."}
        yield {"type": "done", "model": None}
        return

    by_name = toolset.by_name()
    # exclude quarantined tools (marked _disabled by the connect repair loop)
    all_tools = [t for t in toolset.tools if not (t.defaults or {}).get("_disabled")]
    discovered: List[str] = []
    msgs = list(messages)
    extra = registry.completion_kwargs(entry)

    # memory: owner-scoped recall + remember/forget tools (when enabled & state dir known)
    state_dir = ctx.get("state_dir") or ""
    owner = ctx.get("owner") or "anon"
    mem_on = bool(ctx.get("memory_enabled")) and bool(state_dir)
    if mem_on:
        msgs = _inject_memory(msgs, state_dir, owner)
    msgs = _inject_lessons(msgs, ctx.get("lessons"))

    for _ in range(MAX_STEPS):
        seed = toolrag.build_seed(all_tools, discovered)
        tools_payload = _tools_payload(seed, mem_on)
        try:
            stream = registry.completion(model_string, msgs, tools=tools_payload, stream=True, extra=extra)
        except Exception as e:
            yield {"type": "delta", "text": "LLM call error: %s" % e}
            yield {"type": "done", "model": rid}
            return

        text_buf = ""
        tool_calls: Dict[int, Dict[str, Any]] = {}
        for chunk in stream:
            choice = (chunk.choices or [None])[0]
            if not choice:
                continue
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue
            piece = getattr(delta, "content", None)
            if piece:
                text_buf += piece
                yield {"type": "delta", "text": piece}
            for tc in (getattr(delta, "tool_calls", None) or []):
                idx = getattr(tc, "index", 0) or 0
                slot = tool_calls.setdefault(idx, {"name": "", "args": ""})
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        slot["name"] = fn.name
                    if getattr(fn, "arguments", None):
                        slot["args"] += fn.arguments

        if not tool_calls:
            yield {"type": "done", "model": rid}
            return

        # record the assistant turn (with tool_calls) for the follow-up
        msgs.append({
            "role": "assistant",
            "content": text_buf or None,
            "tool_calls": [
                {"id": "call_%d" % i, "type": "function",
                 "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                for i, c in sorted(tool_calls.items())
            ],
        })

        for i, c in sorted(tool_calls.items()):
            name = c["name"]
            try:
                args = json.loads(c["args"] or "{}")
            except Exception:
                args = {}

            if name == toolrag.SEARCH_TOOLS_NAME:
                hits = toolrag.search(args.get("query", ""), all_tools, args.get("top_k", 8))
                discovered.extend(h.name for h in hits)
                result = {"found": [{"name": h.name, "description": h.description} for h in hits]}
                yield {"type": "tool", "name": name, "args": args, "result": result}
                msgs.append(_tool_msg(i, name, result))
                continue

            if mem_on:
                handled, mres = memory.handle(name, args, state_dir, owner)
                if handled:
                    yield {"type": "tool", "name": name, "args": args, "result": mres}
                    msgs.append(_tool_msg(i, name, mres))
                    continue

            spec = by_name.get(name)
            if not spec:
                result = {"ok": False, "error": "unknown_tool"}
                msgs.append(_tool_msg(i, name, result))
                continue

            res = dispatch.execute(spec, args, adapter, ctx=ctx, confirmed=False, toolset=toolset)
            if res.get("confirm_required"):
                if ctx.get("auto_confirm"):
                    # eval harness only: headless run with up-front consent (--live-write)
                    res = dispatch.execute(spec, args, adapter, ctx=ctx, confirmed=True, toolset=toolset)
                else:
                    yield {"type": "confirm", "name": name, "args": args, "danger": res.get("danger", False),
                           "message": res.get("message", "")}
                    # stop the turn; client confirms then calls /confirm
                    yield {"type": "done", "model": rid}
                    return
            yield {"type": "tool", "name": name, "args": args, "result": res}
            msgs.append(_tool_msg(i, name, res))
        # loop continues: feed tool results back to the model

    yield {"type": "done", "model": rid}


def confirm_and_run(name: str, args: Dict[str, Any], toolset: ToolSet, adapter: Adapter,
                    ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    spec = toolset.by_name().get(name)
    if not spec:
        return {"ok": False, "error": "unknown_tool"}
    return dispatch.execute(spec, args or {}, adapter, ctx=ctx or {}, confirmed=True, toolset=toolset)


def _tool_msg(idx: int, name: str, result: Any) -> Dict[str, Any]:
    return {"role": "tool", "tool_call_id": "call_%d" % idx, "name": name,
            "content": json.dumps(result, ensure_ascii=False)[:6000]}
