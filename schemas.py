from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AllowExtraModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class ContentBlock(AllowExtraModel):
    type: str | None = None
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None
    tool_use_id: str | None = None
    content: Any = None


class MessageParam(AllowExtraModel):
    role: str
    content: str | list[ContentBlock]


class ToolParam(AllowExtraModel):
    name: str
    description: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)


class ToolChoiceParam(AllowExtraModel):
    type: str
    name: str | None = None


class AnthropicMessagesRequest(AllowExtraModel):
    model: str
    max_tokens: int
    messages: list[MessageParam]
    system: str | list[ContentBlock] | None = None
    stream: bool = False
    temperature: float | None = None
    tools: list[ToolParam] | None = None
    tool_choice: ToolChoiceParam | dict[str, Any] | None = None


class UpstreamUsage(AllowExtraModel):
    input_tokens: int = 0
    output_tokens: int = 0


class UpstreamContentPart(AllowExtraModel):
    type: str | None = None
    text: str | None = None


class UpstreamOutputItem(AllowExtraModel):
    id: str | None = None
    type: str | None = None
    status: str | None = None
    role: str | None = None
    content: list[UpstreamContentPart] | None = None
    call_id: str | None = None
    name: str | None = None
    arguments: str | None = None


class UpstreamIncompleteDetails(AllowExtraModel):
    reason: str | None = None


class UpstreamResponse(AllowExtraModel):
    id: str
    object: str | None = None
    status: str | None = None
    model: str | None = None
    output: list[UpstreamOutputItem] = Field(default_factory=list)
    usage: UpstreamUsage = Field(default_factory=UpstreamUsage)
    incomplete_details: UpstreamIncompleteDetails | None = None


class UpstreamSsePayload(AllowExtraModel):
    pass
