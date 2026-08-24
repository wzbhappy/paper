"""LLM 抽象层测试：用 fake provider，不打真实网络。"""

import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_smoke.db")

import pytest
from pydantic import BaseModel

from app.llm import LLMClient, LLMError, LLMRequest, Message, Role, TaskType
from app.llm.base import LLMResponse, Usage
from app.llm.prompts import render


class FakeProvider:
    """按预设脚本返回内容，记录收到的请求。"""

    name = "fake"

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[LLMRequest, str]] = []

    async def complete(self, req: LLMRequest, model: str) -> LLMResponse:
        self.calls.append((req, model))
        content = self.replies.pop(0) if self.replies else "{}"
        return LLMResponse(
            content=content,
            model=model,
            usage=Usage(prompt_tokens=10, completion_tokens=5),
        )

    async def stream(self, req: LLMRequest, model: str) -> AsyncIterator[str]:
        self.calls.append((req, model))
        for piece in (self.replies.pop(0) if self.replies else "").split():
            yield piece


def make_client(replies: list[str]) -> tuple[LLMClient, FakeProvider]:
    client = LLMClient()
    provider = FakeProvider(replies)
    client.register("fake", provider)
    # 覆盖路由，强制走 fake provider
    client._provider_for = lambda model: provider  # type: ignore[method-assign]
    return client, provider


def req(content: str = "hi", task: TaskType = TaskType.CHAT) -> LLMRequest:
    return LLMRequest(messages=[Message(role=Role.USER, content=content)], task=task)


@pytest.mark.asyncio
async def test_complete_records_usage():
    client, _ = make_client(["hello"])
    resp = await client.complete(req())
    assert resp.content == "hello"
    assert resp.usage.total_tokens == 15
    assert client.usage_log[0]["task"] == "chat"


@pytest.mark.asyncio
async def test_cache_avoids_second_call():
    client, provider = make_client(["first", "second"])
    first = await client.complete(req("same"))
    second = await client.complete(req("same"))
    assert first.content == second.content == "first"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_cache_key_differs_by_content():
    client, provider = make_client(["a", "b"])
    await client.complete(req("one"))
    await client.complete(req("two"))
    assert len(provider.calls) == 2


class Direction(BaseModel):
    statement: str
    feasibility: float


@pytest.mark.asyncio
async def test_complete_json_parses_fenced_output():
    client, _ = make_client(
        ['```json\n{"statement": "研究 A", "feasibility": 0.8}\n```']
    )
    result = await client.complete_json(req(), Direction)
    assert result.statement == "研究 A"
    assert result.feasibility == 0.8


@pytest.mark.asyncio
async def test_complete_json_retries_then_succeeds():
    client, provider = make_client(
        ["not json at all", '{"statement": "研究 B", "feasibility": 0.5}']
    )
    result = await client.complete_json(req(), Direction, retries=1)
    assert result.statement == "研究 B"
    assert len(provider.calls) == 2
    # 重试请求应带上失败反馈
    assert "不符合要求的 JSON" in provider.calls[1][0].messages[-1].content


@pytest.mark.asyncio
async def test_complete_json_raises_after_retries():
    client, _ = make_client(["nope", "still nope"])
    with pytest.raises(LLMError):
        await client.complete_json(req(), Direction, retries=1)


def test_routing_uses_task_mapping():
    client = LLMClient()
    assert client.resolve_model(TaskType.SUMMARIZE) == client.resolve_model(
        TaskType.SUMMARIZE
    )
    assert client.resolve_model(TaskType.SUMMARIZE, "explicit-model") == "explicit-model"


def test_prompt_render_summarize():
    text = render("summarize", content="正文内容", title="某论文", language="中文")
    assert "某论文" in text
    assert "正文内容" in text
    assert "one_line" in text


def test_prompt_render_missing_variable_raises():
    from jinja2 import UndefinedError

    with pytest.raises(UndefinedError):
        render("summarize", content="x")
