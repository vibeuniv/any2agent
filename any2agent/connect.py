"""Agentic onboarding: connect a downloaded project to an agent through a
closed generate -> verify -> repair loop, until measurable criteria are met (or
the budget is spent, with an honest residual report — never a silent "done").

OSS note: this SDK knows nothing about any specific system. The live target
(base_url + credentials) and LLM key are requested from whoever runs `connect`;
credentials are read from env only and never written to disk.
"""
from __future__ import annotations

import os
import re
import sys
from typing import Any, Dict, List, Optional

from .config import AgentConfig, slugify
from .spec import ToolSet, ToolSpec
from .scan import code as source_scan
from .scan import openapi as openapi_scan
from .scan import auth as auth_scan
from . import verifier as V
from .adapters.rest import RestAdapter
from .core import registry

MAX_ROUNDS = 4   # CTO BUDGET exit


def _ask(prompt: str, preset: Optional[str], default: str = "", interactive: bool = True) -> str:
    if preset is not None and preset != "":
        return preset
    if not interactive:
        return default
    try:
        v = input(prompt).strip()
    except EOFError:
        v = ""
    return v or default


def _guess_base_url(root: str) -> str:
    """Autonomy: infer a likely API base URL from the project's own config/source
    (.env *_URL/BASE_URL, common dev ports). Best-effort default the user confirms."""
    import os
    # prefer the app's OWN base (APP_URL/SELF/SITE/BASE_URL) over third-party service
    # URLs (supabase/auth providers), which are NOT where this project's API lives.
    primary, secondary = [], []
    _THIRD = ("supabase", "auth0", "clerk", "firebaseio", "amazonaws", "stripe", "googleapis")
    for name in (".env", ".env.local", ".env.example", ".env.development"):
        p = os.path.join(root, name)
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    m = re.match(r"\s*[A-Z_]*?(APP_URL|SITE_URL|SELF_URL|PUBLIC_URL|BASE_URL|API_URL)\s*=\s*(\S+)", line)
                    if not m:
                        continue
                    v = m.group(2).strip().strip('"\'')
                    if not v.startswith("http"):
                        continue
                    (secondary if any(x in v.lower() for x in _THIRD) else primary).append(v)
        except Exception:
            pass
    if primary:
        return primary[0].rstrip("/")
    # secondary (third-party) only if nothing better — but for self-hosted APIs prefer dev port below
    if secondary and False:
        return secondary[0].rstrip("/")
    # 2) framework default dev ports
    fw_port = {"nextjs": 3000, "express": 3000, "nestjs": 3000, "fastapi": 8000,
               "flask": 5000, "django": 8000, "spring": 8080}
    try:
        from .scan import code as _s
        fw = _s.detect_framework(root)
        if fw in fw_port:
            return "http://localhost:%d" % fw_port[fw]
    except Exception:
        pass
    return ""


def _build_source_index(root: str, cap: int = 60) -> Dict[str, str]:
    """Map an API path -> a small source snippet mentioning it, used as a hint for
    LLM parameter synthesis. Cheap best-effort scan; safe to be partial."""
    import os
    idx: Dict[str, str] = {}
    skip = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next"}
    exts = (".py", ".js", ".ts", ".tsx", ".java", ".rb", ".go")
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in skip]
        for fn in fns:
            if not fn.endswith(exts):
                continue
            try:
                with open(os.path.join(dp, fn), encoding="utf-8", errors="ignore") as f:
                    txt = f.read(8000)
            except Exception:
                continue
            for m in re.finditer(r"['\"](/[A-Za-z0-9_./{}:-]+)['\"]", txt):
                p = m.group(1)
                if p not in idx and len(idx) < cap:
                    idx[p] = txt[:1500]
    return idx


def _probes(toolset: ToolSet, n: int = 5) -> List[str]:
    # Use read-tool descriptions as natural-language probes for agent selection.
    out = []
    for t in toolset.tools:
        if not t.write and not t.danger and t.description:
            out.append(t.description)
        if len(out) >= n:
            break
    return out


def _print_report(rep: Dict[str, Any]):
    for r in rep["reports"]:
        nm = r["name"]
        st = {True: "PASS", False: "FAIL", None: "SKIP"}.get(r.get("passed"), "?")
        if nm == "coverage":
            print("  [%s] coverage  %d/%d (%.0f%%)  missing=%d"
                  % (st, r["covered"], r["total"], r["pct"] * 100, len(r["missing"])))
        elif nm == "accuracy":
            print("  [%s] accuracy  checked=%d bad=%d" % (st, r["checked"], len(r["bad"])))
        elif nm == "liveness":
            print("  [%s] liveness  ran=%d ok=%d failed=%d %s"
                  % (st, r["ran"], r["ok"], len(r["failed"]), ("· " + r["note"]) if r.get("note") else ""))
        elif nm == "agent_e2e":
            if r.get("skipped"):
                print("  [SKIP] agent_e2e  (%s)" % r["skipped"])
            else:
                okc = sum(1 for c in r["cases"] if c.get("ok"))
                print("  [%s] agent_e2e %d/%d probes selected a valid tool" % (st, okc, len(r["cases"])))


_PATHVAR_RE = re.compile(r"\{([^}/]+)\}")


def _fill_path_params(tool) -> bool:
    """Deterministic: ensure every {var} in the path exists in params."""
    props = tool.parameters.setdefault("properties", {})
    added = False
    for v in _PATHVAR_RE.findall(tool.backing.get("path", "")):
        if v not in props:
            props[v] = {"type": "string", "description": "Path parameter."}
            req = tool.parameters.setdefault("required", [])
            if v not in req:
                req.append(v)
            added = True
    return added


def _repair(toolset: ToolSet, rep: Dict[str, Any], src_index: Dict[str, str]) -> int:
    """Apply auto-fixes (deterministic first, LLM where available). Returns #changes.
    Strengthened: missing routes, path-param fill, LLM param synthesis for thin
    tools, LLM description rewrite for unselected tools, quarantine of dead tools."""
    changes = 0
    by_name = toolset.by_name()
    seen = set(by_name.keys())
    llm_on = registry.llm_available()
    try:
        from . import llm_repair as describe
    except Exception:
        describe = None

    for r in rep["reports"]:
        # 1) coverage: add code routes the toolset is missing (contract/code drift)
        if r["name"] == "coverage" and r.get("missing"):
            for route in r["missing"]:
                spec = source_scan._mk(route["method"], route["path"], seen, "")
                toolset.tools.append(spec)
                changes += 1

        # 2) accuracy: fix path params (deterministic, hard) + synth body/query (LLM, hard+warn)
        if r["name"] == "accuracy":
            name2tool = {tt.name: tt for tt in toolset.tools}
            for b in (r.get("bad") or []) + (r.get("warn") or []):
                t = name2tool.get(b["name"])
                if not t:
                    continue
                if _fill_path_params(t):
                    changes += 1
                if "empty params" in b["why"] and llm_on and describe and describe.budget_left() > 0:
                    hint = src_index.get(t.backing.get("path", ""), "")
                    if describe.synth_params(t, source_hint=hint):
                        changes += 1

        # 3) agent_e2e: rewrite descriptions of tools the model failed to select
        if r["name"] == "agent_e2e" and r.get("missed") and llm_on and describe:
            missed_descs = set(r["missed"])
            targets = [t for t in toolset.tools if t.description in missed_descs] \
                or [t for t in toolset.tools if len(t.description) < 40]
            if targets and describe.budget_left() > 0:
                describe.enrich(targets, force=True)
                changes += len(targets)

        # 4) liveness: transport failures (not authz) -> quarantine (don't ship broken)
        if r["name"] == "liveness" and r.get("failed"):
            for f in r["failed"]:
                t = {tt.name: tt for tt in toolset.tools}.get(f["name"])
                if t and not t.defaults.get("_disabled"):
                    t.defaults["_disabled"] = True   # marked; excluded from serving
                    changes += 1
    return changes


def connect(args) -> None:
    interactive = not getattr(args, "no_input", False)

    path = _ask("? Project path to connect: ", getattr(args, "path", None), ".", interactive)
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        print("[connect] path not found: %s" % path, file=sys.stderr); sys.exit(1)

    project = slugify(_ask("? Project name: ", getattr(args, "project", None),
                           os.path.basename(path.rstrip("/")), interactive))
    # autonomy: propose a base_url guessed from the project's own config/source as the default.
    guessed = _guess_base_url(path)
    if guessed and not getattr(args, "base_url", None):
        print("[connect] guessed base URL (from source): %s" % guessed)
    base_url = _ask("? Live API base URL (for verification/runtime, Enter=guess): ",
                    getattr(args, "base_url", None), guessed, interactive)

    # ---- analyze the project's auth logic -> passthrough plan ----
    auth = auth_scan.analyze(path)
    print("\n[connect] auth analysis: scheme=%s carrier=%s confidence=%s"
          % (auth.get("scheme"), auth.get("carrier"), auth.get("confidence")))
    if auth.get("carrier") == "cookie":
        print("  -> passthrough the user's session cookie(s): %s"
              % (auth.get("cookie_names") or auth.get("cookie_prefixes") or "all"))
    else:
        print("  -> passthrough the user's Bearer token (header=%s)" % auth.get("header", "Authorization"))
    if auth.get("role_source"):
        print("  - role source: %s (backend enforces RBAC; the agent keeps the user's privileges only)"
              % auth["role_source"])
    if auth.get("evidence"):
        print("  - evidence: %s" % ", ".join(auth["evidence"]))
    # allow override
    ov = _ask("? Proceed with this auth? [Y / change carrier: cookie|bearer]: ",
              getattr(args, "auth", None), "y", interactive).lower()
    if ov in ("cookie", "bearer"):
        auth["carrier"] = ov
    auth_type = "passthrough"

    want_live = base_url and registry.llm_available()
    consent = getattr(args, "live", None)
    if consent is None:
        ans = _ask("? Verify with live read-only calls? (write/danger excluded) [y/N]: ", None, "n", interactive)
        consent = ans.lower().startswith("y")
    live = bool(base_url) and bool(consent)
    # `auth` is the analyzed passthrough plan (above) — used as config.auth as-is.

    # ---- generate ----
    print("\n[connect] scanning: %s" % path)
    src_tools, src_meta = source_scan.scan(path)
    routes = src_meta["routes"]
    print("  framework=%s  routes=%d" % (src_meta["framework"], len(routes)))
    contract = openapi_scan and source_scan.find_openapi(path)
    if contract:
        print("  OpenAPI contract found -> fast path: %s" % contract)
        oa_tools, oa_meta = openapi_scan.scan(contract)
        toolset = ToolSet(project, oa_tools, {"source": path, "contract": contract, **src_meta})
    else:
        toolset = ToolSet(project, src_tools, src_meta)

    adapter = RestAdapter(base_url, auth) if base_url else None
    probes = _probes(toolset)

    # source index (path -> handler snippet) for LLM param synthesis hints; reset LLM budget.
    src_index = _build_source_index(path)
    try:
        from . import llm_repair as _desc
        _desc.reset_budget(60)
    except Exception:
        pass

    # verification session (user's own) for live RBAC probing — passthrough into ctx.
    # Supplied via flags or env; used ONLY for verification, never stored.
    sess_cookie = getattr(args, "session_cookie", None) or os.getenv("AIAGENT_VERIFY_COOKIE", "")
    sess_bearer = getattr(args, "session_bearer", None) or os.getenv("AIAGENT_VERIFY_BEARER", "")
    verify_ctx: Dict[str, Any] = {}
    if sess_cookie:
        verify_ctx["cookie"] = sess_cookie
    if sess_bearer:
        verify_ctx["in_headers"] = {"authorization": "Bearer " + sess_bearer}
    if live and not (sess_cookie or sess_bearer):
        print("  - no verification session provided -> unauthenticated probes (401/403 = authz, reported honestly)")

    # ---- verify -> repair loop ----
    prev_sig = None
    final = None
    for rnd in range(1, MAX_ROUNDS + 1):
        print("\n[connect] verify round %d/%d  (live=%s)" % (rnd, MAX_ROUNDS, live))
        rep = V.run_all(toolset, routes, adapter, probes, live=live,
                        model_id=getattr(args, "default_model", None), verify_ctx=verify_ctx)
        _print_report(rep)
        final = rep
        if rep["passed"]:
            print("[connect] ✅ all checks passed")
            break
        sig = _gap_signature(rep)
        if sig == prev_sig:
            print("[connect] ⚠ NO-PROGRESS — remaining gaps can't be auto-fixed; stopping with an honest report")
            break
        prev_sig = sig
        try:
            from . import llm_repair as _d
            if registry.llm_available() and _d.budget_left() <= 0:
                print("[connect] ⚠ LLM-BUDGET exhausted — stopping with an honest report")
                break
        except Exception:
            pass
        n = _repair(toolset, rep, src_index)
        print("  repair: %d change(s)" % n)
        if n == 0:
            print("[connect] ⚠ nothing left to auto-fix — stopping with an honest report")
            break

    # ---- write artifacts (project-named) ----
    cfg = AgentConfig(project=project, base_url=base_url, auth=auth,
                      default_model_id=getattr(args, "default_model", None) or "")
    ts_path, cfg_path = cfg.toolspec_path(), cfg.config_path()
    toolset.save(ts_path)
    cfg.save(cfg_path)
    c = toolset.counts()
    print("\n[connect] wrote: %s (tools=%d write=%d danger=%d), %s"
          % (ts_path, c["tools"], c["write"], c["danger"], cfg_path))
    _residual(final)

    # ---- chat target ----
    where = _ask("\n? Where should the chat live? [1=standalone server / 2=embed snippet / 0=later]: ",
                 getattr(args, "chat", None), "0", interactive)
    if where == "1":
        from .server.app import serve
        ts = ToolSet.load(ts_path)
        print("[connect] serving → http://%s:%d" % (cfg.host, cfg.port))
        serve(cfg, ts)
    elif where == "2":
        _print_embed(cfg)
    else:
        print("[connect] later:  any2agent serve --project %s" % project)


def _gap_signature(rep: Dict[str, Any]) -> str:
    parts = []
    for r in rep["reports"]:
        if r.get("passed") is False:
            if r["name"] == "coverage":
                parts.append("cov:" + ",".join(sorted(m["method"] + m["path"] for m in r["missing"])))
            elif r["name"] == "accuracy":
                parts.append("acc:" + ",".join(sorted(b["name"] for b in r["bad"])))
            elif r["name"] == "liveness":
                parts.append("live:" + ",".join(sorted(f["name"] for f in r["failed"])))
            elif r["name"] == "agent_e2e":
                parts.append("e2e:" + ",".join(sorted(c["probe"][:20] for c in r.get("cases", []) if not c.get("ok"))))
    return "|".join(parts)


def _residual(rep: Optional[Dict[str, Any]]):
    if not rep or rep.get("passed"):
        return
    print("[connect] residual gaps (honest report):")
    for r in rep["reports"]:
        if r.get("passed") is False:
            if r["name"] == "coverage" and r["missing"]:
                print("  - %d uncovered route(s): %s" % (len(r["missing"]),
                      ", ".join(m["method"] + " " + m["path"] for m in r["missing"][:8])))
            elif r["name"] == "liveness" and r["failed"]:
                print("  - %d live failure(s) (check path/auth): %s" % (len(r["failed"]),
                      ", ".join(f["name"] for f in r["failed"][:8])))
            elif r["name"] == "accuracy" and r["bad"]:
                print("  - %d structural error(s)" % len(r["bad"]))
            elif r["name"] == "agent_e2e":
                print("  - probes that selected no valid tool: %d" % sum(1 for c in r.get("cases", []) if not c.get("ok")))


def _print_embed(cfg: AgentConfig):
    print("\n[connect] embed snippet — add to your host page:")
    print('  <iframe src="http://%s:%d/" style="width:380px;height:560px;border:0"></iframe>' % (cfg.host, cfg.port))
    print("  (first run `any2agent serve --project %s`)" % cfg.project)
