"""any2agent — turn any API-backed system into a conversational agent.

Point the SDK at a system's API contract (e.g. OpenAPI), run a script, get a
working agent. All generated artifacts are named after the target project.
"""

# Single source of truth is pyproject's version; read it back at import so the two
# can never drift (the old hardcoded string went stale at 0.1.0 while pyproject was 0.2.0).
from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("any2agent")
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "0.0.0+dev"
