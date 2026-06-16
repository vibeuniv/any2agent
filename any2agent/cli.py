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
        print("       ⚠ base_url 미설정 — %s 의 base_url 을 대상 API 주소로 채우세요." % cfg_path)
    print("       다음:  any2agent serve --project %s" % ts.project)


def cmd_connect(args):
    from .connect import connect
    connect(args)


def cmd_serve(args):
    project = _derive_project(args.project)
    cfg_path = project + ".any2agent.toml"
    if not os.path.exists(cfg_path):
        print("[serve] %s 없음. 먼저 `any2agent init` 을 실행하세요." % cfg_path, file=sys.stderr)
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
    sp.set_defaults(func=cmd_connect)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
