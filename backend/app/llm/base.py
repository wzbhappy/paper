"""LLM 抽象层核心类型：请求/响应/任务枚举/Provider 协议。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class TaskType(str, Enum):
    """任务类型，用于把不同任务路由到不同模型（成本/能力权衡）。"""

    SUMMARIZE = "summarize"
    TRANSLATE = "translate"
    REVIEW_GEN = "review_gen"
    DIRECTION = "direction"
    OUTLINE = "outline"
    POLISH = "polish"
    KEYWORD_EXPAND = "keyword_expand"
    CHAT = "chat"


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class Message(BaseModel):
    role: Role
    content: str


class LLMRequest(BaseModel):
    messages: list[Message]
    task: TaskType = TaskType.CHAT
    model: str | None = None
    """显式指定模型；为 None 时按 task 路由。"""
    temperature: float = 0.3
    max_tokens: int | None = None
    json_mode: bool = False
    """要求模型返回严格 JSON。"""


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMResponse(BaseModel):
    content: str
    model: str
    usage: Usage = Field(default_factory=Usage)
    raw: dict[str, Any] | None = None


class LLMError(RuntimeError):
    """LLM 调用失败（网络、鉴权、限流、响应格式异常等）。"""


@runtime_checkable
class LLMProvider(Protocol):
    """所有 provider 实现该协议。"""

    name: str

    async def complete(self, req: LLMRequest, model: str) -> LLMResponse: ...

    def stream(self, req: LLMRequest, model: str) -> AsyncIterator[str]: ...
