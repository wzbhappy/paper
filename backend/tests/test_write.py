"""正文写作服务测试。"""

import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_smoke.db")

import pytest

from app.llm import LLMClient, LLMRequest, TaskType
from app.llm.base import LLMResponse, Usage
from app.services.direction import PaperBrief
from app.services.summarize import PaperSummary
from app.services.write import (
    WriteAction,
    WriteError,
    write_section,
)


class FakeProvider:
    name = "fake"

    def __init__(self, reply: str = "生成的正文内容。") -> None:
        self.reply = reply
        self.calls: list[LLMRequest] = []

    async def complete(self, req: LLMRequest, model: str) -> LLMResponse:
        self.calls.append(req)
        return LLMResponse(
            content=self.reply, model=model, usage=Usage(prompt_tokens=1, completion_tokens=1)
        )

    async def stream(self, req: LLMRequest, model: str) -> AsyncIterator[str]:
        yield ""


def make_client(reply: str = "生成的正文内容。") -> tuple[LLMClient, FakeProvider]:
    client = LLMClient()
    provider = FakeProvider(reply)
    client._provider_for = lambda model: provider  # type: ignore[method-assign]
    return client, provider


BRIEFS = [
    PaperBrief(
        paper_id="p1",
        title="论文一",
        year=2022,
        summary=PaperSummary(one_line="总结一", method="方法一", conclusion="结论一"),
    ),
    PaperBrief(
        paper_id="p2",
        title="论文二",
        year=2023,
        summary=PaperSummary(one_line="总结二"),
    ),
]


@pytest.mark.asyncio
async def test_draft_uses_key_points_and_papers():
    client, provider = make_client("研究表明 [1] 有效，后续 [2] 扩展了该方法。")
    result = await write_section(
        "proj",
        WriteAction.DRAFT,
        "方法 > 整体框架",
        paper_title="某论文",
        key_points=["描述整体架构", "说明模块协作"],
        briefs=BRIEFS,
        client=client,
    )
    assert result.paper_ids == ["p1", "p2"]
    assert result.invalid_citations == []
    assert result.used_papers is True
    prompt = provider.calls[0].messages[0].content
    assert "描述整体架构" in prompt
    assert "[1] 论文一" in prompt
    assert "方法 > 整体框架" in prompt


@pytest.mark.asyncio
async def test_draft_requires_key_points_or_selection():
    client, _ = make_client()
    with pytest.raises(WriteError):
        await write_section("proj", WriteAction.DRAFT, "某节", client=client)


@pytest.mark.parametrize(
    "action",
    [
        WriteAction.EXPAND,
        WriteAction.REWRITE,
        WriteAction.POLISH,
        WriteAction.ACADEMIC,
        WriteAction.TRANSLATE,
        WriteAction.DEDUP,
    ],
)
@pytest.mark.asyncio
async def test_selection_required_actions_reject_empty(action):
    client, provider = make_client()
    with pytest.raises(WriteError):
        await write_section("proj", action, "某节", selection="  ", client=client)
    # 参数不合法时不应浪费 LLM 调用
    assert provider.calls == []


@pytest.mark.asyncio
async def test_polish_does_not_expose_papers():
    """纯语言加工动作不传文献，避免模型硬塞引用。"""
    client, provider = make_client("润色后的文字。")
    result = await write_section(
        "proj",
        WriteAction.POLISH,
        "某节",
        selection="这个方法挺好的，效果不错。",
        briefs=BRIEFS,
        client=client,
    )
    assert result.paper_ids == []
    assert result.used_papers is False
    prompt = provider.calls[0].messages[0].content
    assert "论文一" not in prompt
    assert "不要添加任何" in prompt


@pytest.mark.asyncio
async def test_no_papers_strips_all_citations():
    """无可引用文献时，任何 [n] 都是编造，应全部剥离。"""
    client, _ = make_client("这个论断有支撑 [1]，那个也有 [2]。")
    result = await write_section(
        "proj",
        WriteAction.POLISH,
        "某节",
        selection="原文",
        client=client,
    )
    assert "[1]" not in result.content
    assert "[2]" not in result.content
    assert result.invalid_citations == [1, 2]


@pytest.mark.asyncio
async def test_out_of_range_citation_stripped():
    client, _ = make_client("真实引用 [1]，编造引用 [9]。")
    result = await write_section(
        "proj",
        WriteAction.DRAFT,
        "某节",
        key_points=["要点"],
        briefs=BRIEFS,
        client=client,
    )
    assert "[1]" in result.content
    assert "[9]" not in result.content
    assert result.invalid_citations == [9]


@pytest.mark.asyncio
async def test_actions_route_to_expected_tasks():
    cases = [
        (WriteAction.DRAFT, TaskType.REVIEW_GEN),
        (WriteAction.POLISH, TaskType.POLISH),
        (WriteAction.TRANSLATE, TaskType.TRANSLATE),
        (WriteAction.DEDUP, TaskType.POLISH),
    ]
    for action, expected in cases:
        client, provider = make_client()
        kwargs = {"key_points": ["x"]} if action is WriteAction.DRAFT else {"selection": "原文"}
        await write_section("proj", action, "某节", client=client, **kwargs)
        assert provider.calls[0].task is expected


@pytest.mark.asyncio
async def test_context_is_truncated():
    client, provider = make_client()
    long_before = "前" * 2000
    long_after = "后" * 2000
    await write_section(
        "proj",
        WriteAction.DRAFT,
        "某节",
        key_points=["要点"],
        context_before=long_before,
        context_after=long_after,
        client=client,
    )
    prompt = provider.calls[0].messages[0].content
    # 上下文各截断到 300 字符，不应把 2000 字全塞进去
    assert prompt.count("前") <= 320
    assert prompt.count("后") <= 320


@pytest.mark.asyncio
async def test_translate_prompt_mentions_language():
    client, provider = make_client("Translated text.")
    await write_section(
        "proj",
        WriteAction.TRANSLATE,
        "某节",
        selection="中文原文",
        language="英文",
        client=client,
    )
    prompt = provider.calls[0].messages[0].content
    assert "英文" in prompt
    assert "中文原文" in prompt


@pytest.mark.asyncio
async def test_dedup_prompt_requires_preserving_citations():
    client, provider = make_client("改写后的文字。")
    await write_section(
        "proj",
        WriteAction.DEDUP,
        "某节",
        selection="原文 [1]",
        client=client,
    )
    prompt = provider.calls[0].messages[0].content
    assert "保留原有的论点" in prompt or "保留原文中已有的引用标记" in prompt


@pytest.mark.asyncio
async def test_draft_with_rag_context():
    from app.parser.chunker import Chunk
    from app.rag import HashEmbedder, InMemoryVectorStore, index_chunks

    embedder = HashEmbedder(dim=64)
    store = InMemoryVectorStore()
    await index_chunks(
        "proj",
        "p1",
        [Chunk(text="two stage encoder architecture detail", index=0, section="Method")],
        paper_title="论文一",
        embedder=embedder,
        store=store,
    )
    client, provider = make_client("正文 [1]。")
    await write_section(
        "proj",
        WriteAction.DRAFT,
        "方法",
        key_points=["two stage encoder architecture"],
        briefs=BRIEFS,
        client=client,
        embedder=embedder,
        store=store,
    )
    assert "相关原文片段" in provider.calls[0].messages[0].content


@pytest.mark.asyncio
async def test_rag_failure_does_not_block():
    class BrokenEmbedder:
        dim = 8

        async def embed(self, texts):
            raise RuntimeError("embedding down")

    from app.rag import InMemoryVectorStore

    client, _ = make_client("正文内容。")
    result = await write_section(
        "proj",
        WriteAction.DRAFT,
        "某节",
        key_points=["要点"],
        briefs=BRIEFS,
        client=client,
        embedder=BrokenEmbedder(),
        store=InMemoryVectorStore(),
    )
    assert result.content == "正文内容。"


@pytest.mark.asyncio
async def test_write_bypasses_cache():
    """同样的请求应每次真正调用模型，否则改写动作会返回相同结果。"""
    client, provider = make_client("结果。")
    for _ in range(2):
        await write_section(
            "proj", WriteAction.POLISH, "某节", selection="同样的原文", client=client
        )
    assert len(provider.calls) == 2
