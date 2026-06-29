import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mapper import anthropic_request_to_responses, determine_stop_reason
from schemas import AnthropicMessagesRequest, UpstreamResponse
from server import app


def test_system_string_and_message_string_map_to_responses() -> None:
    request = AnthropicMessagesRequest.model_validate(
        {
            "model": "llama3.2",
            "max_tokens": 16,
            "system": "Be terse",
            "messages": [{"role": "user", "content": "Hello"}],
        }
    )
    payload = anthropic_request_to_responses(request)
    assert payload["instructions"] == "Be terse"
    assert payload["max_output_tokens"] == 16
    assert payload["input"] == [
        {"role": "user", "content": [{"type": "input_text", "text": "Hello"}]}
    ]


def test_system_block_array_concatenates_text() -> None:
    request = AnthropicMessagesRequest.model_validate(
        {
            "model": "llama3.2",
            "max_tokens": 16,
            "system": [
                {"type": "text", "text": "Line one"},
                {"type": "text", "text": "Line two"},
            ],
            "messages": [{"role": "user", "content": [{"type": "text", "text": "Ping"}]}],
        }
    )
    payload = anthropic_request_to_responses(request)
    assert payload["instructions"] == "Line one\n\nLine two"
    assert payload["input"] == [
        {"role": "user", "content": [{"type": "input_text", "text": "Ping"}]}
    ]


def test_stop_reason_max_tokens_when_incomplete() -> None:
    response = UpstreamResponse.model_validate(
        {
            "id": "resp_1",
            "status": "incomplete",
            "output": [],
            "usage": {"input_tokens": 1, "output_tokens": 2},
            "incomplete_details": {"reason": "max_output_tokens"},
        }
    )
    assert determine_stop_reason(response) == "max_tokens"


def test_count_tokens_stub() -> None:
    client = TestClient(app)
    response = client.post("/v1/messages/count_tokens", json={"foo": "bar"})
    assert response.status_code == 200
    assert response.json() == {"input_tokens": max(1, len(json.dumps({"foo": "bar"})) // 4)}
