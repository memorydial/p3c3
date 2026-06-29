import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mapper import anthropic_request_to_responses
from models import load_model_map, resolve_model
from schemas import AnthropicMessagesRequest


def test_load_model_map_and_resolve_alias() -> None:
    model_map = load_model_map('{"sonnet-codex":"gpt-5.1-codex","llama3.2":"llama3.2:latest"}')
    assert resolve_model("sonnet-codex", model_map) == "gpt-5.1-codex"
    assert resolve_model("llama3.2", model_map) == "llama3.2:latest"


def test_resolve_passthrough_and_invalid_map() -> None:
    assert load_model_map("{bad json}") == {}
    assert resolve_model("qwen2.5", {}) == "qwen2.5"


def test_request_payload_uses_alias_map() -> None:
    request = AnthropicMessagesRequest.model_validate(
        {
            "model": "llama3.2",
            "max_tokens": 8,
            "messages": [{"role": "user", "content": "Hello"}],
        }
    )
    payload = anthropic_request_to_responses(request, {"llama3.2": "llama3.2:latest"})
    assert payload["model"] == "llama3.2:latest"
