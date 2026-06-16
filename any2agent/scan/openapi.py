"""OpenAPI / Swagger scanner: contract -> list[ToolSpec].

Works offline on a local file or fetches a URL. Each operation becomes a tool:
  name        operationId (sanitized) or <method>_<path-as-words>
  description summary/description from the spec (LLM enrichment is separate)
  parameters  JSON-Schema from `parameters` (path/query/header) + requestBody
  backing     {method, path}
  write/danger heuristic by HTTP verb (GET/HEAD=read, POST/PUT/PATCH=write, DELETE=danger)
  domain      first path segment or first tag (for discovery grouping)

Supports OpenAPI 3.x (requestBody/components.schemas $ref) and Swagger 2.0
(parameters with in:body, definitions $ref) at a practical level.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple
from urllib.request import urlopen

from ..spec import ToolSpec

_READ = {"get", "head", "options"}
_DANGER = {"delete"}
_NAME_OK = re.compile(r"[^a-zA-Z0-9_]+")


def load_contract(src: str) -> Dict[str, Any]:
    if src.startswith("http://") or src.startswith("https://"):
        with urlopen(src, timeout=20) as r:  # noqa: S310 (user-provided contract URL)
            raw = r.read().decode("utf-8", "replace")
    else:
        with open(src, encoding="utf-8") as f:
            raw = f.read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError("Contract is not JSON; install pyyaml to parse YAML contracts.") from e
        return yaml.safe_load(raw)


def _sanitize(name: str) -> str:
    n = _NAME_OK.sub("_", name).strip("_")
    return (n or "op")[:64]


def _resolve_ref(doc: Dict[str, Any], ref: str) -> Dict[str, Any]:
    if not ref.startswith("#/"):
        return {}
    node: Any = doc
    for part in ref[2:].split("/"):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return {}
    return node if isinstance(node, dict) else {}


def _schema(doc: Dict[str, Any], sch: Any, depth: int = 0) -> Dict[str, Any]:
    """Best-effort flatten a (possibly $ref'd) schema into a plain JSON-Schema."""
    if not isinstance(sch, dict) or depth > 6:
        return {"type": "string"}
    if "$ref" in sch:
        return _schema(doc, _resolve_ref(doc, sch["$ref"]), depth + 1)
    out: Dict[str, Any] = {}
    for k in ("type", "enum", "format", "description", "items"):
        if k in sch:
            out[k] = sch[k]
    if out.get("type") == "array" and isinstance(out.get("items"), dict):
        out["items"] = _schema(doc, out["items"], depth + 1)
    if sch.get("type") == "object" or "properties" in sch:
        props = {}
        for pn, ps in (sch.get("properties") or {}).items():
            props[pn] = _schema(doc, ps, depth + 1)
        out = {"type": "object", "properties": props}
        if sch.get("required"):
            out["required"] = sch["required"]
    return out or {"type": "string"}


def scan(src: str) -> Tuple[List[ToolSpec], Dict[str, Any]]:
    doc = load_contract(src)
    paths = doc.get("paths") or {}
    is_v3 = str(doc.get("openapi", "")).startswith("3")
    tools: List[ToolSpec] = []
    seen = set()

    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            m = method.lower()
            if m not in {"get", "post", "put", "patch", "delete", "head", "options"} or not isinstance(op, dict):
                continue

            op_id = op.get("operationId") or (m + "_" + re.sub(r"[/{}]+", "_", path).strip("_"))
            name = _sanitize(op_id)
            base = name
            n = 2
            while name in seen:
                name = "%s_%d" % (base, n); n += 1
            seen.add(name)

            props: Dict[str, Any] = {}
            required: List[str] = []
            for p in (op.get("parameters") or []):
                if "$ref" in p:
                    p = _resolve_ref(doc, p["$ref"])
                if not isinstance(p, dict) or p.get("in") == "header":
                    continue
                pn = p.get("name")
                if not pn:
                    continue
                if p.get("in") == "body":  # swagger 2 body param
                    bs = _schema(doc, p.get("schema") or {})
                    for k, v in (bs.get("properties") or {}).items():
                        props[k] = v
                    required += bs.get("required") or []
                    continue
                sch = _schema(doc, p.get("schema") or {"type": p.get("type", "string")})
                if p.get("description"):
                    sch.setdefault("description", p["description"])
                props[pn] = sch
                if p.get("required"):
                    required.append(pn)

            if is_v3 and isinstance(op.get("requestBody"), dict):
                content = (op["requestBody"].get("content") or {})
                jc = content.get("application/json") or next(iter(content.values()), {})
                bs = _schema(doc, (jc or {}).get("schema") or {})
                for k, v in (bs.get("properties") or {}).items():
                    props[k] = v
                required += bs.get("required") or []

            parameters = {"type": "object", "properties": props}
            if required:
                parameters["required"] = sorted(set(required))

            desc = op.get("summary") or op.get("description") or ("%s %s" % (m.upper(), path))
            tags = op.get("tags") or []
            domain = (tags[0] if tags else (path.strip("/").split("/")[0] if path.strip("/") else ""))

            tools.append(ToolSpec(
                name=name,
                description=desc.strip()[:600],
                parameters=parameters,
                backing={"method": m.upper(), "path": path},
                write=(m not in _READ),
                danger=(m in _DANGER),
                domain=domain,
            ))

    info = doc.get("info") or {}
    meta = {
        "source": src,
        "title": info.get("title", ""),
        "version": info.get("version", ""),
        "openapi": doc.get("openapi") or doc.get("swagger") or "",
    }
    return tools, meta
