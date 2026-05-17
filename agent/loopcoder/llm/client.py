"""Thin wrapper around the OpenAI SDK targeted at vLLM's OpenAI-compatible API.

Adds:
- Retry/backoff on transient errors via tenacity.
- Tool-call parallelism passthrough.
- A typed LlmResponse covering both content-only and tool-calling responses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from loopcoder.logsetup import get_logger

log = get_logger("loopcoder.llm")

try:
    import openai  # type: ignore[import-not-found]
    from openai import OpenAI  # type: ignore[import-not-found]
    _OPENAI_AVAILABLE = True
except ImportError:  # pragma: no cover - tested via mocks
    _OPENAI_AVAILABLE = False
    openai = None  # type: ignore[assignment]
    OpenAI = None  # type: ignore[assignment, misc]


@dataclass
class LlmToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string from the model

    def parse(self) -> dict[str, Any]:
        import json
        return json.loads(self.arguments) if self.arguments else {}


@dataclass
class LlmResponse:
    content: str | None
    tool_calls: list[LlmToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: Any = None


class LlmClient:
    """OpenAI-compatible chat client."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "EMPTY",
        model: str = "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8",
        timeout_sec: int = 600,
        max_attempts: int = 5,
        backoff_initial_sec: float = 2.0,
        backoff_max_sec: float = 60.0,
    ) -> None:
        if not _OPENAI_AVAILABLE:
            raise RuntimeError(
                "openai package is required for LlmClient (install via pip)."
            )
        self.model = model
        self.timeout_sec = timeout_sec
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout_sec)
        self._chat = self._build_chat(max_attempts, backoff_initial_sec, backoff_max_sec)

    def _build_chat(self, max_attempts: int, initial: float, maximum: float):
        retry_decorator = retry(
            reraise=True,
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=initial, max=maximum),
            retry=retry_if_exception_type(self._retryable_exceptions()),
        )

        @retry_decorator
        def _do_call(**kwargs: Any):
            return self._client.chat.completions.create(**kwargs)

        return _do_call

    @staticmethod
    def _retryable_exceptions() -> tuple[type[BaseException], ...]:
        if not _OPENAI_AVAILABLE:
            return (ConnectionError,)
        return (
            openai.APIConnectionError,  # type: ignore[attr-defined]
            openai.APITimeoutError,  # type: ignore[attr-defined]
            openai.RateLimitError,  # type: ignore[attr-defined]
            openai.InternalServerError,  # type: ignore[attr-defined]
        )

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Iterable[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        top_p: float = 0.95,
        max_completion_tokens: int = 8192,
        parallel_tool_calls: bool = True,
    ) -> LlmResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "max_completion_tokens": max_completion_tokens,
        }
        tools_list = list(tools) if tools is not None else None
        if tools_list:
            kwargs["tools"] = tools_list
            kwargs["tool_choice"] = "auto"
            kwargs["parallel_tool_calls"] = parallel_tool_calls

        completion = self._chat(**kwargs)
        choice = completion.choices[0]
        msg = choice.message
        usage = getattr(completion, "usage", None)
        log.debug(
            "llm chat done (model=%s, prompt=%s, completion=%s, tools=%s)",
            self.model,
            getattr(usage, "prompt_tokens", "?") if usage else "?",
            getattr(usage, "completion_tokens", "?") if usage else "?",
            len(tools_list) if tools_list else 0,
        )

        tool_calls: list[LlmToolCall] = []
        for tc in (getattr(msg, "tool_calls", None) or []):
            tool_calls.append(
                LlmToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=tc.function.arguments or "",
                )
            )

        # Fallback: small/quantized models often emit the tool call as
        # fenced/tagged JSON in `content` instead of structured tool_calls,
        # which vLLM's parser then drops. Recover it so the loop can act.
        if not tool_calls and tools_list and msg.content:
            from loopcoder.llm.tool_fallback import extract_tool_calls

            allowed = {
                t.get("function", {}).get("name")
                for t in tools_list
                if t.get("function", {}).get("name")
            }
            for i, rec in enumerate(extract_tool_calls(msg.content, allowed)):
                tool_calls.append(
                    LlmToolCall(
                        id=f"fallback-{i}",
                        name=rec["name"],
                        arguments=rec["arguments"],
                    )
                )
            if tool_calls:
                log.warning(
                    "recovered %d tool call(s) from content fallback "
                    "(model %s did not return structured tool_calls)",
                    len(tool_calls),
                    self.model,
                )

        return LlmResponse(
            content=msg.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            raw=completion,
        )
