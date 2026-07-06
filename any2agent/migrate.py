"""FR-07 migration command — modernize old tool-name references after shaping.

Deterministic tool shaping (shape.py) renames mechanical names like `get__notes`
to `notes_list` and keeps the old name as an alias, so curated files that still
reference `get__notes` KEEP WORKING (ToolSet.by_name resolves aliases). This
command is the optional, explicit step to rewrite those files to the new names —
it changes nothing about behavior, only tidies the human-curated assets.

Sources of truth for the rename map (old -> new): each tool's `aliases` (its
former names, all pointing at the current canonical name) plus
`meta.shaping.renamed` (new -> old). A name that still exists as a live canonical
tool is never treated as an old name — rewriting it would be ambiguous.

Targets:
  - <project>.evals.json      — expected_tools paths, checks[].tool, checks[].any_of,
                                cleanup[].tool (exact-name fields)
  - <project>.eval-lessons.json — whole-word occurrences inside guidance strings
                                (word boundaries so `notes` never corrupts `notes_list`)
  - any --files JSON          — generic: any string VALUE anywhere in the tree that
                                exactly equals an old name

Safety: --dry-run prints a per-file change count + samples and writes nothing; a
real run writes a `<file>.premigrate.bak` beside each file it changes before
overwriting it; the rewrite is idempotent (a second run finds 0 changes); files
that don't exist are reported and skipped (honest, never silently). Exit codes:
0 = done (even with 0 changes), 2 = config or toolspec missing.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from .config import AgentConfig, slugify
from .spec import ToolSet


# ── rename map ─────────────────────────────────────────────────────────────────

def build_rename_map(toolset: ToolSet) -> Dict[str, str]:
    """old name -> current name. Unions the two consistent sources (aliases and
    meta.shaping.renamed) and drops any 'old' name that is still a live canonical
    tool (its references already resolve correctly; rewriting would be wrong)."""
    current = {t.name for t in toolset.tools}
    rmap: Dict[str, str] = {}
    for t in toolset.tools:
        for a in t.aliases:
            if a and a != t.name:
                rmap[a] = t.name
    renamed = ((toolset.meta or {}).get("shaping") or {}).get("renamed") or {}
    for new, old in renamed.items():
        if old and new and old != new:
            rmap.setdefault(old, new)
    return {old: new for old, new in rmap.items() if old not in current}


# ── rewriters (pure: operate on already-parsed JSON, return change count) ───────

def _sub_exact(name: Any, rmap: Dict[str, str], samples: List[str]) -> Tuple[Any, int]:
    """Rewrite a single value only if it is a string that exactly matches an old
    name. Returns (value, changed?) so callers can total the count."""
    if isinstance(name, str) and name in rmap:
        new = rmap[name]
        _add_sample(samples, name, new)
        return new, 1
    return name, 0


def _add_sample(samples: List[str], old: str, new: str) -> None:
    s = "%s -> %s" % (old, new)
    if s not in samples and len(samples) < 5:
        samples.append(s)


def rewrite_evals_doc(doc: Dict[str, Any], rmap: Dict[str, str]) -> Tuple[int, List[str]]:
    """Rewrite the exact-name fields of every task in a loaded evals.json doc,
    in place. Fields: expected_tools (list of name-lists), checks[].tool,
    checks[].any_of (list), cleanup[].tool."""
    changes = 0
    samples: List[str] = []
    for task in doc.get("tasks", []) or []:
        if not isinstance(task, dict):
            continue
        paths = task.get("expected_tools")
        if isinstance(paths, list):
            for path in paths:
                if isinstance(path, list):
                    for k, n in enumerate(path):
                        path[k], c = _sub_exact(n, rmap, samples)
                        changes += c
        for c in task.get("checks") or []:
            if not isinstance(c, dict):
                continue
            if "tool" in c:
                c["tool"], ch = _sub_exact(c.get("tool"), rmap, samples)
                changes += ch
            anyof = c.get("any_of")
            if isinstance(anyof, list):
                for k, n in enumerate(anyof):
                    anyof[k], ch = _sub_exact(n, rmap, samples)
                    changes += ch
        for c in task.get("cleanup") or []:
            if isinstance(c, dict) and "tool" in c:
                c["tool"], ch = _sub_exact(c.get("tool"), rmap, samples)
                changes += ch
    return changes, samples


def _word_pattern(rmap: Dict[str, str]) -> Optional["re.Pattern[str]"]:
    if not rmap:
        return None
    # longest first so a longer old name wins over a shorter one that prefixes it;
    # \b around each — and underscores being word chars — means `notes` won't match
    # inside `notes_list` (there's no word boundary between `notes` and `_`).
    keys = sorted((re.escape(k) for k in rmap), key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(keys) + r")\b")


def rewrite_lessons_doc(doc: Dict[str, Any], rmap: Dict[str, str]) -> Tuple[int, List[str]]:
    """Whole-word rewrite of old names inside each lesson's `guidance` string, in
    place. One combined pass (no chained replacement), so a new name that happens
    to equal another old name is never re-rewritten."""
    pat = _word_pattern(rmap)
    if pat is None:
        return 0, []
    changes = 0
    samples: List[str] = []

    def repl(m: "re.Match[str]") -> str:
        nonlocal changes
        old = m.group(0)
        new = rmap[old]
        changes += 1
        _add_sample(samples, old, new)
        return new

    for l in doc.get("lessons", []) or []:
        if isinstance(l, dict) and isinstance(l.get("guidance"), str):
            l["guidance"] = pat.sub(repl, l["guidance"])
    return changes, samples


def rewrite_generic(node: Any, rmap: Dict[str, str], samples: List[str]) -> Tuple[Any, int]:
    """Generic tree rewrite: replace any string VALUE that exactly equals an old
    name, anywhere. Dict keys are left untouched (they are field names, not tool
    references). Returns (new_node, change_count)."""
    if isinstance(node, dict):
        total = 0
        out: Dict[str, Any] = {}
        for k, v in node.items():
            out[k], c = rewrite_generic(v, rmap, samples)
            total += c
        return out, total
    if isinstance(node, list):
        total = 0
        out_l: List[Any] = []
        for v in node:
            nv, c = rewrite_generic(v, rmap, samples)
            out_l.append(nv)
            total += c
        return out_l, total
    return _sub_exact(node, rmap, samples)


# ── file orchestration ──────────────────────────────────────────────────────────

def _process_file(kind: str, fpath: str, rmap: Dict[str, str], dry_run: bool) -> Dict[str, Any]:
    """Load, rewrite (per kind), and (unless dry-run) back up + write one file.
    Returns a status dict: {file, exists, changes, samples, wrote_backup, error?}."""
    st: Dict[str, Any] = {"file": fpath, "exists": os.path.exists(fpath),
                          "changes": 0, "samples": [], "wrote_backup": False}
    if not st["exists"]:
        return st
    try:
        with open(fpath, encoding="utf-8") as f:
            raw = f.read()
        doc = json.loads(raw)
    except Exception as e:
        st["error"] = "unreadable (%s)" % e
        return st

    if kind == "evals":
        changes, samples = rewrite_evals_doc(doc, rmap)
        new_doc = doc
    elif kind == "lessons":
        changes, samples = rewrite_lessons_doc(doc, rmap)
        new_doc = doc
    else:  # generic --files
        samples = []
        new_doc, changes = rewrite_generic(doc, rmap, samples)
    st["changes"], st["samples"] = changes, samples

    if changes and not dry_run:
        try:
            with open(fpath + ".premigrate.bak", "w", encoding="utf-8") as f:
                f.write(raw)
            st["wrote_backup"] = True
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(new_doc, f, ensure_ascii=False, indent=2)
        except Exception as e:
            st["error"] = "write failed (%s)" % e
    return st


def _print_file(st: Dict[str, Any], dry_run: bool) -> None:
    f = st["file"]
    if not st["exists"]:
        print("[migrate]   %s — not found, skipped" % f)
        return
    if st.get("error"):
        print("[migrate]   %s — %s, skipped" % (f, st["error"]))
        return
    verb = "would change" if dry_run else "changed"
    print("[migrate]   %s — %s %d reference(s)%s"
          % (f, verb, st["changes"],
             ("  [backup -> %s.premigrate.bak]" % f) if st.get("wrote_backup") else ""))
    for s in st["samples"]:
        print("[migrate]       %s" % s)


# ── entry point ─────────────────────────────────────────────────────────────────

def run_migrate(args) -> int:
    """Orchestrate the migration. Returns a process exit code (0 done, 2 missing
    config/toolspec) — cli.cmd_migrate turns it into sys.exit."""
    project = slugify(args.project) if getattr(args, "project", None) else slugify(os.path.basename(os.getcwd()))
    cfg_path = project + ".any2agent.toml"
    if not os.path.exists(cfg_path):
        print("[migrate] %s not found. Run `any2agent init` or `connect` first." % cfg_path, file=sys.stderr)
        return 2
    cfg = AgentConfig.load(cfg_path)
    ts_path = cfg.toolspec_path()
    if not os.path.exists(ts_path):
        print("[migrate] %s not found — nothing to build a rename map from." % ts_path, file=sys.stderr)
        return 2

    toolset = ToolSet.load(ts_path)
    rmap = build_rename_map(toolset)
    dry_run = bool(getattr(args, "dry_run", False))
    print("[migrate] project=%s  renames known=%d%s"
          % (project, len(rmap), "  (dry-run — nothing will be written)" if dry_run else ""))
    if not rmap:
        print("[migrate] no old->new tool renames recorded — files already use current names. Done.")
        return 0

    targets: List[Tuple[str, str]] = [("evals", cfg.evals_path()), ("lessons", cfg.lessons_path())]
    extra = getattr(args, "files", None)
    if extra:
        for f in extra.split(","):
            f = f.strip()
            if f:
                targets.append(("generic", f))

    total = 0
    for kind, fpath in targets:
        st = _process_file(kind, fpath, rmap, dry_run)
        _print_file(st, dry_run)
        total += st["changes"]

    if dry_run:
        print("[migrate] dry-run complete — %d reference(s) would change across %d file(s)."
              % (total, len(targets)))
    else:
        print("[migrate] done — %d reference(s) rewritten. Backups: <file>.premigrate.bak" % total)
    return 0
