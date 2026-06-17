"""Auth analyzer — read the target project's authentication logic and decide HOW
to pass the logged-in user's session/token through to tool calls.

Three layers, increasingly general:
  1. heuristic scheme detection (supabase-ssr, next-auth, spring-security, ...)
  2. carrier EXTRACTION — find which cookies/headers the code actually reads from
     inbound requests (framework-agnostic; works even on unknown/custom stacks)
  3. LLM fallback — when 1+2 are still low/unknown, ask an LLM to read the auth
     files and produce the plan (needs a provider key)

Output (config.auth) for passthrough:
  type=passthrough, scheme, carrier(cookie|bearer), cookie_prefixes/cookie_names,
  header, role_source, confidence, evidence, source(heuristic|extracted|llm).

RBAC stays server-enforced; passthrough only carries the user's identity. The SDK
never implements role logic. Signed-request (HMAC/SigV4) / mTLS auth cannot be
passthrough'd by forwarding and is flagged for a custom adapter.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Set

_SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target", ".next", ".idea"}
_AUTH_HINT = ("auth", "login", "logout", "session", "security", "middleware", "supabase",
              "token", "jwt", "signin", "identity", "passport", "sanctum", "devise")
_EXTS = (".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".rb", ".go", ".cs", ".php", ".json")

# --- credential-read patterns (what the app consumes from inbound requests) ---
_COOKIE_READ = [
    re.compile(r"cookies\(\)\.get\(\s*['\"]([^'\"]+)['\"]"),            # next: cookies().get('x')
    re.compile(r"\.cookies\.get\(\s*['\"]([^'\"]+)['\"]"),             # req/request.cookies.get('x')
    re.compile(r"\.cookies\[\s*['\"]([^'\"]+)['\"]\s*\]"),             # req.cookies['x']
    re.compile(r"COOKIES\[\s*['\"]([^'\"]+)['\"]\s*\]"),               # django request.COOKIES['x']
    re.compile(r"@CookieValue\(\s*(?:value\s*=\s*)?['\"]([^'\"]+)['\"]"),  # spring
    re.compile(r"getCookie\(\s*['\"]([^'\"]+)['\"]"),                  # helpers
]
_HEADER_READ = [
    re.compile(r"headers\(\)\.get\(\s*['\"]([^'\"]+)['\"]"),           # next: headers().get('x')
    re.compile(r"\.headers\.get\(\s*['\"]([^'\"]+)['\"]"),
    re.compile(r"\.headers\[\s*['\"]([^'\"]+)['\"]\s*\]"),
    re.compile(r"@RequestHeader\(\s*(?:value\s*=\s*)?['\"]([^'\"]+)['\"]"),  # spring
    re.compile(r"getHeader\(\s*['\"]([^'\"]+)['\"]"),                  # java
    re.compile(r"META\[\s*['\"]HTTP_([A-Z_]+)['\"]\s*\]"),             # django META
]
_COOKIE_AUTHISH = ("session", "token", "auth", "sid", "jwt", "access", "refresh", "sb-", "csrf")
_HDR_AUTHISH = ("authorization", "token", "auth", "key", "access", "session", "jwt", "api")
# auth that fundamentally can't be passthrough'd by forwarding a token:
_UNPASSABLE = ("sigv4", "aws4-hmac", "x-amz-", "hmac", "createhmac", "x509", "mtls", "client-cert")


def _candidate_files(root: str, cap: int = 140) -> List[str]:
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in _SKIP]
        for fn in fns:
            if not fn.lower().endswith(_EXTS):
                continue
            rel = os.path.relpath(os.path.join(dp, fn), root).lower()
            if fn.lower() in ("middleware.ts", "middleware.js", "package.json") or any(h in rel for h in _AUTH_HINT):
                out.append(os.path.join(dp, fn))
            if len(out) >= cap:
                return out
    return out


def _read(paths: List[str], cap: int = 700000) -> Dict[str, str]:
    texts, total = {}, 0
    for p in paths:
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                t = f.read(20000)
        except Exception:
            continue
        texts[p] = t
        total += len(t)
        if total > cap:
            break
    return texts


def _evidence(texts, root, needles):
    return [os.path.relpath(p, root) for p, t in texts.items()
            if any(n in t.lower() for n in needles)][:5]


# ---------------- layer 1: heuristic scheme ----------------
def _heuristic(texts, root) -> Dict[str, Any]:
    low = "\n".join(texts.values()).lower()

    def plan(scheme, carrier, conf, **kw):
        d = {"type": "passthrough", "scheme": scheme, "carrier": carrier,
             "confidence": conf, "source": "heuristic"}
        d.update(kw)
        return d

    if "@supabase/ssr" in low or "createserverclient" in low or "supabase.auth" in low:
        return plan("supabase-ssr", "cookie", "high" if "@supabase/ssr" in low else "medium",
                    cookie_prefixes=["sb-"],
                    role_source="Supabase auth.getUser(); role in JWT app_metadata/user_metadata or profiles table",
                    evidence=_evidence(texts, root, ["supabase", "createserverclient", "getuser"]))
    if "next-auth" in low or "getserversession" in low or "authjs" in low:
        return plan("next-auth", "cookie", "high",
                    cookie_names=["next-auth.session-token", "__Secure-next-auth.session-token",
                                  "authjs.session-token", "__Secure-authjs.session-token"],
                    role_source="NextAuth session callback (session.user.role)",
                    evidence=_evidence(texts, root, ["next-auth", "getserversession", "authjs"]))
    if "express-session" in low or "req.session" in low:
        return plan("express-session", "cookie", "medium", cookie_names=["connect.sid"],
                    role_source="req.session.user role / middleware",
                    evidence=_evidence(texts, root, ["express-session", "req.session"]))
    if "springframework.security" in low or "securityfilterchain" in low:
        return plan("spring-security", "cookie", "high", cookie_names=["JSESSIONID"],
                    role_source="Spring Security GrantedAuthority / @PreAuthorize",
                    evidence=_evidence(texts, root, ["securityfilterchain", "preauthorize"]))
    if "sanctum" in low or "laravel" in low:
        return plan("laravel", "cookie", "medium", cookie_names=["laravel_session", "XSRF-TOKEN"],
                    role_source="Laravel gate/policy or Sanctum abilities",
                    evidence=_evidence(texts, root, ["sanctum", "laravel"]))
    if "rest_framework" in low or "simplejwt" in low or "django" in low:
        carrier = "bearer" if ("simplejwt" in low or "tokenauth" in low) else "cookie"
        return plan("django", carrier, "medium",
                    cookie_names=["sessionid"] if carrier == "cookie" else None,
                    header="Authorization" if carrier == "bearer" else None,
                    role_source="Django permissions / DRF permission_classes",
                    evidence=_evidence(texts, root, ["rest_framework", "simplejwt", "django"]))
    if ("authorization" in low and "bearer" in low) and any(
            k in low for k in ("jsonwebtoken", "jwt.verify", "jwtverify", "from 'jose'", "import jwt", "verifyjwt")):
        return plan("jwt-bearer", "bearer", "medium", header="Authorization",
                    role_source="JWT claim (role/roles/scope) verified server-side",
                    evidence=_evidence(texts, root, ["bearer", "jwt", "jose"]))
    return {"type": "passthrough", "scheme": "unknown", "carrier": "cookie",
            "confidence": "low", "source": "heuristic",
            "role_source": "unknown (server-enforced)", "evidence": []}


# ---------------- layer 2: carrier extraction ----------------
def _extract(texts) -> Dict[str, Any]:
    cookies: Set[str] = set()
    headers: Set[str] = set()
    for t in texts.values():
        for rx in _COOKIE_READ:
            for m in rx.finditer(t):
                cookies.add(m.group(1))
        for rx in _HEADER_READ:
            for m in rx.finditer(t):
                headers.add(m.group(1).lower().replace("_", "-"))
    auth_cookies = sorted(c for c in cookies if any(k in c.lower() for k in _COOKIE_AUTHISH))
    auth_headers = sorted(h for h in headers if any(k in h for k in _HDR_AUTHISH))
    return {"cookies": sorted(cookies), "headers": sorted(headers),
            "auth_cookies": auth_cookies, "auth_headers": auth_headers}


def _augment(plan: Dict[str, Any], ext: Dict[str, Any]) -> Dict[str, Any]:
    ac, ah = ext["auth_cookies"], ext["auth_headers"]
    has_bearer_hdr = any(h == "authorization" for h in ah)

    if plan["scheme"] != "unknown":
        # strengthen known plan with concrete names the code reads
        if plan.get("carrier") == "cookie" and ac:
            names = set(plan.get("cookie_names") or [])
            # keep prefix-matched too; add exact authish cookies
            pref = plan.get("cookie_prefixes") or []
            for c in ac:
                if not any(c.startswith(p) for p in pref):
                    names.add(c)
            if names:
                plan["cookie_names"] = sorted(names)
        plan.setdefault("extracted", {"cookies": ac, "headers": ah})
        return plan

    # unknown scheme -> decide carrier from what the code actually reads
    if has_bearer_hdr or (ah and not ac):
        hdr = "Authorization" if has_bearer_hdr else ah[0].title()
        return {**plan, "carrier": "bearer", "header": hdr, "confidence": "medium",
                "source": "extracted", "extracted": {"headers": ah, "cookies": ac}}
    if ac:
        return {**plan, "carrier": "cookie", "cookie_names": ac, "confidence": "medium",
                "source": "extracted", "extracted": {"cookies": ac, "headers": ah}}
    return plan  # still unknown


# ---------------- layer 3: LLM fallback ----------------
def _llm_auth(texts, root, model_id=None) -> Dict[str, Any] | None:
    from ..core import registry
    entry, model_string, _ = registry.resolve(model_id)
    if not entry:
        return None
    # pick the most auth-relevant files (smaller prompt)
    picked = sorted(texts.items(),
                    key=lambda kv: -sum(kv[1].lower().count(h) for h in _AUTH_HINT))[:6]
    snippet = "\n\n".join("// %s\n%s" % (os.path.relpath(p, root), t[:2500]) for p, t in picked)
    prompt = (
        "Read this project's authentication logic and decide HOW to pass the logged-in "
        "user's session/token through to backend calls. Output JSON ONLY with keys: "
        '{"scheme": str, "carrier": "cookie"|"bearer", "cookie_names": [str], '
        '"cookie_prefixes": [str], "header": str, "role_source": str}. '
        "If carrier=cookie, give the cookie name(s)/prefix(es) to forward; if bearer, the header "
        "name (usually Authorization). If unsure, give the most likely value. No prose, JSON only.\n\n"
        + snippet
    )
    try:
        resp = registry.completion(model_string, [{"role": "user", "content": prompt}],
                                   tools=None, stream=False, extra=registry.completion_kwargs(entry))
        txt = resp.choices[0].message.content or ""
        i, j = txt.find("{"), txt.rfind("}")
        if i < 0 or j < 0:
            return None
        d = json.loads(txt[i:j + 1])
        out = {"type": "passthrough", "scheme": d.get("scheme", "llm-detected"),
               "carrier": d.get("carrier", "cookie"), "confidence": "llm", "source": "llm",
               "role_source": d.get("role_source", "unknown (server-enforced)")}
        if d.get("cookie_names"):
            out["cookie_names"] = d["cookie_names"]
        if d.get("cookie_prefixes"):
            out["cookie_prefixes"] = d["cookie_prefixes"]
        if d.get("header"):
            out["header"] = d["header"]
        return out
    except Exception:
        return None


def analyze(root: str, use_llm: bool = True, model_id=None) -> Dict[str, Any]:
    files = _candidate_files(root)
    texts = _read(files)
    blob = "\n".join(texts.values()).lower()

    plan = _heuristic(texts, root)
    ext = _extract(texts)
    plan = _augment(plan, ext)

    # flag fundamentally non-passthrough auth (signed/mTLS)
    if any(k in blob for k in _UNPASSABLE):
        plan["warning"] = ("Signed-request (HMAC/SigV4) or mTLS detected — can't be done by token "
                           "passthrough; a custom adapter is required.")

    low_conf = plan.get("confidence") in ("low",) or plan.get("scheme") == "unknown"
    if low_conf and use_llm:
        llm = _llm_auth(texts, root, model_id)
        if llm:
            # keep extraction evidence if any
            if ext.get("auth_cookies") or ext.get("auth_headers"):
                llm["extracted"] = {"cookies": ext["auth_cookies"], "headers": ext["auth_headers"]}
            llm["evidence"] = _evidence(texts, root, list(_AUTH_HINT))
            if plan.get("warning"):
                llm["warning"] = plan["warning"]
            plan = llm

    plan.setdefault("evidence", _evidence(texts, root, list(_AUTH_HINT)))
    return plan
