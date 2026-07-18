"""any2agent CLI — init (scan + config), scan (toolspec only), serve (run agent).

Turnkey flow:
    any2agent init --openapi <url|file> --project myapp --base-url https://api.myapp.com
    any2agent serve --project myapp

All artifacts are named after the project: <project>.toolspec.json, <project>.any2agent.toml.
"""
from __future__ import annotations

import argparse
import os
import sys

# Load .env from the working directory if python-dotenv is available (optional —
# never required; shell-exported env vars work too).
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.getcwd(), ".env"))
except Exception:
    pass

from .config import AgentConfig, slugify
from .spec import ToolSet


def _derive_project(arg_project: str | None) -> str:
    if arg_project:
        return slugify(arg_project)
    return slugify(os.path.basename(os.getcwd()))


def _do_scan(args) -> ToolSet:
    from .scan import openapi
    tools, meta = openapi.scan(args.openapi)
    project = _derive_project(args.project)
    if getattr(args, "describe", False):
        from .llm_repair import enrich
        tools = enrich(tools)
    ts = ToolSet(project=project, tools=tools, meta=meta)
    return ts


def cmd_scan(args):
    ts = _do_scan(args)
    out = ts.project + ".toolspec.json"
    ts.save(out)
    c = ts.counts()
    print("[scan] %s  tools=%d write=%d danger=%d  -> %s"
          % (ts.project, c["tools"], c["write"], c["danger"], out))


def cmd_init(args):
    ts = _do_scan(args)
    cfg = AgentConfig(
        project=ts.project,
        base_url=args.base_url or "",
        auth={"type": args.auth} if args.auth == "none" else {"type": args.auth, "token_env": args.token_env or (ts.project.upper() + "_TOKEN")},
        default_model_id=args.default_model or "",
    )
    if args.auth == "api_key_header":
        cfg.auth["header"] = args.header or "X-API-Key"
    if args.auth == "cookie":
        cfg.auth["name"] = args.cookie_name or "SESSION"
    ts_path = cfg.toolspec_path()
    cfg_path = cfg.config_path()
    ts.save(ts_path)
    cfg.save(cfg_path)
    c = ts.counts()
    print("[init] project=%s  tools=%d (write=%d danger=%d)" % (ts.project, c["tools"], c["write"], c["danger"]))
    print("       toolspec -> %s" % ts_path)
    print("       config   -> %s" % cfg_path)
    if not cfg.base_url:
        print("       ⚠ base_url is empty — set base_url in %s to your target API." % cfg_path)
    print("       next:  any2agent serve --project %s" % ts.project)


def cmd_connect(args):
    from .connect import connect
    connect(args)


def cmd_compose(args):
    """Propose composite (multi-step) tools and interactively approve them into the
    toolspec. Approval is required for every candidate — there is no --yes."""
    from .compose import run_compose
    run_compose(args)


def cmd_migrate(args):
    """Rewrite old (pre-shaping) tool-name references in curated files to the
    current names. Aliases already keep old references working; this only
    modernizes the files. --dry-run previews; a real run backs up before writing."""
    from .migrate import run_migrate
    sys.exit(run_migrate(args))


def cmd_eval(args):
    """Task-based self-verification: run realistic tasks through the real agent
    loop against the live API and gate on the completion rate. CI-friendly:
    exit 0 = gate passed, 1 = below threshold, 2 = cannot run."""
    import json as _json
    from . import verifier as V
    from .adapters.rest import RestAdapter
    from .core import registry
    from .evals import budget as eval_budget
    from .evals import history as eval_history
    from .evals import lessons as eval_lessons
    from .evals import tasks as eval_tasks

    project = _derive_project(args.project)
    cfg_path = project + ".any2agent.toml"
    if not os.path.exists(cfg_path):
        print("[eval] %s not found. Run `any2agent connect` or `init` first." % cfg_path, file=sys.stderr)
        sys.exit(2)
    cfg = AgentConfig.load(cfg_path)

    if getattr(args, "history", False):
        entries = eval_history.load(cfg.state_dir(), n=10)
        if not entries:
            print("[eval] no recorded runs yet.")
            sys.exit(0)
        import datetime as _dt
        for e in entries:
            print("  %s  rate=%.2f rated=%d %s failed=%s"
                  % (_dt.datetime.fromtimestamp(e["ts"]).strftime("%Y-%m-%d %H:%M"),
                     e.get("rate", 0.0), e.get("rated", 0),
                     "PASS" if e.get("passed") else "FAIL",
                     ",".join(e.get("failed", [])) or "-"))
        print("[eval] " + eval_history.trend_line(entries))
        sys.exit(0)

    ts = ToolSet.load(cfg.toolspec_path())
    if not cfg.base_url:
        print("[eval] base_url is empty in %s — a live target is required." % cfg_path, file=sys.stderr)
        sys.exit(2)
    if not registry.llm_available():
        print("[eval] no LLM provider key set — the agent loop cannot run.", file=sys.stderr)
        sys.exit(2)

    write_ok = bool(getattr(args, "live_write", False))
    if write_ok and not getattr(args, "yes", False) and sys.stdin.isatty():
        ans = input("? --live-write runs WRITE tools against %s. Confirm this is NOT production [y/N]: "
                    % cfg.base_url).strip().lower()
        write_ok = ans.startswith("y")
        if not write_ok:
            print("[eval] write tasks disabled — running read tasks only.")

    _votes = getattr(args, "judge_votes", 1) or 1
    eval_budget.reset((getattr(args, "budget", None) or 40) * _votes)  # k judge draws per task
    tasks, invalid = eval_tasks.load_or_generate(
        ts, cfg.evals_path(), n=args.n, regen=getattr(args, "regen", False),
        model_id=getattr(args, "model", None))
    if invalid:
        print("[eval] %d invalid task(s) excluded:" % len(invalid))
        for iv in invalid[:8]:
            print("  - %s: %s" % (iv["id"], iv["why"]))
    if not tasks:
        print("[eval] no runnable tasks — curate %s or fix the toolspec." % cfg.evals_path(), file=sys.stderr)
        sys.exit(2)

    from .config import verify_ctx_from_env
    verify_ctx = verify_ctx_from_env()

    adapter = RestAdapter(cfg.base_url, cfg.auth)
    n_read = sum(1 for t in tasks if t.kind == "read")
    print("[eval] tasks=%d (read=%d write=%d)  target=%s" % (len(tasks), n_read, len(tasks) - n_read, cfg.base_url))

    # --compare: A/B the current toolset against an older one on the SAME tasks.
    # The old run is measurement only — it never touches history or lessons.
    compare_path = getattr(args, "compare", None)
    old_rep = None
    if compare_path:
        try:
            old_ts = ToolSet.load(compare_path)
        except FileNotFoundError:
            print("[eval] --compare file not found: %s" % compare_path, file=sys.stderr)
            sys.exit(2)
        except Exception as e:
            print("[eval] --compare file unreadable (%s): %s" % (e, compare_path), file=sys.stderr)
            sys.exit(2)
        old_tasks, old_invalid = eval_tasks.validate(tasks, old_ts)
        if old_invalid:
            print("[compare] %d task(s) not runnable on the OLD toolset (excluded there): %s"
                  % (len(old_invalid), ",".join(iv["id"] for iv in old_invalid[:6])))
        eval_budget.reset((getattr(args, "budget", None) or 40) * 2 * _votes)  # two runs × k votes
        print("[compare] running OLD toolset (%s)…" % compare_path)
        old_rep = V.task_eval(old_ts, adapter, old_tasks, model_id=getattr(args, "model", None),
                              threshold=args.threshold, write_ok=write_ok, verify_ctx=verify_ctx,
                              judge_model=getattr(args, "judge_model", None))
        print("[compare] running CURRENT toolset…")

    rep = V.task_eval(ts, adapter, tasks, model_id=getattr(args, "model", None),
                      threshold=args.threshold, write_ok=write_ok, verify_ctx=verify_ctx,
                      judge_model=getattr(args, "judge_model", None),
                      strict=getattr(args, "strict", False),
                      judge_votes=getattr(args, "judge_votes", 1) or 1)

    if rep.get("skipped"):
        print("[eval] skipped: %s" % rep["skipped"], file=sys.stderr)
        sys.exit(2)
    by_id = {t.id: t for t in tasks}
    for r in rep["results"]:
        mark = "PASS" if r["success"] else ("SKIP" if r["ungraded"] else "FAIL")
        print("  [%s] %-20s tools=%d checks %d/%d" % (
            mark, r["task_id"], r["metrics"].get("tool_calls", 0),
            r["checks_passed"], r["checks_total"]))
    m = rep["metrics"]
    ci = rep.get("rate_ci", [0, 1])
    power = " ⚠underpowered(+%d tasks for a trustworthy gate)" % rep.get("add_tasks_for_power", 0) \
        if rep.get("underpowered") else ""
    print("[eval] rate=%.2f  95%% CI [%.2f, %.2f]  (threshold %.2f%s)  rated=%d%s"
          % (rep["rate"], ci[0], ci[1], rep["threshold"],
             ", strict" if rep.get("strict") else "", rep["rated"], power))
    print("       wrong_tool=%d  errors=%d  skipped_write=%d  skipped_budget=%d  infra=%d  ungraded=%d"
          % (m["wrong_tool_calls"], m["tool_errors"], rep["skipped_write"],
             rep["skipped_budget"], rep["infra_errors"], rep["ungraded"]))
    for res in rep["residue"]:
        print("  ⚠ residue (manual cleanup needed): task=%s tool=%s (%s)"
              % (res["task"], res["tool"], res["why"]))

    # failures → curated "what to fix" lines + persisted lessons (raw reasons live in --json)
    built = eval_lessons.build(rep, by_id)

    # history + trend — the one line that answers "better or worse than last time?"
    eval_history.append(cfg.state_dir(), rep, fixes=built)
    print("[eval] history: " + eval_history.trend_line(eval_history.load(cfg.state_dir())))
    passed_ids = [r["task_id"] for r in rep["results"] if r["success"]]
    kept = eval_lessons.merge_save(cfg.lessons_path(), project, built, passed_ids, ts)
    if built:
        print("[eval] what to fix:")
        for l in built:
            print("  - %s [%s] %s" % (l["task_id"], l["class"], l["guidance"]))
    if kept or built:
        print("[eval] lessons -> %s (%d active; injected as guidance at serve time)"
              % (cfg.lessons_path(), len(kept)))

    # --fix: apply the repair channels now (description rewrite w/ failure
    # context, param synthesis from 4xx) instead of waiting for connect --eval
    if built and getattr(args, "fix", False):
        if not registry.llm_available():
            print("[eval] --fix needs a provider key — skipped.")
        else:
            from .connect import _eval_repair
            n = _eval_repair(ts, rep, by_id)
            if n:
                ts.save(cfg.toolspec_path())
                print("[eval] fix: %d change(s) -> %s — re-run eval to confirm."
                      % (n, cfg.toolspec_path()))
            else:
                print("[eval] fix: nothing auto-fixable (see lessons for manual guidance).")

    if old_rep is not None and not old_rep.get("skipped"):
        om, nm = old_rep["metrics"], rep["metrics"]
        # paired McNemar: same task set, so compare per-task pass/fail. Only the
        # tasks that CHANGED verdict carry signal (b: old-pass→new-fail, c: reverse).
        from .evals import stats
        old_pass = {r["task_id"]: r["success"] for r in old_rep["results"]}
        b = c = 0
        for r in rep["results"]:
            tid = r["task_id"]
            if tid in old_pass:
                if old_pass[tid] and not r["success"]:
                    b += 1
                elif not old_pass[tid] and r["success"]:
                    c += 1
        pval = stats.mcnemar_exact(b, c)
        print("[compare] old rate=%.2f  new rate=%.2f   changed tasks: %d worse, %d better  (McNemar p=%.2f)"
              % (old_rep["rate"], rep["rate"], b, c, pval))
        print("          avg tool-calls: old %.1f → new %.1f" % (om["avg_tool_calls"], nm["avg_tool_calls"]))
        if b + c < 3:
            print("[compare] verdict: 🤷 inconclusive — only %d task(s) changed; add tasks (--n) for a real signal" % (b + c))
        elif c > b and pval < 0.05:
            print("[compare] verdict: ✅ new toolset significantly better — keep it")
        elif b > c and pval < 0.05:
            print("[compare] verdict: ❌ new toolset significantly worse — revert to %s" % compare_path)
        else:
            print("[compare] verdict: ➖ no significant difference — decide on call count / cost")

    if getattr(args, "json", None):
        out = {"current": rep, "old": old_rep} if old_rep is not None else rep
        with open(args.json, "w", encoding="utf-8") as f:
            _json.dump(out, f, ensure_ascii=False, indent=2)
        print("[eval] report -> %s" % args.json)
    if rep["passed"]:
        print("[eval] ✅ gate passed")
        sys.exit(0)
    print("[eval] ❌ below threshold — failed: %s" % (", ".join(rep["failed"]) or "(residue/ungraded)"))
    sys.exit(1)


def cmd_serve(args):
    project = _derive_project(args.project)
    cfg_path = project + ".any2agent.toml"
    if not os.path.exists(cfg_path):
        print("[serve] %s not found. Run `any2agent init` first." % cfg_path, file=sys.stderr)
        sys.exit(1)
    cfg = AgentConfig.load(cfg_path)
    ts = ToolSet.load(cfg.toolspec_path())
    from .server.app import serve
    host = args.host or cfg.host
    port = args.port or cfg.port
    print("[serve] %s — http://%s:%d  (tools=%d)" % (cfg.project, host, port, len(ts.tools)))
    serve(cfg, ts, host=host, port=port)


def cmd_mcp(args):
    project = _derive_project(args.project)
    cfg_path = project + ".any2agent.toml"
    if not os.path.exists(cfg_path):
        print("[mcp] %s not found. Run `any2agent init` first." % cfg_path, file=sys.stderr)
        sys.exit(1)
    cfg = AgentConfig.load(cfg_path)
    ts = ToolSet.load(cfg.toolspec_path())
    from .server.mcp_server import serve_mcp
    # stdout is the MCP transport — keep it clean, log to stderr only.
    print("[mcp] %s — serving %d verified tools over stdio" % (cfg.project, len(ts.tools)),
          file=sys.stderr)
    serve_mcp(cfg, ts)


def main(argv=None):
    p = argparse.ArgumentParser(prog="any2agent", description="Turn an API contract into a conversational agent.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_scan_args(sp):
        sp.add_argument("--openapi", required=True, help="OpenAPI/Swagger contract (URL or file)")
        sp.add_argument("--project", help="Project name (default: current dir name)")
        sp.add_argument("--describe", action="store_true", help="Enrich descriptions via LLM (needs a provider key)")

    sp = sub.add_parser("scan", help="Scan a contract -> <project>.toolspec.json")
    add_scan_args(sp); sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("init", help="Scan + write <project>.toolspec.json and <project>.any2agent.toml")
    add_scan_args(sp)
    sp.add_argument("--base-url", help="Target API base URL")
    sp.add_argument("--auth", choices=["none", "bearer", "api_key_header", "cookie"], default="none")
    sp.add_argument("--token-env", help="Env var holding the credential")
    sp.add_argument("--header", help="Header name for api_key_header auth")
    sp.add_argument("--cookie-name", help="Cookie name for cookie auth")
    sp.add_argument("--default-model", help="Default model id: gpt|kimi|claude|gemini")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("serve", help="Run the agent (chat UI + API)")
    sp.add_argument("--project", help="Project name (default: current dir name)")
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("mcp", help="Serve the verified tools as an MCP server (stdio) for Cursor/Claude/etc. [needs any2agent[mcp], Python 3.10+]")
    sp.add_argument("--project", help="Project name (default: current dir name)")
    sp.set_defaults(func=cmd_mcp)

    sp = sub.add_parser("connect", help="Agentic onboarding: scan a source tree -> verify/repair loop -> agent")
    sp.add_argument("--path", help="Path to the target project source tree")
    sp.add_argument("--project", help="Project name (default: target dir name)")
    sp.add_argument("--base-url", help="Live API base URL (for verification/runtime)")
    sp.add_argument("--auth", choices=["none", "bearer", "api_key_header", "cookie"])
    sp.add_argument("--default-model")
    sp.add_argument("--live", dest="live", action="store_true", default=None, help="Consent to live read probing")
    sp.add_argument("--no-live", dest="live", action="store_false", help="Disable live probing")
    sp.add_argument("--session-cookie", help="User's session cookie for live RBAC verification (not stored)")
    sp.add_argument("--session-bearer", help="User's bearer token for live RBAC verification (not stored)")
    sp.add_argument("--chat", help="Chat target: 1=standalone 2=embed 0=later")
    sp.add_argument("--no-input", action="store_true", help="Non-interactive (use flags/defaults only)")
    sp.add_argument("--eval", dest="eval_gate", action="store_true",
                    help="After the verify loop, run the task-based eval as a final gate")
    sp.add_argument("--no-shape", action="store_true",
                    help="Skip deterministic tool shaping (resource_action names, list promotion)")
    sp.set_defaults(func=cmd_connect)

    sp = sub.add_parser("compose", help="Propose composite (multi-step) tools and interactively approve them into the toolspec")
    sp.add_argument("--project", help="Project name (default: current dir name)")
    sp.add_argument("--n", type=int, default=6, help="Max composite candidates to propose (default 6)")
    sp.add_argument("--model", help="Model id for the proposal LLM (default: config/auto)")
    sp.add_argument("--dry-run", action="store_true", help="Preview candidates; never modify the toolspec")
    sp.set_defaults(func=cmd_compose)

    sp = sub.add_parser("migrate", help="Rewrite old (pre-shaping) tool-name references in evals/lessons (and given files) to current names")
    sp.add_argument("--project", help="Project name (default: current dir name)")
    sp.add_argument("--dry-run", action="store_true", help="Preview per-file change counts; write nothing")
    sp.add_argument("--files", metavar="a.json,b.json",
                    help="Extra JSON files to migrate (generic: rewrite string values matching old tool names)")
    sp.set_defaults(func=cmd_migrate)

    sp = sub.add_parser("eval", help="Task-based self-verification: run realistic tasks through the agent and gate on completion rate")
    sp.add_argument("--project", help="Project name (default: current dir name)")
    sp.add_argument("--n", type=int, default=8, help="Max tasks to generate (default 8)")
    sp.add_argument("--regen", action="store_true", help="Ignore <project>.evals.json and regenerate tasks")
    sp.add_argument("--live-write", action="store_true", help="Allow WRITE tasks (explicit consent; never on production)")
    sp.add_argument("--yes", action="store_true", help="Skip the --live-write interactive confirmation")
    sp.add_argument("--model", help="Model id for the agent under test (default: config/auto)")
    sp.add_argument("--judge-model", help="Model id for the LLM judge (default: same as --model)")
    sp.add_argument("--threshold", type=float, default=0.8, help="Completion-rate gate (default 0.8)")
    sp.add_argument("--budget", type=int, help="Eval LLM call budget (default 40)")
    sp.add_argument("--json", help="Write the full report JSON to this path")
    sp.add_argument("--history", action="store_true", help="Show the last recorded runs and exit")
    sp.add_argument("--fix", action="store_true",
                    help="Auto-apply repair for fixable failures (description rewrite, param synthesis)")
    sp.add_argument("--strict", action="store_true",
                    help="Gate on the Wilson CI lower bound ≥ threshold AND a minimum sample "
                         "(statistically sound; fails 'underpowered' on tiny task sets)")
    sp.add_argument("--judge-votes", type=int, default=1, metavar="N",
                    help="Sample the LLM judge N times per task and take the majority (default 1)")
    sp.add_argument("--compare", metavar="OLD_TOOLSPEC",
                    help="A/B: also run the same tasks on an older toolspec and print a verdict")
    sp.set_defaults(func=cmd_eval)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
