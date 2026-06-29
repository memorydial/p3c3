import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streaming import translate_responses_sse


async def _collect(lines: list[str]) -> list[str]:
    async def source():
        for line in lines:
            yield line

    chunks = []
    async for chunk in translate_responses_sse(source(), requested_model="llama3.2"):
        chunks.append(chunk.decode("utf-8"))
    return chunks


def test_streaming_text_sequence_matches_anthropic_grammar() -> None:
    lines = [
        'event: response.created\n',
        'data: {"response":{"id":"resp_1","status":"in_progress"}}\n',
        '\n',
        'event: response.output_text.delta\n',
        'data: {"output_index":0,"delta":"OK"}\n',
        '\n',
        'event: response.output_text.done\n',
        'data: {"output_index":0}\n',
        '\n',
        'event: response.completed\n',
        'data: {"response":{"id":"resp_1","status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"OK"}]}],"usage":{"input_tokens":3,"output_tokens":2}}}\n',
        '\n',
    ]
    chunks = asyncio.run(_collect(lines))
    assert [chunk.split("\n", 1)[0] for chunk in chunks] == [
        "event: message_start",
        "event: ping",
        "event: content_block_start",
        "event: content_block_delta",
        "event: content_block_stop",
        "event: message_delta",
        "event: message_stop",
    ]
    assert json.loads(chunks[0].split("data: ", 1)[1])["message"]["id"] == "resp_1"
    assert json.loads(chunks[3].split("data: ", 1)[1])["delta"]["text"] == "OK"


def test_streaming_ignores_unknown_events_and_maps_tool_use() -> None:
    lines = [
        'event: response.created\n',
        'data: {"response":{"id":"resp_tool","status":"in_progress"}}\n',
        '\n',
        'event: response.content_part.added\n',
        'data: {"ignored":true}\n',
        '\n',
        'event: response.output_item.added\n',
        'data: {"output_index":1,"item":{"type":"function_call","call_id":"call_1","name":"get_weather"}}\n',
        '\n',
        'event: response.function_call_arguments.delta\n',
        'data: {"output_index":1,"delta":"{\\"city\\":\\"Vancouver\\"}"}\n',
        '\n',
        'event: response.output_item.done\n',
        'data: {"output_index":1}\n',
        '\n',
        'event: response.completed\n',
        'data: {"response":{"id":"resp_tool","status":"completed","output":[{"type":"function_call","call_id":"call_1","name":"get_weather","arguments":"{\\"city\\":\\"Vancouver\\"}"}],"usage":{"input_tokens":4,"output_tokens":5}}}\n',
        '\n',
    ]
    chunks = asyncio.run(_collect(lines))
    serialized = "".join(chunks)
    assert "response.content_part.added" not in serialized
    assert '"type": "tool_use"' in serialized
    assert '"stop_reason": "tool_use"' in serialized
