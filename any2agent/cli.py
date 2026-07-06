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

    eval_budget.reset(getattr(args, "budget", None) or 40)
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

    verify_ctx = {}
    if os.getenv("ANY2AGENT_VERIFY_COOKIE"):
        verify_ctx["cookie"] = os.getenv("ANY2AGENT_VERIFY_COOKIE")
    if os.getenv("ANY2AGENT_VERIFY_BEARER"):
        verify_ctx["in_headers"] = {"authorization": "Bearer " + os.getenv("ANY2AGENT_VERIFY_BEARER")}

    adapter = RestAdapter(cfg.base_url, cfg.auth)
    n_read = sum(1 for t in tasks if t.kind == "read")
    print("[eval] tasks=%d (read=%d write=%d)  target=%s" % (len(tasks), n_read, len(tasks) - n_read, cfg.base_url))
    rep = V.task_eval(ts, adapter, tasks, model_id=getattr(args, "model", None),
                      threshold=args.threshold, write_ok=write_ok, verify_ctx=verify_ctx,
                      judge_model=getattr(args, "judge_model", None))

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
    print("[eval] rate=%.2f (threshold %.2f)  rated=%d  wrong_tool=%d  errors=%d  "
          "skipped_write=%d  skipped_budget=%d  infra=%d  ungraded=%d"
          % (rep["rate"], rep["threshold"], rep["rated"], m["wrong_tool_calls"],
             m["tool_errors"], rep["skipped_write"], rep["skipped_budget"],
             rep["infra_errors"], rep["ungraded"]))
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

    if getattr(args, "json", None):
        with open(args.json, "w", encoding="utf-8") as f:
            _json.dump(rep, f, ensure_ascii=False, indent=2)
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

    sp = sub.add_parser("connect", help="Agentic onboarding: scan a source tree -> verify/repair loop -> agent")
    sp.add_argument("--path", help="Path to the target project source tree")
    sp.add_argument("--project", help="Project name (default: target dir name)")
    sp.add_argument("--base-url", help="Live API base URL (for verification/runtime)")
    sp.add_argument("--auth", choices=["none", "bearer", "api_key_header", "cookie"])
    sp.add_argument("--token-env"); sp.add_argument("--header"); sp.add_argument("--cookie-name")
    sp.add_argument("--default-model")
    sp.add_argument("--live", dest="live", action="store_true", default=None, help="Consent to live read probing")
    sp.add_argument("--no-live", dest="live", action="store_false", help="Disable live probing")
    sp.add_argument("--session-cookie", help="User's session cookie for live RBAC verification (not stored)")
    sp.add_argument("--session-bearer", help="User's bearer token for live RBAC verification (not stored)")
    sp.add_argument("--chat", help="Chat target: 1=standalone 2=embed 0=later")
    sp.add_argument("--no-input", action="store_true", help="Non-interactive (use flags/defaults only)")
    sp.add_argument("--eval", dest="eval_gate", action="store_true",
                    help="After the verify loop, run the task-based eval as a final gate")
    sp.set_defaults(func=cmd_connect)

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
    sp.set_defaults(func=cmd_eval)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
