"""Composite tool proposal + human approval — the FR-04 half of tool-composition.

An LLM proposes candidate composite tools from the toolspec (and, when available,
the tool chains agents actually took in past evals — mined from eval-history.jsonl).
A human MUST approve each candidate before it is appended to the toolspec: there is
deliberately NO --yes for adoption, because approval is the safety boundary. Only a
--dry-run preview exists. No danger tool may appear in a composite (FR-04); the
executor and confirm gate live in core/composite.py + core/dispatch.py (FR-05).

No-key degradation (house style): with no provider key, propose() falls back to a
deterministic list→detail pairing so the feature still produces reviewable
candidates offline.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from typing import Any, Dict, List, Optional, Tuple

from .config import AgentConfig, slugify
from .spec import ToolSet, ToolSpec
from .core import registry, composite
from .evals import history as eval_history

_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")
_PATHVAR = re.compile(r"\{([^}/]+)\}")


# ── eval-history chain mining ──────────────────────────────────────────────────

def read_history_chains(state_dir: str) -> List[Tuple[List[str], int]]:
    """Aggregate multi-step tool chains recorded across eval runs, most frequent
    first. Best-effort: returns [] when nothing was recorded."""
    counts: Dict[Tuple[str, ...], int] = {}
    for entry in eval_history.load(state_dir, n=200):
        for chain in entry.get("chains") or []:
            if isinstance(chain, list) and len(chain) >= 2:
                key = tuple(chain)
                counts[key] = counts.get(key, 0) + 1
    return [(list(k), v) for k, v in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)]


def _chain_adjacent_pairs(chains: Optional[List[Tuple[List[str], int]]]) -> set:
    pairs = set()
    for chain, _ in (chains or []):
        for a, b in zip(chain, chain[1:]):
            pairs.add((a, b))
    return pairs


# ── candidate → spec + validation ──────────────────────────────────────────────

def _candidate_to_spec(cand: Dict[str, Any], by_name: Dict[str, ToolSpec]) -> ToolSpec:
    steps = cand.get("composite") or (cand.get("backing") or {}).get("composite") or []
    spec = ToolSpec(
        name=str(cand.get("name", "")).strip(),
        description=str(cand.get("description", "")).strip()[:400],
        parameters=cand.get("parameters") or {"type": "object", "properties": {}},
        backing={"composite": steps},
        domain=str(cand.get("domain", "")).strip(),
    )
    # flags are authoritative from the steps, not from whatever the candidate claimed
    spec.write, spec.danger = composite.effective_flags(spec, by_name)
    if not spec.domain:
        for st in steps:
            t = by_name.get(st.get("tool", "")) if isinstance(st, dict) else None
            if t and t.domain:
                spec.domain = t.domain
                break
    return spec


def validate_composite(spec: ToolSpec, toolset: ToolSet) -> Tuple[bool, str]:
    """Policy + structural validation before adoption. Structural rules (steps,
    danger, nesting, bindings) delegate to core.composite.validate; here we add the
    naming/collision policy."""
    by_name = toolset.by_name()
    if not _NAME_RE.match(spec.name or ""):
        return False, "invalid tool name %r" % spec.name
    if spec.name in by_name:
        return False, "name %r already exists (canonical or alias)" % spec.name
    return composite.validate(spec, by_name)


# ── proposal ────────────────────────────────────────────────────────────────────

_PROMPT = """You design COMPOSITE tools: a single tool that runs an ordered sequence of the
EXISTING tools below, passing earlier results into later calls, so the agent does a common
multi-step job in one call.

Binding syntax for a step's args (deterministic, no code runs):
  "$input.<key>"          value from the composite tool's own input args
  "$steps[i].<path>"      value from step i's result dict {ok,status,data,error}; path is
                          chained .key and [index], e.g. "$steps[0].data[0].id"

Rules:
- Each composite has >= 2 steps and references only tool names from the catalog.
- NEVER use a tool whose "danger" is true.
- If any step writes (write=true), it must be the LAST step.
- Declare in "parameters" ONLY the inputs the caller supplies (bound via $input); do not
  declare args that are filled from a previous step.

Output ONLY a JSON array, no prose:
[{"name":"<snake_case>","description":"when to use it","parameters":{"type":"object","properties":{}},
  "composite":[{"tool":"<name>","args":{"<param>":"<literal|$binding>"}}]}]

Frequent tool chains agents actually took (chain (xcount)):
%s

Tool catalog:
%s"""


def _llm_propose(toolset: ToolSet, chains, model_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
    """One completion call (no loop -> no separate budget). Returns raw candidate
    dicts or None (no key / parse failure). Monkeypatched in proposal tests."""
    entry, model_string, _ = registry.resolve(model_id)
    if not entry:
        return None
    # danger tools are excluded from the catalog entirely (defense-in-depth:
    # validate() would reject them anyway, but the proposer never sees them)
    catalog = [{"name": t.name, "description": t.description,
                "parameters": t.parameters, "write": bool(t.write)}
               for t in toolset.tools
               if not (t.defaults or {}).get("_disabled")
               and not composite.is_composite(t) and not t.danger]
    chains_txt = "; ".join("%s (x%d)" % ("->".join(c), cnt) for c, cnt in (chains or [])[:8]) or "(none recorded)"
    try:
        resp = registry.completion(
            model_string,
            [{"role": "user", "content": _PROMPT % (chains_txt, json.dumps(catalog, ensure_ascii=False)[:12000])}],
            tools=None, stream=False, extra=registry.completion_kwargs(entry))
        txt = (resp.choices[0].message.content or "").strip()
    except Exception:
        return None
    i, j = txt.find("["), txt.rfind("]")
    if i < 0 or j < 0:
        return None
    try:
        arr = json.loads(txt[i:j + 1])
    except Exception:
        return None
    return arr if isinstance(arr, list) else None


def _deterministic_propose(toolset: ToolSet, chains, n: int) -> List[Dict[str, Any]]:
    """No-LLM fallback: for each collection read whose items have a sibling detail
    route (list path + /{var}), propose 'list then fetch the first item's detail'.
    Chains seen in history sort first (source='chain'), else source='pair'."""
    from .shape import is_list_tool
    reads = [t for t in toolset.tools
             if not t.write and not t.danger
             and not (t.defaults or {}).get("_disabled") and not composite.is_composite(t)]
    lists = [t for t in reads if is_list_tool(t)]
    details = [t for t in reads if _PATHVAR.search(t.backing.get("path") or "")]
    seen_pairs = _chain_adjacent_pairs(chains)

    out: List[Dict[str, Any]] = []
    for lst in lists:
        base = (lst.backing.get("path") or "").rstrip("/")
        for det in details:
            dp = det.backing.get("path") or ""
            if not (base and dp.startswith(base + "/")):
                continue
            var = None
            for m in _PATHVAR.finditer(dp):
                var = m.group(1)
            if not var:
                continue
            resource = base.strip("/").replace("/", "_") or "items"
            out.append({
                "name": "%s_first" % det.name,
                "description": ("List %s, then fetch the first item's detail — one step "
                                "instead of listing and picking manually." % resource),
                "parameters": {"type": "object", "properties": {}},
                "composite": [
                    {"tool": lst.name, "args": {}},
                    {"tool": det.name, "args": {var: "$steps[0].data[0].id"}},
                ],
                "source": "chain" if (lst.name, det.name) in seen_pairs else "pair",
            })
    # chain-backed candidates first, then cap
    out.sort(key=lambda c: 0 if c["source"] == "chain" else 1)
    return out[:n]


def propose(toolset: ToolSet, chains=None, n: int = 6,
            model_id: Optional[str] = None) -> Tuple[List[Tuple[ToolSpec, str]], List[Dict[str, str]]]:
    """Returns (accepted, rejected). accepted = [(spec, source)] ready to adopt;
    rejected = [{name, why}] reported honestly (never silently dropped)."""
    by_name = toolset.by_name()
    raw = _llm_propose(toolset, chains, model_id)
    default_src = "llm"
    if not raw:
        raw = _deterministic_propose(toolset, chains, n)
        default_src = "pair"

    accepted: List[Tuple[ToolSpec, str]] = []
    rejected: List[Dict[str, str]] = []
    seen = set()
    for cand in raw[:n]:
        if not isinstance(cand, dict):
            rejected.append({"name": "?", "why": "candidate is not an object"})
            continue
        spec = _candidate_to_spec(cand, by_name)
        src = str(cand.get("source") or default_src)
        if spec.name in seen:
            rejected.append({"name": spec.name, "why": "duplicate candidate"})
            continue
        ok, why = validate_composite(spec, toolset)
        if not ok:
            rejected.append({"name": spec.name or "?", "why": why})
            continue
        seen.add(spec.name)
        accepted.append((spec, src))
    return accepted, rejected


# ── approval UX ──────────────────────────────────────────────────────────────

def _render(spec: ToolSpec, source: str) -> str:
    kind = "danger" if spec.danger else ("write" if spec.write else "read")
    steps = composite.steps_of(spec)
    lines = ["  %s  (%s; %d steps; source=%s)" % (spec.name, kind, len(steps), source),
             "    %s" % (spec.description or "(no description)")]
    for k, st in enumerate(steps, 1):
        lines.append("    step %d: %-20s args %s"
                     % (k, st.get("tool", "?"), json.dumps(st.get("args") or {}, ensure_ascii=False)))
    if spec.write:
        lines.append("    ⚠ contains a write step — the confirm gate applies to this composite too.")
    return "\n".join(lines)


def approve_interactive(proposals: List[Tuple[ToolSpec, str]], toolset: ToolSet,
                        dry_run: bool = False, in_fn=None, out=print) -> List[ToolSpec]:
    """Print each candidate and, unless dry_run, ask y/N. Approved specs are
    appended to `toolset` (in memory — the caller persists). Never auto-adopts."""
    in_fn = in_fn or input
    adopted: List[ToolSpec] = []
    for spec, src in proposals:
        out(_render(spec, src))
        if dry_run:
            out("    (dry-run: not adopted)")
            continue
        try:
            ans = in_fn("  Adopt this composite tool? [y/N]: ").strip().lower()
        except EOFError:
            ans = ""
        if ans.startswith("y"):
            toolset.tools.append(spec)
            adopted.append(spec)
            out("    ✅ adopted %s" % spec.name)
        else:
            out("    skipped")
    return adopted


# ── CLI entry ────────────────────────────────────────────────────────────────

def _derive_project(arg_project: Optional[str]) -> str:
    return slugify(arg_project) if arg_project else slugify(os.path.basename(os.getcwd()))


def run_compose(args) -> None:
    project = _derive_project(getattr(args, "project", None))
    cfg_path = project + ".any2agent.toml"
    if not os.path.exists(cfg_path):
        print("[compose] %s not found. Run `any2agent connect` or `init` first." % cfg_path,
              file=sys.stderr)
        sys.exit(2)
    cfg = AgentConfig.load(cfg_path)
    ts = ToolSet.load(cfg.toolspec_path())

    chains = read_history_chains(cfg.state_dir())
    if chains:
        print("[compose] mined %d recurring tool chain(s) from eval history" % len(chains))
    proposals, rejected = propose(ts, chains=chains, n=getattr(args, "n", 6),
                                  model_id=getattr(args, "model", None))
    for r in rejected:
        print("[compose] rejected candidate %s: %s" % (r["name"], r["why"]))
    if not proposals:
        print("[compose] no adoptable candidates — nothing to propose.")
        return

    dry = bool(getattr(args, "dry_run", False))
    print("\n[compose] %d candidate(s)%s:\n" % (len(proposals), " (dry-run)" if dry else ""))
    adopted = approve_interactive(proposals, ts, dry_run=dry)

    if dry:
        print("\n[compose] dry-run: the toolspec was not modified.")
        return
    if not adopted:
        print("\n[compose] nothing adopted — toolspec unchanged.")
        return

    backup = project + ".toolspec.precompose.json"
    shutil.copy(cfg.toolspec_path(), backup)   # preserve the pre-compose toolset for A/B
    ts.save(cfg.toolspec_path())
    print("\n[compose] adopted %d composite tool(s) -> %s" % (len(adopted), cfg.toolspec_path()))
    print("[compose] backed up pre-compose toolset -> %s" % backup)
    print("[compose] verify the change before relying on it:")
    print("          any2agent eval --compare %s --project %s" % (backup, project))
