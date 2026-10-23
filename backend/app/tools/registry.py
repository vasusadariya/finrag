"""Tool registry: schemas for the model, argument validation, safe dispatch, output caps."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.llm.types import ToolCall, ToolResult, ToolSpec

ToolFn = Callable[[dict[str, Any]], dict[str, Any]]
MAX_RESULT_CHARS = 12_000

_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def validate_args(schema: dict[str, Any], args: Any) -> list[str]:
    """Check ``args`` against the small JSON-schema subset our tools use; return problems."""
    if not isinstance(args, dict):
        return ["arguments must be an object"]
    props: dict[str, Any] = schema.get("properties", {})
    problems = [
        f"missing required argument '{r}'" for r in schema.get("required", []) if r not in args
    ]
    for name, value in args.items():
        spec = props.get(name)
        if spec is None:
            problems.append(f"unknown argument '{name}'; allowed: {sorted(props)}")
            continue
        want = spec.get("type")
        if want is not None:
            ok = isinstance(value, _TYPES[want]) and not (
                isinstance(value, bool) and want != "boolean"
            )
            if not ok:
                problems.append(f"argument '{name}' must be of type {want}")
                continue
        if "enum" in spec and value not in spec["enum"]:
            problems.append(f"argument '{name}' must be one of {spec['enum']}")
        if want == "array" and "items" in spec:
            item_type = _TYPES[spec["items"]["type"]]
            if not all(isinstance(v, item_type) and not isinstance(v, bool) for v in value):
                problems.append(f"every item of '{name}' must be of type {spec['items']['type']}")
    return problems


@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    fn: ToolFn


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, spec: ToolSpec, fn: ToolFn) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool {spec.name!r} already registered")
        self._tools[spec.name] = Tool(spec, fn)

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[ToolSpec]:
        return [t.spec for t in self._tools.values()]

    def call(self, call: ToolCall) -> ToolResult:
        """Run one tool call. Unknown tools, bad arguments and crashes become error results."""
        tool = self._tools.get(call.name)
        if tool is None:
            return self._err(call, f"unknown tool {call.name!r}; available: {self.names}")
        problems = validate_args(tool.spec.parameters, call.args)
        if problems:
            return self._err(call, "invalid arguments: " + "; ".join(problems))
        try:
            content = tool.fn(call.args)
        except Exception as e:  # noqa: BLE001 - a crashing tool must not kill the agent loop
            return self._err(call, f"tool failed: {type(e).__name__}")
        return ToolResult(call.id, call.name, _cap(content))

    @staticmethod
    def _err(call: ToolCall, message: str) -> ToolResult:
        return ToolResult(call.id, call.name, {"ok": False, "error": message})


def _cap(content: dict[str, Any]) -> dict[str, Any]:
    """Keep a tool result small enough for the context window."""
    try:
        text = json.dumps(content, default=str)
    except (TypeError, ValueError):
        return {"ok": False, "error": "tool returned a result that is not JSON serialisable"}
    if len(text) <= MAX_RESULT_CHARS:
        return content
    return {
        "ok": False,
        "error": f"result too large ({len(text)} chars); narrow the request (fewer rows/passages)",
    }
