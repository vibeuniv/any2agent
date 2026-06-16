"""Source-tree scanner: detect the web framework and extract routes directly from
code, producing ToolSpec entries AND a ground-truth route list used by the
verifier's coverage critic. Best-effort across common frameworks; when an OpenAPI
contract is found in the tree it's preferred (richer parameter schemas).

Static extraction captures method + path + path-params reliably; query/body
params are often not statically resolvable — the verifier's liveness/agent checks
catch the gaps so the repair loop can enrich them.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Tuple

from ..spec import ToolSpec

_READ = {"GET", "HEAD", "OPTIONS"}
_DANGER = {"DELETE"}
_VERBS = "get|post|put|patch|delete|head|options"
_NAME_OK = re.compile(r"[^a-zA-Z0-9_]+")
_PATHVAR = re.compile(r"\{([^}/]+)\}|:([A-Za-z_][A-Za-z0-9_]*)")

# language/framework route patterns -> (regex, group_idx_method, group_idx_path, fixed_method)
_PY_DECORATOR = re.compile(r"@\w+\.(%s)\(\s*['\"]([^'\"]+)['\"]" % _VERBS, re.I)
_PY_FLASK_ROUTE = re.compile(r"@\w+\.route\(\s*['\"]([^'\"]+)['\"](.*?)\)", re.I | re.S)
_JS_CALL = re.compile(r"\b\w+\.(%s)\(\s*['\"]([^'\"]+)['\"]" % _VERBS, re.I)
_NEST_DEC = re.compile(r"@(Get|Post|Put|Patch|Delete|Head|Options)\(\s*['\"]?([^'\")]*)['\"]?\s*\)")
_SPRING_MAP = re.compile(r"@(Get|Post|Put|Patch|Delete)Mapping\(\s*(?:value\s*=\s*)?['\"]([^'\"]+)['\"]")
_SPRING_REQ = re.compile(r"@RequestMapping\(\s*(?:value\s*=\s*)?['\"]([^'\"]+)['\"]")
# Next.js App Router: route.ts handlers export named HTTP-method functions/consts.
_NEXT_EXPORT = re.compile(r"export\s+(?:async\s+)?(?:function|const)\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b")
_NEXT_FILE = re.compile(r"(^|/)route\.(ts|js|tsx|jsx|mts|mjs)$")

_EXT_LANG = {
    ".py": "python", ".js": "js", ".ts": "js", ".java": "java", ".kt": "java",
}
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target", ".idea"}


def _walk(root: str):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in _SKIP_DIRS]
        for fn in fns:
            ext = os.path.splitext(fn)[1].lower()
            if ext in _EXT_LANG:
                yield os.path.join(dp, fn), _EXT_LANG[ext]


def find_openapi(root: str) -> str | None:
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in _SKIP_DIRS]
        for fn in fns:
            low = fn.lower()
            if low in ("openapi.json", "openapi.yaml", "openapi.yml", "swagger.json", "swagger.yaml"):
                return os.path.join(dp, fn)
    return None


def detect_framework(root: str) -> str:
    hay = ""
    for path, lang in _walk(root):
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                hay += f.read(4000)
        except Exception:
            continue
        if len(hay) > 400000:
            break
    # Next.js: package.json depends on "next", or App Router route handlers exist.
    pj = os.path.join(root, "package.json")
    if os.path.exists(pj):
        try:
            with open(pj, encoding="utf-8", errors="ignore") as f:
                if '"next"' in f.read():
                    return "nextjs"
        except Exception:
            pass
    h = hay.lower()
    if "fastapi" in h:
        return "fastapi"
    if "from flask" in h or "import flask" in h:
        return "flask"
    if "@nestjs" in h or "@controller" in h.lower():
        return "nestjs"
    if "express(" in h or "require('express')" in h or 'require("express")' in h:
        return "express"
    if "springframework" in h or "@restcontroller" in h:
        return "spring"
    return "unknown"


def _name(method: str, path: str, seen: set) -> str:
    base = _NAME_OK.sub("_", (method + "_" + path).lower()).strip("_")[:60] or "op"
    name = base
    n = 2
    while name in seen:
        name = "%s_%d" % (base, n); n += 1
    seen.add(name)
    return name


def _path_params(path: str) -> Dict[str, Any]:
    props = {}
    for m in _PATHVAR.finditer(path):
        var = m.group(1) or m.group(2)
        if var:
            props[var] = {"type": "string", "description": "Path parameter."}
    return props


def _mk(method: str, path: str, seen: set, domain: str, desc: str = "") -> ToolSpec:
    method = method.upper()
    props = _path_params(path)
    params = {"type": "object", "properties": props}
    if props:
        params["required"] = sorted(props.keys())
    return ToolSpec(
        name=_name(method, path, seen),
        description=(desc or ("%s %s" % (method, path)))[:400],
        parameters=params,
        backing={"method": method, "path": path},
        write=(method not in _READ),
        danger=(method in _DANGER),
        domain=domain or (path.strip("/").split("/")[0] if path.strip("/") else ""),
    )


def scan(root: str) -> Tuple[List[ToolSpec], Dict[str, Any]]:
    """Return (tools, meta). meta.routes is the ground-truth (method,path) list."""
    fw = detect_framework(root)
    tools: List[ToolSpec] = []
    seen: set = set()
    routes: List[Dict[str, str]] = []

    def add(method: str, path: str, domain: str = ""):
        method = method.upper()
        key = (method, path)
        if key in {(r["method"], r["path"]) for r in routes}:
            return
        routes.append({"method": method, "path": path})
        tools.append(_mk(method, path, seen, domain))

    # Framework-gated extraction: run only the passes that match the detected
    # framework, so e.g. Next.js never runs the Express ".get()" pass (which would
    # mis-read header/param accessors like headers.get('authorization') as routes).
    use_py = fw in {"fastapi", "flask", "unknown"}
    use_express = fw in {"express", "unknown"}
    use_nest = fw in {"nestjs", "unknown"}
    use_spring = fw in {"spring", "unknown"}
    use_next = fw in {"nextjs", "unknown"}

    # Next.js: only the route.* file-path pass (no decorator/call scanning).
    if fw == "nextjs":
        _scan_next(root, add)
    else:
        for path_, lang in _walk(root):
            try:
                with open(path_, encoding="utf-8", errors="ignore") as f:
                    src = f.read()
            except Exception:
                continue
            dom = os.path.splitext(os.path.basename(path_))[0]

            if lang == "python" and use_py:
                for m in _PY_DECORATOR.finditer(src):
                    add(m.group(1), m.group(2), dom)
                for m in _PY_FLASK_ROUTE.finditer(src):
                    rpath, tail = m.group(1), m.group(2) or ""
                    methods = re.findall(r"['\"](GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)['\"]", tail, re.I)
                    for mm in (methods or ["GET"]):
                        add(mm, rpath, dom)
            elif lang == "js":
                if use_express:
                    for m in _JS_CALL.finditer(src):
                        rpath = m.group(2)
                        if rpath.startswith("/"):  # real route paths start with '/'; cuts .get('header') noise
                            add(m.group(1), rpath, dom)
                if use_nest:
                    base = ""
                    cm = re.search(r"@Controller\(\s*['\"]?([^'\")]*)['\"]?\s*\)", src)
                    if cm:
                        base = "/" + cm.group(1).strip("/")
                    for m in _NEST_DEC.finditer(src):
                        sub = m.group(2).strip("/")
                        full = (base.rstrip("/") + "/" + sub).rstrip("/") if (base or sub) else "/"
                        add(m.group(1), full or "/", dom)
            elif lang == "java" and use_spring:
                cls = _SPRING_REQ.search(src)
                base = "/" + cls.group(1).strip("/") if cls else ""
                for m in _SPRING_MAP.finditer(src):
                    sub = m.group(2).strip("/")
                    full = (base.rstrip("/") + "/" + sub).rstrip("/") if (base or sub) else "/"
                    add(m.group(1), full or "/", dom)

        if use_next:  # unknown frameworks: still try route.* files
            _scan_next(root, add)

    meta = {"source": root, "framework": fw, "routes": routes, "route_count": len(routes),
            "extraction": "static-best-effort"}
    return tools, meta


def _next_url(rel_parts: List[str]) -> str:
    """Folder segments under app/ (excluding the route.* file) -> URL path.
    Drops route groups (..), maps [seg]/[...seg] -> {seg}."""
    segs = []
    for s in rel_parts:
        if s.startswith("(") and s.endswith(")"):
            continue  # route group, not part of the URL
        if s.startswith("[") and s.endswith("]"):
            inner = s[1:-1].lstrip(".")  # [...slug] / [[...slug]] -> slug
            segs.append("{" + inner + "}")
        else:
            segs.append(s)
    return "/" + "/".join(segs)


def _scan_next(root: str, add):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in _SKIP_DIRS]
        for fn in fns:
            if not _NEXT_FILE.search("/" + fn):
                continue
            full = os.path.join(dp, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/").split("/")
            # locate the 'app' router dir (supports app/ and src/app/)
            if "app" not in rel:
                continue
            i = len(rel) - 1 - rel[::-1].index("app")
            folder = rel[i + 1:-1]  # between app/ and route.*
            path = _next_url(folder) or "/"
            try:
                with open(full, encoding="utf-8", errors="ignore") as f:
                    src = f.read()
            except Exception:
                continue
            methods = sorted(set(m.group(1) for m in _NEXT_EXPORT.finditer(src)))
            dom = folder[0] if folder and not (folder[0].startswith("(")) else (folder[1] if len(folder) > 1 else "")
            for mm in (methods or []):
                add(mm, path, dom)
