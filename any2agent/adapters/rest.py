"""Generic REST adapter (stdlib urllib — one plain synchronous JSON request is
all a tool call needs; ponytail: stdlib does it). Builds the request from
base_url + backing.path (filling {path} params), routes remaining args to query
(GET/DELETE) or JSON body (POST/PUT/PATCH), and applies pluggable auth from
config. Credentials come from env vars named in the auth block — never from the
spec or config file.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlencode, urlsplit

from ..spec import ToolSpec
from .base import Adapter

_PATH_VAR = re.compile(r"\{([^}]+)\}")
_BODY_METHODS = {"POST", "PUT", "PATCH"}
_SENSITIVE = ("cookie", "authorization")


class _SafeRedirect(urlrequest.HTTPRedirectHandler):
    """urlopen re-sends request headers verbatim on redirect — including the
    passthrough Cookie/Authorization — even to a DIFFERENT host, which leaks
    the user's credentials. Strip those headers whenever the redirect target
    changes origin (scheme/host/port). httpx does this for us; stdlib doesn't."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = self.parent  # noqa: unused; keep signature
        nreq = super().redirect_request(req, fp, code, msg, headers, newurl)
        if nreq is not None and _origin(newurl) != _origin(req.full_url):
            for h in _SENSITIVE:
                nreq.remove_header(h.capitalize())
        return nreq


def _origin(url: str):
    p = urlsplit(url)
    return (p.scheme, p.hostname, p.port)


# one opener with the safe redirect handler, reused for every call
_OPENER = urlrequest.build_opener(_SafeRedirect())


class RestAdapter(Adapter):
    def __init__(self, base_url: str, auth: Dict[str, Any] | None = None, timeout: float = 20.0):
        self.base_url = (base_url or "").rstrip("/")
        self.auth = auth or {"type": "none"}
        self.timeout = timeout

    def _headers(self, ctx: Dict[str, Any] | None = None) -> Dict[str, str]:
        h = {"Accept": "application/json"}
        a = self.auth or {}
        t = a.get("type", "none")
        ctx = ctx or {}

        if t == "passthrough":
            # Forward the logged-in USER's own credential (no standing creds) so the
            # target's RBAC applies to the user's role. The server populates ctx with
            # the caller's inbound headers: ctx["in_headers"] (lower-cased) + ctx["cookie"].
            in_h = {k.lower(): v for k, v in (ctx.get("in_headers") or {}).items()}
            if a.get("carrier") == "bearer":
                name = a.get("header", "Authorization")
                raw = in_h.get(name.lower()) or ctx.get("bearer")
                if raw:
                    # forward as-is (preserves 'Bearer ' or custom token scheme)
                    h[name] = raw if (" " in raw or name.lower() != "authorization") else ("Bearer " + raw)
            else:  # cookie
                cookie = _filter_cookie(ctx.get("cookie") or in_h.get("cookie", ""),
                                        a.get("cookie_prefixes") or [], a.get("cookie_names") or [])
                if cookie:
                    h["Cookie"] = cookie
            return h

        token = os.getenv(a.get("token_env", "")) if a.get("token_env") else None
        if t == "bearer" and token:
            h["Authorization"] = "Bearer " + token
        elif t == "api_key_header" and token:
            h[a.get("header", "X-API-Key")] = token
        elif t == "cookie" and token:
            h["Cookie"] = a.get("name", "SESSION") + "=" + token
        return h

    def call(self, spec: ToolSpec, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        method = (spec.backing.get("method") or "GET").upper()
        path = spec.backing.get("path") or "/"
        merged = {**(spec.defaults or {}), **_clean(args)}

        # fill path params — quote them so an LLM-supplied value can't inject a
        # new path segment, host, or scheme (e.g. id="../admin" or "@evil.com")
        used = set()
        def _sub(m):
            k = m.group(1)
            used.add(k)
            v = merged.get(k)
            return _quote_seg(v) if k in merged else m.group(0)
        url = self.base_url + _PATH_VAR.sub(_sub, path)

        # the final URL must stay on the configured API's origin and be http(s):
        # tool args are LLM-controlled, so this is the SSRF / scheme guard.
        if not _same_origin(url, self.base_url):
            return {"ok": False, "error": "blocked_off_origin_url"}

        rest = {k: v for k, v in merged.items() if k not in used and v is not None}
        body = None
        if method in _BODY_METHODS:
            body = rest
        elif rest:
            url = url + ("&" if "?" in url else "?") + urlencode({k: _scalar(v) for k, v in rest.items()})

        headers = self._headers(ctx)
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            req = urlrequest.Request(url, data=payload, headers=headers, method=method)
            try:
                resp = _OPENER.open(req, timeout=self.timeout)  # noqa: S310 (origin-checked above)
                status, ct = resp.status, resp.headers.get("content-type", "")
                text = resp.read().decode("utf-8", "replace")
            except urlerror.HTTPError as e:  # non-2xx still carries a body
                status, ct = e.code, e.headers.get("content-type", "") if e.headers else ""
                text = e.read().decode("utf-8", "replace")
            data: Any
            if "application/json" in ct:
                try:
                    data = json.loads(text)
                except Exception:
                    data = text[:4000]
            else:
                data = text[:4000]
            ok = 200 <= status < 300
            out = {"ok": ok, "status": status, "data": data}
            if not ok:
                out["error"] = "http_%d" % status
            return out
        except Exception as e:  # connection refused / timeout / dns
            return {"ok": False, "error": str(e)}


def _filter_cookie(raw: str, prefixes, names) -> str:
    """From an inbound Cookie header, keep only the auth cookies to forward.
    Empty prefixes+names = forward everything (best-effort when scheme unknown)."""
    if not raw:
        return ""
    if not prefixes and not names:
        return raw
    keep = []
    for part in raw.split(";"):
        kv = part.strip()
        nm = kv.split("=", 1)[0].strip()
        if nm in (names or []) or any(nm.startswith(p) for p in (prefixes or [])):
            keep.append(kv)
    return "; ".join(keep)


def _quote_seg(v) -> str:
    """Percent-quote a path-param value so it can't escape its segment or the
    URL (slashes, scheme, host). `safe=''` also encodes '/'."""
    from urllib.parse import quote
    return quote(str(v), safe="")


def _same_origin(url: str, base: str) -> bool:
    """The built URL must be http(s) and share the base URL's origin — the SSRF
    guard for LLM-controlled path/query values (blocks file://, other hosts)."""
    u, b = urlsplit(url), urlsplit(base)
    return u.scheme in ("http", "https") and (u.scheme, u.hostname, u.port) == (b.scheme, b.hostname, b.port)


def _clean(d):
    return d if isinstance(d, dict) else {}


def _scalar(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v
