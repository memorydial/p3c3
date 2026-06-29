import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mapper import anthropic_request_to_responses, responses_to_anthropic_message
from schemas import AnthropicMessagesRequest, UpstreamResponse


def test_tool_schema_and_tool_choice_mapping() -> None:
    request = AnthropicMessagesRequest.model_validate(
        {
            "model": "qwen2.5",
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "Need weather"}],
            "tools": [
                {
                    "name": "get_weather",
                    "description": "Get weather",
                    "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
                }
            ],
            "tool_choice": {"type": "any"},
        }
    )
    payload = anthropic_request_to_responses(request)
    assert payload["tools"] == [
        {
            "type": "function",
            "name": "get_weather",
            "description": "Get weather",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        }
    ]
    assert payload["tool_choice"] == "required"


def test_assistant_tool_use_maps_to_function_call_input() -> None:
    request = AnthropicMessagesRequest.model_validate(
        {
            "model": "qwen2.5",
            "max_tokens": 16,
            "messages": [
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "call_1", "name": "get_weather", "input": {"city": "Vancouver"}}],
                }
            ],
        }
    )
    payload = anthropic_request_to_responses(request)
    assert payload["input"] == [
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "get_weather",
            "arguments": '{"city": "Vancouver"}',
        }
    ]


def test_user_tool_result_maps_to_function_call_output() -> None:
    request = AnthropicMessagesRequest.model_validate(
        {
            "model": "qwen2.5",
            "max_tokens": 16,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": [{"type": "text", "text": "rain"}]}],
                }
            ],
        }
    )
    payload = anthropic_request_to_responses(request)
    assert payload["input"] == [{"type": "function_call_output", "call_id": "call_1", "output": "rain"}]


def test_function_call_output_maps_back_to_tool_use_block() -> None:
    response = UpstreamResponse.model_validate(
        {
            "id": "resp_1",
            "status": "completed",
            "output": [
                {
                    "id": "fc_1",
                    "type": "function_call",
                    "status": "completed",
                    "call_id": "call_1",
                    "name": "get_weather",
                    "arguments": '{"city":"Vancouver"}',
                }
            ],
            "usage": {"input_tokens": 2, "output_tokens": 3},
        }
    )
    mapped = responses_to_anthropic_message(response, requested_model="qwen2.5")
    assert mapped["content"] == [
        {"type": "tool_use", "id": "call_1", "name": "get_weather", "input": {"city": "Vancouver"}}
    ]
    assert mapped["stop_reason"] == "tool_use"
