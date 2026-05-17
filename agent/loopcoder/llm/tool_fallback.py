"""Recover tool calls from assistant *content* when the server returned none.

Small / quantized models (and some vLLM tool-parser mismatches) emit the
tool call as free text instead of structured ``tool_calls`` — typically a
JSON object wrapped in a markdown fence or an XML-ish tag, e.g.::

    ```xml
    <response>
        {"name": "write_file", "arguments": {"path": "a.py", "content": "x"}}
    </response>
    ```

    ```json
    {"name": "write_file", "arguments": {...}}
    ```

    <tool_call>{"name": "write_file", "arguments": {...}}</tool_call>

This module extracts those into the same shape the structured path uses.
It is intentionally conservative: it only recovers an object that has a
``name`` plus ``arguments``/``parameters``, and only when the name matches
a tool that was actually offered. Anything ambiguous is left alone so the
loop's normal "no action" handling still applies.
"""

from __future__ import annotations

import json
import re
from typing import Any

# JSON object candidates: fenced blocks first, then xml-ish tags, then any
# brace-balanced object. Ordered most-specific to least.
_FENCE_RE = re.compile(r"```(?:json|xml|tool_call|python)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(
    r"<(?:tool_call|response|function_call)>\s*(.*?)\s*</(?:tool_call|response|function_call)>",
    re.DOTALL | re.IGNORECASE,
)


def _iter_json_objects(text: str):
    """Yield candidate JSON object substrings from text, most-specific first."""
    for m in _TAG_RE.finditer(text):
        yield m.group(1).strip()
    for m in _FENCE_RE.finditer(text):
        yield m.group(1).strip()
    # Last resort: every brace-balanced top-level {...} span.
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                yield text[start : i + 1]


def _normalize(obj: Any) -> tuple[str, Any] | None:
    """Return (name, arguments) if obj looks like a tool call, else None."""
    if not isinstance(obj, dict):
        return None
    name = obj.get("name") or obj.get("tool") or obj.get("function")
    if isinstance(name, dict):  # {"function": {"name": ...}}
        name = name.get("name")
    if not isinstance(name, str) or not name:
        return None
    args = obj.get("arguments")
    if args is None:
        args = obj.get("parameters")
    if args is None:
        args = obj.get("args", {})
    return name, args


def extract_tool_calls(content: str | None, allowed_names: set[str]) -> list[dict[str, str]]:
    """Best-effort recovery of tool calls from assistant content.

    Returns a list of ``{"name": str, "arguments": <json str>}`` for each
    recovered call whose name is in ``allowed_names``. Empty list if nothing
    safely recoverable.
    """
    if not content or not allowed_names:
        return []

    recovered: list[dict[str, str]] = []
    seen: set[str] = set()
    for chunk in _iter_json_objects(content):
        try:
            obj = json.loads(chunk)
        except (json.JSONDecodeError, ValueError):
            continue
        norm = _normalize(obj)
        if norm is None:
            continue
        name, args = norm
        if name not in allowed_names:
            continue
        args_str = args if isinstance(args, str) else json.dumps(args)
        key = name + "\x00" + args_str
        if key in seen:
            continue
        seen.add(key)
        recovered.append({"name": name, "arguments": args_str})
    return recovered
