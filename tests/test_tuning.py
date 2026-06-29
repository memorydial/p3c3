from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mapper import anthropic_request_to_responses
from models import ProxySettings, _env_choice, _env_float
from schemas import AnthropicMessagesRequest


def _req(temperature: float | None = None) -> AnthropicMessagesRequest:
    data = {
        "model": "gpt-5.5",
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "hi"}],
    }
    if temperature is not None:
        data["temperature"] = temperature
    return AnthropicMessagesRequest.model_validate(data)


def test_no_tuning_leaves_payload_untouched() -> None:
    payload = anthropic_request_to_responses(_req())
    assert "temperature" not in payload
    assert "reasoning" not in payload
    assert "text" not in payload


def test_env_temperature_overrides_request_temperature() -> None:
    payload = anthropic_request_to_responses(_req(temperature=0.9), temperature=0.1)
    assert payload["temperature"] == 0.1


def test_request_temperature_used_when_no_override() -> None:
    payload = anthropic_request_to_responses(_req(temperature=0.7))
    assert payload["temperature"] == 0.7


def test_reasoning_effort_and_summary_injected() -> None:
    payload = anthropic_request_to_responses(
        _req(), reasoning_effort="high", reasoning_summary="concise"
    )
    assert payload["reasoning"] == {"effort": "high", "summary": "concise"}


def test_reasoning_summary_none_is_dropped() -> None:
    payload = anthropic_request_to_responses(
        _req(), reasoning_effort="low", reasoning_summary="none"
    )
    assert payload["reasoning"] == {"effort": "low"}


def test_verbosity_maps_to_text_block() -> None:
    payload = anthropic_request_to_responses(_req(), verbosity="low")
    assert payload["text"] == {"verbosity": "low"}


def test_env_float_rejects_garbage(monkeypatch) -> None:
    monkeypatch.setenv("PROXY_TEMPERATURE", "not-a-number")
    assert _env_float("PROXY_TEMPERATURE") is None
    monkeypatch.setenv("PROXY_TEMPERATURE", "0.3")
    assert _env_float("PROXY_TEMPERATURE") == 0.3


def test_env_choice_rejects_unknown(monkeypatch) -> None:
    monkeypatch.setenv("PROXY_REASONING_EFFORT", "turbo")
    assert _env_choice("PROXY_REASONING_EFFORT", {"low", "medium", "high"}) is None
    monkeypatch.setenv("PROXY_REASONING_EFFORT", "HIGH")
    assert _env_choice("PROXY_REASONING_EFFORT", {"low", "medium", "high"}) == "high"


def test_from_env_reads_tuning(monkeypatch) -> None:
    monkeypatch.setenv("PROXY_TEMPERATURE", "0.2")
    monkeypatch.setenv("PROXY_REASONING_EFFORT", "medium")
    monkeypatch.setenv("PROXY_REASONING_SUMMARY", "auto")
    monkeypatch.setenv("PROXY_VERBOSITY", "high")
    s = ProxySettings.from_env()
    assert s.temperature == 0.2
    assert s.reasoning_effort == "medium"
    assert s.reasoning_summary == "auto"
    assert s.verbosity == "high"
