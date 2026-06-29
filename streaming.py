from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator

from mapper import determine_stop_reason
from schemas import UpstreamResponse, UpstreamSsePayload


@dataclass
class BlockState:
    index: int
    kind: str
    output_index: int
    call_id: str | None = None
    name: str | None = None
    closed: bool = False


def sse_event(event: str, payload: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


async def iter_sse_events(lines: AsyncIterator[str | bytes]) -> AsyncIterator[tuple[str | None, str]]:
    event_name: str | None = None
    data_lines: list[str] = []
    async for raw_line in lines:
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        line = line.rstrip("\n")
        if line.endswith("\r"):
            line = line[:-1]
        if line == "":
            if event_name is not None or data_lines:
                yield event_name, "\n".join(data_lines)
            event_name = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].lstrip()
            continue
        if line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
    if event_name is not None or data_lines:
        yield event_name, "\n".join(data_lines)


async def aggregate_responses_message(
    upstream_lines: AsyncIterator[str | bytes],
    requested_model: str,
) -> dict[str, Any]:
    """Consume a Responses SSE stream and assemble one non-stream Anthropic message.

    Used in codex mode for non-stream clients (e.g. `claude -p`): the codex backend is
    stream-only, so the upstream stream is consumed and the deltas are folded into a single
    message - text from output_text.delta, tool calls from function_call events, usage/stop
    from response.completed. Mirrors translate_responses_sse's event understanding.
    """
    response_id = "msg_proxy"
    order: list[int] = []
    text_by_index: dict[int, list[str]] = {}
    tool_by_index: dict[int, dict[str, Any]] = {}
    usage = {"input_tokens": 0, "output_tokens": 0}
    saw_tool_use = False
    stop_reason = "end_turn"

    def ensure(idx: int) -> None:
        if idx not in order:
            order.append(idx)

    async for event_name, data in iter_sse_events(upstream_lines):
        if data == "[DONE]":
            break
        payload = UpstreamSsePayload.model_validate(json.loads(data or "{}")).model_dump()
        response_data = payload.get("response") if isinstance(payload.get("response"), dict) else {}
        if event_name == "response.created" and response_data.get("id"):
            response_id = str(response_data["id"])
        if event_name == "response.output_text.delta":
            idx = int(payload.get("output_index", 0))
            ensure(idx)
            text_by_index.setdefault(idx, []).append(str(payload.get("delta", "")))
        elif event_name == "response.output_item.added":
            item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
            if item.get("type") == "function_call":
                idx = int(payload.get("output_index", 0))
                ensure(idx)
                tool_by_index[idx] = {"call_id": item.get("call_id"), "name": item.get("name"), "args": ""}
                saw_tool_use = True
        elif event_name == "response.function_call_arguments.delta":
            idx = int(payload.get("output_index", 0))
            ensure(idx)
            entry = tool_by_index.setdefault(idx, {"call_id": payload.get("call_id"), "name": payload.get("name"), "args": ""})
            entry["args"] += str(payload.get("delta", ""))
            saw_tool_use = True
        elif event_name == "response.completed":
            upstream_response = UpstreamResponse.model_validate(response_data)
            usage = {
                "input_tokens": upstream_response.usage.input_tokens,
                "output_tokens": upstream_response.usage.output_tokens,
            }
            stop_reason = determine_stop_reason(upstream_response)
            if saw_tool_use and stop_reason == "end_turn":
                stop_reason = "tool_use"
            break

    content: list[dict[str, Any]] = []
    for idx in order:
        if idx in tool_by_index:
            t = tool_by_index[idx]
            try:
                parsed = json.loads(t["args"]) if t["args"] else {}
            except json.JSONDecodeError:
                parsed = {}
            content.append({"type": "tool_use", "id": t["call_id"], "name": t["name"], "input": parsed if isinstance(parsed, dict) else {}})
        elif idx in text_by_index:
            content.append({"type": "text", "text": "".join(text_by_index[idx])})
    return {
        "id": response_id,
        "type": "message",
        "role": "assistant",
        "model": requested_model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage,
    }


async def translate_responses_sse(
    upstream_lines: AsyncIterator[str | bytes],
    requested_model: str,
) -> AsyncIterator[bytes]:
    started = False
    response_id = "msg_proxy"
    next_index = 0
    blocks: dict[int, BlockState] = {}
    saw_tool_use = False

    async for event_name, data in iter_sse_events(upstream_lines):
        if data == "[DONE]":
            break
        payload = UpstreamSsePayload.model_validate(json.loads(data or "{}")).model_dump()
        response_data = payload.get("response") if isinstance(payload.get("response"), dict) else {}
        if event_name == "response.created" and response_data.get("id"):
            response_id = str(response_data["id"])
        if not started and event_name in {"response.created", "response.in_progress", "response.output_item.added", "response.output_text.delta", "response.function_call_arguments.delta", "response.completed"}:
            yield sse_event(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": response_id,
                        "type": "message",
                        "role": "assistant",
                        "model": requested_model,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                },
            )
            yield sse_event("ping", {"type": "ping"})
            started = True
        if event_name == "response.output_item.added":
            item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
            output_index = int(payload.get("output_index", 0))
            if item.get("type") == "function_call":
                blocks[output_index] = BlockState(
                    index=next_index,
                    kind="tool_use",
                    output_index=output_index,
                    call_id=item.get("call_id"),
                    name=item.get("name"),
                )
                next_index += 1
                saw_tool_use = True
                yield sse_event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": blocks[output_index].index,
                        "content_block": {
                            "type": "tool_use",
                            "id": item.get("call_id"),
                            "name": item.get("name"),
                            "input": {},
                        },
                    },
                )
            continue
        if event_name == "response.output_text.delta":
            output_index = int(payload.get("output_index", 0))
            if output_index not in blocks:
                blocks[output_index] = BlockState(index=next_index, kind="text", output_index=output_index)
                next_index += 1
                yield sse_event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": blocks[output_index].index,
                        "content_block": {"type": "text", "text": ""},
                    },
                )
            yield sse_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": blocks[output_index].index,
                    "delta": {"type": "text_delta", "text": str(payload.get("delta", ""))},
                },
            )
            continue
        if event_name == "response.function_call_arguments.delta":
            output_index = int(payload.get("output_index", 0))
            if output_index not in blocks:
                blocks[output_index] = BlockState(
                    index=next_index,
                    kind="tool_use",
                    output_index=output_index,
                    call_id=payload.get("call_id"),
                    name=payload.get("name"),
                )
                next_index += 1
                saw_tool_use = True
                yield sse_event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": blocks[output_index].index,
                        "content_block": {
                            "type": "tool_use",
                            "id": payload.get("call_id"),
                            "name": payload.get("name"),
                            "input": {},
                        },
                    },
                )
            yield sse_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": blocks[output_index].index,
                    "delta": {"type": "input_json_delta", "partial_json": str(payload.get("delta", ""))},
                },
            )
            continue
        if event_name in {"response.output_text.done", "response.output_item.done", "response.function_call_arguments.done"}:
            output_index = int(payload.get("output_index", 0))
            block = blocks.get(output_index)
            if block and not block.closed:
                block.closed = True
                yield sse_event(
                    "content_block_stop",
                    {"type": "content_block_stop", "index": block.index},
                )
            continue
        if event_name == "response.completed":
            upstream_response = UpstreamResponse.model_validate(response_data)
            for block in blocks.values():
                if not block.closed:
                    block.closed = True
                    yield sse_event(
                        "content_block_stop",
                        {"type": "content_block_stop", "index": block.index},
                    )
            stop_reason = determine_stop_reason(upstream_response)
            if saw_tool_use and stop_reason == "end_turn":
                stop_reason = "tool_use"
            yield sse_event(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                    "usage": {"output_tokens": upstream_response.usage.output_tokens},
                },
            )
            yield sse_event("message_stop", {"type": "message_stop"})
            break

