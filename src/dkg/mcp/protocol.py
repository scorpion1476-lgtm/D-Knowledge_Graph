"""JSON-RPC 2.0 request/response and tool registry."""

from __future__ import annotations

import builtins
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..core.errors import DKGError

ERR_PARSE = -32700
ERR_INVALID = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603
ERR_POLICY = -32001
ERR_UNAVAILABLE = -32002


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], dict]
    kind: str = "read"  # 'read' | 'write' | 'admin'


#: Fallback ceiling for an integer argument whose schema declares no maximum.
#: A tool that wants a different ceiling declares one; this exists so that
#: forgetting to declare one cannot leave a read unbounded.
DEFAULT_INTEGER_MAXIMUM = 1000

#: Fallback ceiling for a string argument whose schema declares no maxLength.
#: Long enough for any real query, short enough that a pathological pattern
#: cannot be pushed through into the storage layer.
DEFAULT_STRING_MAX_LENGTH = 4096


def enforce_schema(tool: ToolSpec, arguments: dict) -> dict:
    """Make a tool's declared input schema binding, not decorative.

    The registry used to hand arguments straight to the handler. A schema
    declaring ``"limit": {"minimum": 1, "maximum": 100}`` therefore advertised a
    bound that nothing applied: an adversarial review asked ``dkg.search`` for a
    billion results and got as many as the corpus held, and pushed a
    200,000-character query far enough down to surface a raw storage error
    instead of a validation error. The MCP surface is the trust boundary against
    an agent acting on injected content, so an argument outside the contract is
    refused here, before any handler sees it.

    Bounds are applied even when the schema omits them, because the failure that
    matters is the one nobody remembered to declare. Refusing rather than
    silently clamping keeps the caller honest about what it asked for.
    """
    properties = (tool.input_schema or {}).get("properties") or {}
    for key, value in (arguments or {}).items():
        rules = properties.get(key)
        if not isinstance(rules, dict):
            continue
        declared = rules.get("type")
        if declared in ("integer", "number") and isinstance(value, bool) is False:
            try:
                number = int(value) if declared == "integer" else float(value)
            except (TypeError, ValueError) as e:
                raise DKGError(
                    f"{tool.name}: {key!r} must be a {declared}, got {value!r}",
                    code="dkg/mcp/invalid_argument",
                ) from e
            low = rules.get("minimum")
            high = rules.get("maximum")
            if declared == "integer" and high is None:
                high = DEFAULT_INTEGER_MAXIMUM
            if low is not None and number < low:
                raise DKGError(
                    f"{tool.name}: {key!r} must be at least {low}, got {number}",
                    code="dkg/mcp/invalid_argument",
                )
            if high is not None and number > high:
                raise DKGError(
                    f"{tool.name}: {key!r} must be at most {high}, got {number}",
                    code="dkg/mcp/invalid_argument",
                )
        elif declared == "string":
            text = str(value)
            cap = rules.get("maxLength", DEFAULT_STRING_MAX_LENGTH)
            if len(text) > cap:
                raise DKGError(
                    f"{tool.name}: {key!r} is {len(text)} characters, above the limit of {cap}",
                    code="dkg/mcp/invalid_argument",
                )
    return arguments


@dataclass
class ToolRegistry:
    tools: dict[str, ToolSpec] = field(default_factory=dict)

    def register(self, tool: ToolSpec) -> None:
        if tool.name in self.tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self.tools[tool.name] = tool

    def list(self) -> list[dict]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "kind": t.kind,
                "input_schema": t.input_schema,
            }
            for t in self.tools.values()
        ]

    def call(self, name: str, arguments: dict) -> dict:
        tool = self.tools.get(name)
        if tool is None:
            raise DKGError(f"tool not found: {name}", code="dkg/mcp/tool_not_found")
        return tool.handler(enforce_schema(tool, arguments or {}))

    # ``Sequence`` rather than ``list`` on the parameter, and ``builtins.list``
    # on the return, because inside this class body the bare name ``list``
    # resolves to the ``list()`` method above rather than to the builtin.
    def restrict(self, allowlist: Sequence[str]) -> builtins.list[str]:
        """Drop every tool not on ``allowlist``. Returns the names it did not know.

        Restriction removes the tool outright rather than refusing it at call
        time, so a restricted server does not even advertise what it will not
        run. An unknown name is reported rather than ignored, because silently
        accepting a typo would leave an operator believing a tool was served
        when it was not.
        """
        wanted = {n.strip() for n in allowlist if n and n.strip()}
        unknown = sorted(wanted - set(self.tools))
        self.tools = {name: spec for name, spec in self.tools.items() if name in wanted}
        return unknown


def make_response(id_: Any, result: Any = None, error: dict | None = None) -> dict:
    payload = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    return payload


def make_error(id_: Any, code: int, message: str, data: dict | None = None) -> dict:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return make_response(id_, error=err)


def parse_request(raw: str) -> dict:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"parse error: {e}") from e
    if not isinstance(obj, dict) or obj.get("jsonrpc") != "2.0" or "method" not in obj:
        raise ValueError("invalid JSON-RPC 2.0 request")
    return obj
