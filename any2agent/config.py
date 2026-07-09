"""Agent configuration. Every generated artifact is named after the project, so
the project name is the single source of templating. Config persists as
<project>.any2agent.toml next to the toolspec.

auth describes how the REST adapter authenticates to the target system:
  - {"type": "none"}
  - {"type": "bearer", "token_env": "MYAPP_TOKEN"}
  - {"type": "api_key_header", "header": "X-API-Key", "token_env": "MYAPP_KEY"}
  - {"type": "cookie", "name": "SESSION", "token_env": "MYAPP_SESSION"}
Credentials themselves live in env vars (token_env), never in the config file.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict

try:  # py3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # py3.9/3.10
    import tomli as _toml  # type: ignore


def llm_source_allowed() -> bool:
    """Whether build-time tooling may send raw SOURCE excerpts to the LLM
    (param synthesis hints, layer-3 auth analysis). Opt out with
    ANY2AGENT_NO_LLM_SOURCE=1 to keep source code off the provider — tool
    names/descriptions/schemas are still sent, only source snippets are held back."""
    return os.getenv("ANY2AGENT_NO_LLM_SOURCE", "").strip() not in ("1", "true", "yes")


def verify_ctx_from_env() -> Dict[str, Any]:
    """Verification session (the user's own) from env — used by connect's live
    probes and `eval` runs alike. Read-only here; never persisted."""
    ctx: Dict[str, Any] = {}
    if os.getenv("ANY2AGENT_VERIFY_COOKIE"):
        ctx["cookie"] = os.getenv("ANY2AGENT_VERIFY_COOKIE")
    if os.getenv("ANY2AGENT_VERIFY_BEARER"):
        ctx["in_headers"] = {"authorization": "Bearer " + os.getenv("ANY2AGENT_VERIFY_BEARER")}
    return ctx


def slugify(name: str) -> str:
    out = "".join(c if (c.isalnum() or c in "-_") else "-" for c in name.strip().lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-_") or "project"


@dataclass
class AgentConfig:
    project: str
    base_url: str = ""
    auth: Dict[str, Any] = field(default_factory=lambda: {"type": "none"})
    default_model_id: str = ""        # picker default: gpt|kimi|claude|gemini (empty = first available)
    host: str = "127.0.0.1"
    port: int = 8800
    # memory: the agent can remember small facts per user across sessions.
    memory_enabled: bool = True
    # header carrying a STABLE per-user id (set by the embedding app) used to scope
    # memory. Empty = single shared "anon" bucket (ok for local/single-user only).
    memory_owner_header: str = ""

    # ---- project-name templated artifact paths ----
    def toolspec_path(self) -> str:
        return f"{self.project}.toolspec.json"

    def config_path(self) -> str:
        return f"{self.project}.any2agent.toml"

    def evals_path(self) -> str:
        return f"{self.project}.evals.json"

    def lessons_path(self) -> str:
        return f"{self.project}.eval-lessons.json"

    def state_dir(self) -> str:
        return os.path.join(".any2agent-state", self.project)

    # ---- persistence ----
    def save(self, path: str | None = None) -> str:
        path = path or self.config_path()
        with open(path, "w", encoding="utf-8") as f:
            f.write(self._to_toml())
        return path

    @staticmethod
    def load(path: str) -> "AgentConfig":
        with open(path, "rb") as f:
            d = _toml.load(f)
        auth = d.get("auth") or {"type": "none"}
        return AgentConfig(
            project=d.get("project", "project"),
            base_url=d.get("base_url", ""),
            auth=auth,
            default_model_id=d.get("default_model_id", ""),
            host=d.get("host", "127.0.0.1"),
            port=int(d.get("port", 8800)),
            memory_enabled=bool(d.get("memory_enabled", True)),
            memory_owner_header=d.get("memory_owner_header", ""),
        )

    def _to_toml(self) -> str:
        def s(v: str) -> str:
            return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
        lines = [
            "# any2agent project config — generated. Credentials live in env (auth.token_env), not here.",
            f"project = {s(self.project)}",
            f"base_url = {s(self.base_url)}",
            f"default_model_id = {s(self.default_model_id)}",
            f"host = {s(self.host)}",
            f"port = {self.port}",
            f"memory_enabled = {'true' if self.memory_enabled else 'false'}",
            f"memory_owner_header = {s(self.memory_owner_header)}",
            "",
            "[auth]",
        ]
        for k, v in (self.auth or {"type": "none"}).items():
            if isinstance(v, list):
                lines.append(f"{k} = [" + ", ".join(s(str(x)) for x in v) + "]")
            elif isinstance(v, bool):
                lines.append(f"{k} = {'true' if v else 'false'}")
            else:
                lines.append(f"{k} = {s(str(v))}")
        return "\n".join(lines) + "\n"
