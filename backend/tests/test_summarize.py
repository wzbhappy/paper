"""结构化摘要服务测试：LLM 用 fake provider，正文裁剪逻辑用纯文本验证。"""

import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_smoke.db")

import json

import pytest

from app.llm import LLMClient, LLMRequest
from app.llm.base import LLMResponse, Usage
from app.services.summarize import (
    PaperSummary,
    _approx_tokens,
    prepare_content,
    summarize_paper,
)


class FakeProvider:
    name = "fake"

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[LLMRequest] = []

    async def complete(self, req: LLMRequest, model: str) -> LLMResponse:
        self.calls.append(req)
        content = self.replies.pop(0) if self.replies else "{}"
        return LLMResponse(
            content=content, model=model, usage=Usage(prompt_tokens=1, completion_tokens=1)
        )

    async def stream(self, req: LLMRequest, model: str) -> AsyncIterator[str]:
        yield ""


def make_client(replies: list[str]) -> tuple[LLMClient, FakeProvider]:
    client = LLMClient()
    provider = FakeProvider(replies)
    client._provider_for = lambda model: provider  # type: ignore[method-assign]
    return client, provider


FULL_SUMMARY = {
    "one_line": "提出两阶段编码器提升稀疏监督下的引文图表示学习",
    "problem": "稀疏监督下引文图表示学习效果差",
    "method": "两阶段编码器：局部结构编码 + 全局信号聚合",
    "dataset": "Cora, Citeseer, PubMed",
    "metrics": {"accuracy": "84.3%"},
    "conclusion": "在三个基准上均优于基线",
    "limitations": ["未在超大规模图上验证"],
    "future_work": ["扩展到动态图"],
    "key_terms": ["图神经网络", "稀疏监督"],
}


@pytest.mark.asyncio
async def test_summarize_paper_parses_full_schema():
    client, provider = make_client([json.dumps(FULL_SUMMARY, ensure_ascii=False)])
    result = await summarize_paper(
        "# Title\n\nSome body content that is long enough.",
        title="Title",
        client=client,
    )
    assert isinstance(result, PaperSummary)
    assert result.one_line is not None and "两阶段" in result.one_line
    assert result.metrics == {"accuracy": "84.3%"}
    assert result.limitations == ["未在超大规模图上验证"]
    assert len(provider.calls) == 1
    # prompt 应包含正文与标题
    assert "Some body content" in provider.calls[0].messages[0].content


@pytest.mark.asyncio
async def test_summarize_paper_tolerates_partial_fields():
    client, _ = make_client(['{"one_line": "只有一句话"}'])
    result = await summarize_paper("# T\n\nbody text here", client=client)
    assert result.one_line == "只有一句话"
    assert result.metrics == {}
    assert result.limitations == []
    assert result.problem is None


@pytest.mark.asyncio
async def test_summarize_paper_handles_null_fields():
    payload = {**FULL_SUMMARY, "dataset": None, "limitations": []}
    client, _ = make_client([json.dumps(payload, ensure_ascii=False)])
    result = await summarize_paper("# T\n\nbody text here", client=client)
    assert result.dataset is None
    assert result.limitations == []


@pytest.mark.asyncio
async def test_summarize_paper_retries_on_bad_json():
    client, provider = make_client(
        ["这不是 JSON", json.dumps(FULL_SUMMARY, ensure_ascii=False)]
    )
    result = await summarize_paper("# T\n\nbody text here", client=client)
    assert result.problem is not None
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_summarize_paper_skips_llm_on_empty_content():
    client, provider = make_client([json.dumps(FULL_SUMMARY)])
    result = await summarize_paper("   \n  ", client=client)
    assert result == PaperSummary()
    assert provider.calls == []


@pytest.mark.asyncio
async def test_summarize_paper_uses_discipline_override(tmp_path):
    client, provider = make_client([json.dumps(FULL_SUMMARY, ensure_ascii=False)])
    # 学科无专用模板时应回退到通用模板，不报错
    await summarize_paper("# T\n\nbody", discipline="nonexistent_field", client=client)
    assert len(provider.calls) == 1


def test_prepare_content_keeps_short_text_intact():
    md = "# T\n\nshort body"
    assert prepare_content(md, max_tokens=6000) == md


def test_prepare_content_drops_references_first():
    body = "word " * 3000
    md = (
        "# Paper\n\nAuthors here\n\n"
        f"## Abstract\n\n{'abs ' * 200}\n\n"
        f"## Introduction\n\n{body}\n\n"
        f"## References\n\n{body}\n"
    )
    trimmed = prepare_content(md, max_tokens=800)
    assert "Abstract" in trimmed
    assert "References" not in trimmed


def test_prepare_content_prefers_abstract_and_conclusion():
    filler = "word " * 2000
    md = (
        f"## Abstract\n\n{'abstract text ' * 30}\n\n"
        f"## Related Work\n\n{filler}\n\n"
        f"## Conclusion\n\n{'conclusion text ' * 30}\n"
    )
    trimmed = prepare_content(md, max_tokens=400)
    assert "abstract text" in trimmed
    assert "conclusion text" in trimmed
    assert _approx_tokens(trimmed) <= 500


def test_prepare_content_no_headings_truncates():
    md = "word " * 5000
    trimmed = prepare_content(md, max_tokens=200)
    assert len(trimmed) < len(md)


def test_prepare_content_preserves_original_order():
    md = (
        f"## Abstract\n\n{'a ' * 50}\n\n"
        f"## Introduction\n\n{'i ' * 50}\n\n"
        f"## Conclusion\n\n{'c ' * 50}\n"
    )
    trimmed = prepare_content(md, max_tokens=200)
    positions = [
        trimmed.find("Abstract"),
        trimmed.find("Introduction"),
        trimmed.find("Conclusion"),
    ]
    present = [p for p in positions if p != -1]
    assert present == sorted(present)
