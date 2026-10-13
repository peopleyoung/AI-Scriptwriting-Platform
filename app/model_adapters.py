"""Provider HTTP contracts. Provider bodies and credentials never enter errors."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urlsplit

import httpx


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


def check_cancel(token: Any) -> None:
    if token is not None and getattr(token, "cancelled", False):
        raise ModelAdapterError("CANCELED", "model invocation was canceled")


class BaseModelAdapter:
    adapter_name = "base"

    def __init__(self, *, endpoint: str | None = None, credential: str | None = None, transport: httpx.BaseTransport | None = None):
        self.endpoint = endpoint
        self.credential = credential
        self.transport = transport

    def invoke(self, *, messages: list[dict[str, str]], model: str, parameters: dict[str, Any] | None = None, timeout: float | None = None, cancel_token: Any = None) -> AdapterResponse:
        raise NotImplementedError

    def stream(self, **kwargs: Any) -> Iterable[str]:
        yield self.invoke(**kwargs).text

    def classify_error(self, error: Exception) -> ModelAdapterError:
        if isinstance(error, ModelAdapterError):
            return error
        if isinstance(error, httpx.TimeoutException):
            return ModelAdapterError("TIMEOUT", "model request timed out", retryable=True)
        if isinstance(error, httpx.TransportError):
            return ModelAdapterError("TRANSIENT_PROVIDER_ERROR", "model provider connection failed", retryable=True)
        return ModelAdapterError("INVALID_PROVIDER_RESPONSE", "model provider returned an invalid response")


class MockModelAdapter(BaseModelAdapter):
    adapter_name = "mock"

    def invoke(self, *, messages: list[dict[str, str]], model: str, parameters: dict[str, Any] | None = None, timeout: float | None = None, cancel_token: Any = None) -> AdapterResponse:
        check_cancel(cancel_token)
        prompt = messages[-1].get("content", "") if messages else ""
        if any("MUJIAN_SCRIPT_JSON" in m.get("content", "") for m in messages):
            request = json.loads(prompt)
            path = request["target_path"]
            fragment = {"id": str(path.get("fragment", "generated")), "blocks": [{"type": "ACTION", "text": f"mock:{request.get('instruction') or '下一幕开始。'}"}]}
            scene = {"number": path.get("scene", 1), "heading": "场景 · 夜", "fragments": [fragment]}
            episode = {"number": path.get("episode", 1), "title": "新的故事", "scenes": [scene]}
            target = {"WORK": {"episodes": [episode]}, "EPISODE": episode, "SCENE": scene, "FRAGMENT": fragment}[request["target_type"]]
            text = json.dumps(target, ensure_ascii=False)
        else:
            text = f"已记录创作讨论：{prompt}"
        return AdapterResponse(text=text, stop_reason="stop", usage={"input_tokens": len(prompt), "output_tokens": len(text)})


class HTTPModelAdapter(BaseModelAdapter):
    path = ""
    default_endpoint = ""

    def _url(self) -> str:
        endpoint = (self.endpoint or self.default_endpoint).rstrip("/")
        parts = urlsplit(endpoint)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ModelAdapterError("INVALID_PARAMETERS", "model endpoint must be an HTTP URL without embedded credentials")
        return endpoint if parts.path.endswith(self.path) else endpoint + self.path

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _check_status(self, response: httpx.Response) -> None:
        code = response.status_code
        if 200 <= code < 300:
            return
        if code in {401, 403}:
            raise ModelAdapterError("AUTHENTICATION_ERROR", "model credential was rejected")
        if code == 429:
            raise ModelAdapterError("RATE_LIMITED", "model provider is rate limited", retryable=True)
        if code in {408, 409, 425} or code >= 500:
            raise ModelAdapterError("TRANSIENT_PROVIDER_ERROR", "model provider is temporarily unavailable", retryable=True)
        raise ModelAdapterError("INVALID_PARAMETERS", "model provider rejected the request")

    def _request(self, *, messages: list[dict[str, str]], model: str, parameters: dict[str, Any] | None, timeout: float | None, cancel_token: Any, streaming: bool):
        check_cancel(cancel_token)
        settings = dict(parameters or {})
        # Reserved request identity cannot be replaced through model parameters.
        settings.pop("messages", None)
        settings.pop("model", None)
        settings.pop("stream", None)
        payload = self.build_request(messages=messages, model=model, parameters=settings)
        payload["stream"] = streaming
        deadline = time.monotonic() + (timeout or 120)
        try:
            with httpx.Client(transport=self.transport, timeout=timeout or 120, follow_redirects=False) as client:
                with client.stream("POST", self._url(), json=payload, headers=self._headers()) as response:
                    self._check_status(response)
                    if not streaming:
                        raw = response.read()
                        if len(raw) > 4 * 1024 * 1024:
                            raise ModelAdapterError("INVALID_PROVIDER_RESPONSE", "model response is too large")
                        check_cancel(cancel_token)
                        yield json.loads(raw)
                        return
                    for line in response.iter_lines():
                        check_cancel(cancel_token)
                        if time.monotonic() > deadline:
                            raise ModelAdapterError("TIMEOUT", "model request timed out", retryable=True)
                        if line.startswith("data:"):
                            value = line[5:].strip()
                            if value == "[DONE]":
                                return
                            if value:
                                yield json.loads(value)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise self.classify_error(exc) from None

    def invoke(self, *, messages: list[dict[str, str]], model: str, parameters: dict[str, Any] | None = None, timeout: float | None = None, cancel_token: Any = None) -> AdapterResponse:
        # Streaming allows cancellation between provider events and captures usage.
        text = []; usage = {}; stop = None
        streaming = (parameters or {}).get("stream", True) is not False
        size = 0
        for event in self._request(messages=messages, model=model, parameters=parameters, timeout=timeout, cancel_token=cancel_token, streaming=streaming):
            if not isinstance(event, dict):
                raise ModelAdapterError("INVALID_PROVIDER_RESPONSE", "model provider returned an invalid response")
            if event.get("type") == "error" or event.get("error"):
                raise ModelAdapterError("TRANSIENT_PROVIDER_ERROR", "model provider interrupted the response", retryable=True)
            if not streaming:
                parsed = self.parse_response(event)
                text.append(parsed.text)
                usage.update(parsed.usage)
                stop = parsed.stop_reason
                continue
            chunks = list(self.parse_stream([event]))
            size += sum(len(chunk) for chunk in chunks)
            if size > 4 * 1024 * 1024:
                raise ModelAdapterError("INVALID_PROVIDER_RESPONSE", "model response is too large")
            text.extend(chunks)
            if any((choice.get("delta") or {}).get("refusal") for choice in event.get("choices", [])):
                raise ModelAdapterError("CONTENT_POLICY_ERROR", "model declined this request")
            if self.adapter_name == "anthropic":
                usage.update(event.get("message", {}).get("usage", {}))
                usage.update(event.get("usage", {}))
                stop = event.get("delta", {}).get("stop_reason") or stop
            else:
                usage.update(event.get("usage") or {})
                choices = event.get("choices") or []
                if choices:
                    stop = choices[0].get("finish_reason") or stop
        check_cancel(cancel_token)
        if not all(isinstance(chunk, str) for chunk in text):
            raise ModelAdapterError("INVALID_PROVIDER_RESPONSE", "model provider returned invalid text")
        result = "".join(text)
        if not result.strip():
            raise ModelAdapterError("EMPTY_MODEL_RESPONSE", "model returned no text")
        if stop in {"content_filter", "refusal"}:
            raise ModelAdapterError("CONTENT_POLICY_ERROR", "model declined this request")
        usage = {key: value for key, value in usage.items() if key in {"input_tokens", "output_tokens", "prompt_tokens", "completion_tokens", "total_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"} and isinstance(value, int) and not isinstance(value, bool) and value >= 0}
        return AdapterResponse(result, stop, usage)

    def stream(self, **kwargs: Any) -> Iterable[str]:
        for event in self._request(parameters=kwargs.pop("parameters", None), timeout=kwargs.pop("timeout", None), cancel_token=kwargs.pop("cancel_token", None), streaming=True, **kwargs):
            yield from self.parse_stream([event])


class OpenAICompatibleAdapter(HTTPModelAdapter):
    adapter_name = "openai"
    path = "/chat/completions"
    default_endpoint = "https://api.openai.com/v1"

    def _headers(self):
        if not self.credential:
            raise ModelAdapterError("AUTHENTICATION_ERROR", "configure a model credential before calling the provider")
        return {"Authorization": f"Bearer {self.credential}", "Content-Type": "application/json"}

    def build_request(self, *, messages, model, parameters=None):
        return {**(parameters or {}), "model": model, "messages": messages}

    def parse_response(self, payload):
        choice = (payload.get("choices") or [{}])[0]
        return AdapterResponse(text=(choice.get("message") or {}).get("content", ""), stop_reason=choice.get("finish_reason"), usage=payload.get("usage") or {})

    def parse_stream(self, chunks):
        for chunk in chunks:
            choices = chunk.get("choices") or []
            if choices:
                delta = choices[0].get("delta") or {}
                if isinstance(delta.get("content"), str):
                    yield delta["content"]


class AnthropicAdapter(HTTPModelAdapter):
    adapter_name = "anthropic"
    path = "/messages"
    default_endpoint = "https://api.anthropic.com/v1"

    def _headers(self):
        if not self.credential:
            raise ModelAdapterError("AUTHENTICATION_ERROR", "configure a model credential before calling the provider")
        return {"x-api-key": self.credential, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}

    def build_request(self, *, messages, model, parameters=None):
        system = "\n".join(item["content"] for item in messages if item.get("role") == "system")
        payload = {"max_tokens": 8192, **(parameters or {}), "model": model, "messages": [m for m in messages if m.get("role") != "system"]}
        if system:
            payload["system"] = system
        return payload

    def parse_response(self, payload):
        return AdapterResponse(text="".join(b.get("text", "") for b in payload.get("content", []) if isinstance(b, dict)), stop_reason=payload.get("stop_reason"), usage=payload.get("usage") or {})

    def parse_stream(self, chunks):
        for chunk in chunks:
            if isinstance(chunk.get("delta", {}).get("text"), str):
                yield chunk["delta"]["text"]


def adapter_for(name: str, **kwargs: Any) -> BaseModelAdapter:
    normalized = name.lower()
    if normalized in {"anthropic", "claude"}:
        return AnthropicAdapter(**kwargs)
    if normalized in {"openai", "openai-compatible", "openai_compatible"}:
        return OpenAICompatibleAdapter(**kwargs)
    if normalized == "mock":
        return MockModelAdapter(**kwargs)
    raise ModelAdapterError("UNSUPPORTED_ADAPTER", "unsupported model adapter")
