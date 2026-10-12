"""Small provider adapter contract used by the generation worker.

Adapters return the same shape regardless of whether the eventual deployment
uses a streaming HTTP client or the deterministic local test substitute.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


class ModelAdapterError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class AdapterResponse:
    text: str
    stop_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    trace_id: str | None = None


class BaseModelAdapter:
    adapter_name = "base"

    def invoke(self, *, messages: list[dict[str, str]], model: str, parameters: dict[str, Any] | None = None, timeout: float | None = None, cancel_token: Any = None) -> AdapterResponse:
        raise NotImplementedError

    def stream(self, **kwargs: Any) -> Iterable[str]:
        response = self.invoke(**kwargs)
        yield response.text

    def classify_error(self, error: Exception) -> ModelAdapterError:
        if isinstance(error, ModelAdapterError):
            return error
        message = str(error)
        lowered = message.lower()
        if "auth" in lowered or "401" in lowered or "403" in lowered:
            return ModelAdapterError("AUTHENTICATION_ERROR", message)
        if "rate" in lowered or "429" in lowered:
            return ModelAdapterError("RATE_LIMITED", message, retryable=True)
        if "timeout" in lowered:
            return ModelAdapterError("TIMEOUT", message, retryable=True)
        if "parameter" in lowered or "400" in lowered:
            return ModelAdapterError("INVALID_PARAMETERS", message)
        if "policy" in lowered or "content" in lowered:
            return ModelAdapterError("CONTENT_POLICY_ERROR", message)
        return ModelAdapterError("TRANSIENT_PROVIDER_ERROR", message, retryable=True)


class MockModelAdapter(BaseModelAdapter):
    adapter_name = "mock"

    def invoke(self, *, messages: list[dict[str, str]], model: str, parameters: dict[str, Any] | None = None, timeout: float | None = None, cancel_token: Any = None) -> AdapterResponse:
        if cancel_token is not None and getattr(cancel_token, "cancelled", False):
            raise ModelAdapterError("CANCELED", "model invocation was canceled")
        prompt = messages[-1].get("content", "") if messages else ""
        return AdapterResponse(text=f"mock:{prompt}", stop_reason="stop", usage={"input_tokens": len(prompt), "output_tokens": 1})


class OpenAICompatibleAdapter(MockModelAdapter):
    adapter_name = "openai"

    def build_request(self, *, messages: list[dict[str, str]], model: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"model": model, "messages": messages, **(parameters or {})}

    def parse_response(self, payload: dict[str, Any]) -> AdapterResponse:
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return AdapterResponse(text=message.get("content", ""), stop_reason=choice.get("finish_reason"), usage=payload.get("usage") or {})

    def parse_stream(self, chunks: Iterable[dict[str, Any]]) -> Iterable[str]:
        for chunk in chunks:
            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            if delta.get("content"):
                yield delta["content"]


class AnthropicAdapter(MockModelAdapter):
    adapter_name = "anthropic"

    def build_request(self, *, messages: list[dict[str, str]], model: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        system = "\n".join(item["content"] for item in messages if item.get("role") == "system")
        user_messages = [item for item in messages if item.get("role") != "system"]
        payload = {"model": model, "messages": user_messages, **(parameters or {})}
        if system:
            payload["system"] = system
        return payload

    def parse_response(self, payload: dict[str, Any]) -> AdapterResponse:
        blocks = payload.get("content") or []
        text = "".join(block.get("text", "") for block in blocks if isinstance(block, dict))
        return AdapterResponse(text=text, stop_reason=payload.get("stop_reason"), usage=payload.get("usage") or {})

    def parse_stream(self, chunks: Iterable[dict[str, Any]]) -> Iterable[str]:
        for chunk in chunks:
            delta = chunk.get("delta") or {}
            if delta.get("text"):
                yield delta["text"]


def adapter_for(name: str) -> BaseModelAdapter:
    normalized = name.lower()
    if normalized in {"anthropic", "claude"}:
        return AnthropicAdapter()
    if normalized in {"openai", "openai-compatible", "openai_compatible"}:
        return OpenAICompatibleAdapter()
    if normalized == "mock":
        return MockModelAdapter()
    raise ModelAdapterError("UNSUPPORTED_ADAPTER", f"unsupported adapter: {name}")
