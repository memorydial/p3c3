from __future__ import annotations

import json
from typing import Any

from models import resolve_model
from schemas import AnthropicMessagesRequest, UpstreamOutputItem, UpstreamResponse


def blocks_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    if not isinstance(content, list):
        return str(content)
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "\n\n".join(part for part in parts if part)


def stringify_tool_result_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return blocks_to_text(content)
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    if content is None:
        return ""
    return str(content)


def extract_system_instructions(system: str | list[dict[str, Any]] | None) -> str | None:
    text = blocks_to_text(system)
    return text or None


def _text_part_type(role: str) -> str:
    # Responses API role-correct typing, enforced strictly by the codex backend:
    #   assistant (echoed history)      -> output_text
    #   user / system / developer input -> input_text
    # Both directions 400 if wrong. Claude Code's in-array system messages type as input_text.
    # Ollama tolerates either.
    return "output_text" if role == "assistant" else "input_text"


def message_to_input_items(message: dict[str, Any]) -> list[dict[str, Any]]:
    role = message["role"]
    content = message["content"]
    if isinstance(content, str):
        return [
            {
                "role": role,
                "content": [{"type": _text_part_type(role), "text": content}],
            }
        ]
    items: list[dict[str, Any]] = []
    for raw_block in content:
        block = dict(raw_block)
        block_type = block.get("type")
        if block_type == "tool_use" and role == "assistant":
            items.append(
                {
                    "type": "function_call",
                    "call_id": block.get("id"),
                    "name": block.get("name"),
                    "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                }
            )
            continue
        if block_type == "tool_result" and role == "user":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": block.get("tool_use_id"),
                    "output": stringify_tool_result_content(block.get("content")),
                }
            )
            continue
        text = block.get("text")
        if isinstance(text, str):
            items.append(
                {
                    "role": role,
                    "content": [{"type": _text_part_type(role), "text": text}],
                }
            )
    return items


def map_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    return [
        {
            "type": "function",
            "name": tool["name"],
            "description": tool.get("description"),
            "parameters": tool.get("input_schema") or {},
        }
        for tool in tools
    ]


def map_tool_choice(tool_choice: dict[str, Any] | None) -> str | dict[str, str] | None:
    if not tool_choice:
        return None
    choice_type = tool_choice.get("type")
    if choice_type == "auto":
        return "auto"
    if choice_type == "any":
        return "required"
    if choice_type == "tool" and isinstance(tool_choice.get("name"), str):
        return {"type": "function", "name": tool_choice["name"]}
    return None


def anthropic_request_to_responses(
    request: AnthropicMessagesRequest,
    model_map: dict[str, str] | None = None,
    codex_mode: bool = False,
    *,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    reasoning_summary: str | None = None,
    verbosity: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": resolve_model(request.model, model_map),
        "input": [],
        "stream": request.stream,
    }
    # The ChatGPT codex backend 400s on max_output_tokens; vanilla OpenAI/Ollama accept it.
    if not codex_mode:
        payload["max_output_tokens"] = request.max_tokens
    else:
        # Codex backend mandates store=false (non-stateful) and rejects the default.
        payload["store"] = False
    instructions = extract_system_instructions(request.model_dump().get("system"))
    if instructions:
        payload["instructions"] = instructions
    elif codex_mode:
        # The ChatGPT codex backend 400s with "Instructions are required" if absent.
        payload["instructions"] = "You are a helpful coding assistant."
    # Generation tuning: env override wins over a per-request temperature; reasoning
    # effort/summary and text verbosity are codex/GPT-5 controls with no Anthropic
    # equivalent, so they only ever come from proxy config.
    effective_temperature = temperature if temperature is not None else request.temperature
    if effective_temperature is not None:
        payload["temperature"] = effective_temperature
    reasoning: dict[str, str] = {}
    if reasoning_effort is not None:
        reasoning["effort"] = reasoning_effort
    if reasoning_summary is not None and reasoning_summary != "none":
        reasoning["summary"] = reasoning_summary
    if reasoning:
        payload["reasoning"] = reasoning
    if verbosity is not None:
        payload["text"] = {"verbosity": verbosity}
    tools = map_tools(request.model_dump().get("tools"))
    if tools:
        payload["tools"] = tools
    tool_choice = map_tool_choice(
        request.tool_choice.model_dump() if hasattr(request.tool_choice, "model_dump") else request.tool_choice
    )
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    for message in request.model_dump().get("messages", []):
        payload["input"].extend(message_to_input_items(message))
    return payload


def _parse_tool_input(raw_arguments: str | None) -> dict[str, Any]:
    if not raw_arguments:
        return {}
    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def determine_stop_reason(response: UpstreamResponse) -> str:
    if any(item.type == "function_call" for item in response.output):
        return "tool_use"
    reason = response.incomplete_details.reason if response.incomplete_details else None
    if response.status == "incomplete" and reason == "max_output_tokens":
        return "max_tokens"
    return "end_turn"


def upstream_item_to_anthropic_block(item: UpstreamOutputItem) -> list[dict[str, Any]]:
    if item.type == "function_call":
        return [
            {
                "type": "tool_use",
                "id": item.call_id,
                "name": item.name,
                "input": _parse_tool_input(item.arguments),
            }
        ]
    if item.type != "message":
        return []
    blocks: list[dict[str, Any]] = []
    for part in item.content or []:
        if part.type == "output_text" and isinstance(part.text, str):
            blocks.append({"type": "text", "text": part.text})
    return blocks


def responses_to_anthropic_message(
    response: UpstreamResponse,
    requested_model: str,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for item in response.output:
        content.extend(upstream_item_to_anthropic_block(item))
    return {
        "id": response.id,
        "type": "message",
        "role": "assistant",
        "model": requested_model,
        "content": content,
        "stop_reason": determine_stop_reason(response),
        "stop_sequence": None,
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
    }
