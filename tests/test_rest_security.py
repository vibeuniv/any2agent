"""REST adapter security: passthrough creds must not leak across a redirect to
another host, and LLM-controlled path params must not escape the API's origin
(SSRF / scheme break-out)."""
import http.server
import threading

import pytest

from any2agent.adapters.rest import RestAdapter
from any2agent.spec import ToolSpec


def _serve(handler):
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture(scope="module")
def hosts():
    captured = {}

    class Other(http.server.BaseHTTPRequestHandler):  # the "attacker" host B
        def do_GET(self):
            captured.update({k.lower(): v for k, v in self.headers.items()})
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":1}')

        def log_message(self, *a): pass

    b = _serve(Other)
    bport = b.server_address[1]

    class Api(http.server.BaseHTTPRequestHandler):   # the configured API, host A
        def do_GET(self):
            if self.path.startswith("/redir"):
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:%d/" % bport)
                self.end_headers()
            else:
                self.send_response(200); self.end_headers(); self.wfile.write(b'{"here":"A"}')

        def log_message(self, *a): pass

    a = _serve(Api)
    yield "http://127.0.0.1:%d" % a.server_address[1], captured
    a.shutdown(); b.shutdown()


def _tool(path):
    return ToolSpec(name="t", description="", backing={"method": "GET", "path": path})


def test_creds_not_leaked_across_host_redirect(hosts):
    base, captured = hosts
    captured.clear()
    adapter = RestAdapter(base, {"type": "passthrough", "carrier": "cookie"})
    ctx = {"cookie": "sb-session=SECRET"}
    # follow a redirect from host A to host B; the cookie must be dropped en route
    adapter.call(_tool("/redir"), {}, ctx)
    assert "cookie" not in captured, "passthrough cookie leaked to another host on redirect"
    assert "authorization" not in captured


def test_path_param_cannot_escape_origin(hosts):
    base, _ = hosts
    adapter = RestAdapter(base, {"type": "none"})
    # an LLM-supplied id that tries to jump hosts / schemes is quoted, staying on-origin
    r = adapter.call(ToolSpec(name="t", description="",
                              backing={"method": "GET", "path": "/notes/{id}"}),
                     {"id": "1/../../@evil.com"}, {})
    assert r["ok"] and r["status"] == 200, "quoted param should stay on-origin, not escape"


def test_same_origin_guard():
    from any2agent.adapters.rest import _same_origin
    b = "https://api.example.com/v1"
    assert _same_origin("https://api.example.com/v1/notes", b)
    assert not _same_origin("https://evil.com/v1/notes", b)          # other host
    assert not _same_origin("https://api.example.com:8443/v1", b)    # other port
    assert not _same_origin("http://api.example.com/v1", b)          # downgraded scheme
    assert not _same_origin("file:///etc/passwd", b)                 # non-http scheme
