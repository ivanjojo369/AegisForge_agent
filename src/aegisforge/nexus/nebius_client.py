"""Nebius Token Factory adapter for AegisForge Nexus.

Location in repository: src/aegisforge/nexus/nebius_client.py

Uses Nebius's OpenAI-compatible Chat Completions API. This module does not
execute tools or shell commands; it only sends explicitly requested inference
calls and returns model outputs to the calling orchestrator.

Required environment variable:
    NEBIUS_API_KEY

Optional environment variables:
    NEBIUS_BASE_URL  (default: https://api.tokenfactory.nebius.com/v1)
    NEBIUS_MODEL     (default: nvidia/Nemotron-3_5-Lightning)

Dependencies: requests (already a dependency of AegisForge Agent).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

import requests


DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1"
DEFAULT_MODEL = "nvidia/Nemotron-3_5-Lightning"


class NebiusConfigurationError(ValueError):
    """Invalid or missing configuration; no network request was sent."""


class NebiusAPIError(RuntimeError):
    """Nebius inference failure, with credentials and response bodies redacted."""


@dataclass(frozen=True, slots=True)
class NebiusReply:
    """Normalized output from a single Nebius Chat Completions request."""

    content: str
    model: str
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    tool_calls: tuple[dict[str, Any], ...] = ()
    request_id: str | None = None


class NebiusClient:
    """Small synchronous API adapter for the Nexus planner and verifier.

    A call to ``chat`` always sends at most one HTTP request: there are no
    implicit retries or automatic tool executions, making costs predictable.
    Do not pass untrusted model-proposed tool calls directly to an executor.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 60.0,
        session: requests.Session | None = None,
    ) -> None:
        key = api_key if api_key is not None else os.getenv("NEBIUS_API_KEY", "")
        if not isinstance(key, str) or not key.strip():
            raise NebiusConfigurationError(
                "NEBIUS_API_KEY is missing. Set it in your environment; do not commit it."
            )

        raw_url = base_url or os.getenv("NEBIUS_BASE_URL") or DEFAULT_BASE_URL
        normalized_url = raw_url.rstrip("/")
        parsed = urlsplit(normalized_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise NebiusConfigurationError("NEBIUS_BASE_URL must be a valid HTTPS URL.")
        if parsed.query or parsed.fragment:
            raise NebiusConfigurationError("NEBIUS_BASE_URL must not contain a query or fragment.")
        if not (parsed.path == "/v1" or parsed.path.endswith("/v1")):
            raise NebiusConfigurationError("NEBIUS_BASE_URL must end with /v1.")

        chosen_model = model or os.getenv("NEBIUS_MODEL") or DEFAULT_MODEL
        if not isinstance(chosen_model, str) or not chosen_model.strip():
            raise NebiusConfigurationError("A nonempty Nebius model ID is required.")
        if timeout_seconds <= 0:
            raise NebiusConfigurationError("timeout_seconds must be positive.")

        self._api_key = key.strip()
        self.base_url = normalized_url
        self.model = chosen_model.strip()
        self.timeout_seconds = float(timeout_seconds)
        self._owns_session = session is None
        self._session = session or requests.Session()

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        model: str | None = None,
        max_tokens: int = 512,
        temperature: float | None = 0.2,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: str | Mapping[str, Any] | None = None,
        response_format: Mapping[str, Any] | None = None,
        chat_template_kwargs: Mapping[str, Any] | None = None,
    ) -> NebiusReply:
        """Request one completion; no automatic retries or tool execution.

        The model's tool_calls are *proposals* and must pass the Nexus
        allowlist, argument validation, and authorization before execution.
        """
        if not messages:
            raise NebiusConfigurationError("messages must not be empty.")
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
            raise NebiusConfigurationError("max_tokens must be a positive integer.")
        if temperature is not None and not 0 <= temperature <= 2:
            raise NebiusConfigurationError("temperature must be between 0 and 2.")

        safe_messages: list[dict[str, Any]] = []
        for item in messages:
            if not isinstance(item, Mapping) or item.get("role") not in {
                "system", "developer", "user", "assistant", "tool"
            }:
                raise NebiusConfigurationError("Each message needs a supported role.")
            safe_messages.append(dict(item))

        selected_model = model or self.model
        payload: dict[str, Any] = {
            "model": selected_model,
            "messages": safe_messages,
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if tools is not None:
            payload["tools"] = [dict(tool) for tool in tools]
        if tool_choice is not None:
            if tools is None:
                raise NebiusConfigurationError("tool_choice requires tools.")
            payload["tool_choice"] = tool_choice
        if response_format is not None:
            # Only standard JSON-object mode is used in the review-only path.
            if not isinstance(response_format, Mapping) or dict(response_format) != {"type": "json_object"}:
                raise NebiusConfigurationError("Unsupported response_format.")
            payload["response_format"] = {"type": "json_object"}
        if chat_template_kwargs is not None:
            # This is an opt-in Nemotron parameter; never used for other calls.
            if not isinstance(chat_template_kwargs, Mapping) or dict(chat_template_kwargs) != {"enable_thinking": False}:
                raise NebiusConfigurationError("Unsupported chat template options.")
            payload["chat_template_kwargs"] = {"enable_thinking": False}

        result = self._request("POST", "/chat/completions", json=payload)
        choices = result.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise NebiusAPIError("Nebius returned no valid completion choices.")
        first = choices[0]
        message = first.get("message")
        if not isinstance(message, dict):
            raise NebiusAPIError("Nebius returned a malformed assistant message.")

        raw_content = message.get("content")
        if raw_content is None:
            content = ""
        elif isinstance(raw_content, str):
            content = raw_content
        elif isinstance(raw_content, list):
            content = "".join(
                str(part.get("text", ""))
                for part in raw_content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        else:
            raise NebiusAPIError("Nebius returned an unsupported content format.")

        calls = message.get("tool_calls") or []
        if not isinstance(calls, list):
            raise NebiusAPIError("Nebius returned malformed tool calls.")

        usage = result.get("usage")
        return NebiusReply(
            content=content,
            model=str(result.get("model") or selected_model),
            finish_reason=first.get("finish_reason"),
            usage=dict(usage) if isinstance(usage, dict) else {},
            tool_calls=tuple(dict(call) for call in calls if isinstance(call, dict)),
            request_id=result.get("id") if isinstance(result.get("id"), str) else None,
        )

    def chat_json(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        max_tokens: int = 4096,
        temperature: float | None = None,
    ) -> NebiusReply:
        """One JSON-mode request for Nemotron code proposals; never retry.

        Nebius uses an OpenAI-compatible API. These options follow the
        Nemotron 3.5 structured-output guidance; the provider may still
        reject unsupported parameters, in which case the caller must stop.
        """
        if not self.model.casefold().startswith("nvidia/") or "nemotron" not in self.model.casefold():
            raise NebiusConfigurationError("Structured proposal mode requires NVIDIA Nemotron.")
        return self.chat(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format={"type": "json_object"},
            chat_template_kwargs={"enable_thinking": False},
        )

    def list_model_ids(self) -> list[str]:
        """Read available catalog model IDs (HTTP GET; does not run inference)."""
        result = self._request("GET", "/models")
        data = result.get("data")
        if not isinstance(data, list):
            raise NebiusAPIError("Nebius returned an invalid models listing.")
        return sorted(
            item["id"]
            for item in data
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._session.request(
                method,
                f"{self.base_url}{path}",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout_seconds,
                **kwargs,
            )
        except requests.Timeout as exc:
            raise NebiusAPIError(
                "Nebius request timed out; its billing/execution status is unknown. "
                "Do not retry automatically."
            ) from exc
        except requests.RequestException as exc:
            raise NebiusAPIError(f"Nebius network request failed ({type(exc).__name__}).") from exc

        if not 200 <= response.status_code < 300:
            # Never print provider error bodies: they may echo user content.
            raise NebiusAPIError(f"Nebius returned HTTP {response.status_code}.")
        try:
            result = response.json()
        except ValueError as exc:
            raise NebiusAPIError("Nebius returned invalid JSON.") from exc
        if not isinstance(result, dict):
            raise NebiusAPIError("Nebius returned an unexpected response structure.")
        return result

    def close(self) -> None:
        """Close only the HTTP session created by this client."""
        if self._owns_session:
            self._session.close()

    def __enter__(self) -> NebiusClient:
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "NebiusClient",
    "NebiusReply",
    "NebiusAPIError",
    "NebiusConfigurationError",
]
