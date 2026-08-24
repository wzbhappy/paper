"""热点分析测试：统计部分纯计算，gap 推断用 fake provider。"""

import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_smoke.db")

import pytest

from app.llm import LLMClient, LLMRequest
from app.llm.base import LLMResponse, Usage
from app.services.direction import PaperBrief
from app.services.hotspot import (
    analyze_hotspots,
    compute_stats,
    normalize_term,
)
from app.services.summarize import PaperSummary


class FakeProvider:
    name = "fake"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[LLMRequest] = []

    async def complete(self, req: LLMRequest, model: str) -> LLMResponse:
        self.calls.append(req)
        return LLMResponse(
            content=self.reply, model=model, usage=Usage(prompt_tokens=1, completion_tokens=1)
        )

    async def stream(self, req: LLMRequest, model: str) -> AsyncIterator[str]:
        yield ""


def make_client(reply: str) -> tuple[LLMClient, FakeProvider]:
    client = LLMClient()
    provider = FakeProvider(reply)
    client._provider_for = lambda model: provider  # type: ignore[method-assign]
    return client, provider


def brief(
    pid: str,
    year: int,
    terms: list[str],
    limitations: list[str] | None = None,
    title: str | None = None,
) -> PaperBrief:
    return PaperBrief(
        paper_id=pid,
        title=title or f"论文 {pid}",
        year=year,
        summary=PaperSummary(
            one_line=f"{pid} 的总结",
            key_terms=terms,
            limitations=limitations or [],
        ),
    )


# ---------- 归一化 ----------


def test_normalize_term():
    assert normalize_term("Graph Neural Network!") == "graph neural network"
    assert normalize_term("  多余   空白  ") == "多余 空白"
    assert normalize_term("") == ""


# ---------- 统计 ----------


def test_compute_stats_empty():
    stats = compute_stats([])
    assert stats.total_papers == 0
    assert stats.trends == []


def test_compute_stats_counts_terms():
    briefs = [
        brief("p1", 2023, ["图神经网络", "引文网络"]),
        brief("p2", 2023, ["图神经网络", "对比学习"]),
        brief("p3", 2022, ["图神经网络"]),
    ]
    stats = compute_stats(briefs)
    assert stats.total_papers == 3
    assert stats.papers_with_terms == 3
    top = stats.trends[0]
    assert top.term == "图神经网络"
    assert top.count == 3


def test_compute_stats_filters_low_frequency():
    briefs = [
        brief("p1", 2023, ["常见术语", "冷门术语一"]),
        brief("p2", 2023, ["常见术语", "冷门术语二"]),
    ]
    stats = compute_stats(briefs)
    terms = {t.term for t in stats.trends}
    assert "常见术语" in terms
    # 只出现 1 次的术语被过滤
    assert "冷门术语一" not in terms


def test_compute_stats_filters_stopwords():
    briefs = [
        brief("p1", 2023, ["method", "图神经网络"]),
        brief("p2", 2023, ["method", "图神经网络"]),
    ]
    stats = compute_stats(briefs)
    terms = {t.term for t in stats.trends}
    assert "method" not in terms
    assert "图神经网络" in terms


def test_trend_rising_when_concentrated_in_recent_years():
    briefs = [
        brief("p1", 2024, ["新兴方向"]),
        brief("p2", 2024, ["新兴方向"]),
        brief("p3", 2023, ["新兴方向"]),
    ]
    stats = compute_stats(briefs, recent_years=3)
    trend = next(t for t in stats.trends if t.term == "新兴方向")
    assert trend.trend == "rising"
    assert trend.recent_share == 1.0


def test_trend_declining_when_mostly_old():
    briefs = [brief(f"p{i}", 2015, ["旧方向"]) for i in range(5)]
    briefs.append(brief("recent", 2024, ["旧方向"]))
    stats = compute_stats(briefs, recent_years=3)
    trend = next(t for t in stats.trends if t.term == "旧方向")
    assert trend.trend == "declining"


def test_trend_window_is_relative_to_library_not_wall_clock():
    """全是旧文献时，最新的那批仍应被视为「近期」。"""
    briefs = [
        brief("p1", 2010, ["老主题"]),
        brief("p2", 2011, ["老主题"]),
        brief("p3", 2012, ["老主题"]),
    ]
    stats = compute_stats(briefs, recent_years=2)
    trend = next(t for t in stats.trends if t.term == "老主题")
    # 2011-2012 属于近期窗口，占 2/3
    assert trend.recent_count == 2
    assert trend.trend in ("rising", "stable")


def test_compute_stats_year_range():
    briefs = [brief("p1", 2018, ["a", "b"]), brief("p2", 2024, ["a", "b"])]
    stats = compute_stats(briefs)
    assert stats.year_range == (2018, 2024)


def test_cooccurrence_pairs():
    briefs = [
        brief("p1", 2023, ["图神经网络", "引文网络"]),
        brief("p2", 2023, ["图神经网络", "引文网络"]),
        brief("p3", 2023, ["图神经网络", "对比学习"]),
    ]
    stats = compute_stats(briefs)
    pairs = {(p.a, p.b): p.count for p in stats.cooccurrence}
    key = tuple(sorted(("图神经网络", "引文网络")))
    assert pairs.get(key) == 2


def test_isolated_terms_detected():
    briefs = [
        brief("p1", 2023, ["核心主题", "配套主题"]),
        brief("p2", 2023, ["核心主题", "配套主题"]),
        brief("p3", 2023, ["孤立主题"]),
        brief("p4", 2023, ["孤立主题"]),
    ]
    stats = compute_stats(briefs)
    assert "孤立主题" in stats.isolated_terms


def test_limitations_collected_and_deduped():
    briefs = [
        brief("p1", 2023, ["a", "b"], limitations=["样本量不足", "缺少消融"]),
        brief("p2", 2023, ["a", "b"], limitations=["样本量不足"]),
    ]
    stats = compute_stats(briefs)
    assert stats.limitations.count("样本量不足") == 1
    assert "缺少消融" in stats.limitations


def test_papers_without_terms_not_counted():
    briefs = [
        brief("p1", 2023, []),
        brief("p2", 2023, ["有术语", "另一个"]),
        brief("p3", 2023, ["有术语", "另一个"]),
    ]
    stats = compute_stats(briefs)
    assert stats.total_papers == 3
    assert stats.papers_with_terms == 2


# ---------- gap 推断 ----------

GAPS = json.dumps(
    {
        "gaps": [
            {
                "statement": "动态图上的对比学习尚未被验证",
                "reason": "已有工作只在静态图上做过",
                "signal": "missing_intersection",
                "difficulty": 0.6,
                "evidence_indices": [1, 2],
            }
        ]
    },
    ensure_ascii=False,
)

MANY_BRIEFS = [
    brief("p1", 2024, ["图神经网络", "对比学习"], ["缺少动态图验证"]),
    brief("p2", 2024, ["图神经网络", "引文网络"], ["数据规模有限"]),
    brief("p3", 2023, ["图神经网络", "对比学习"]),
    brief("p4", 2022, ["引文网络", "时序建模"]),
]


@pytest.mark.asyncio
async def test_analyze_hotspots_returns_gaps():
    client, provider = make_client(GAPS)
    report = await analyze_hotspots(MANY_BRIEFS, seed_keywords=["图神经网络"], client=client)
    assert len(report.gaps) == 1
    gap = report.gaps[0]
    assert gap.evidence_paper_ids == ["p1", "p2"]
    assert gap.signal == "missing_intersection"
    assert gap.difficulty == 0.6
    assert report.seed_keywords == ["图神经网络"]

    prompt = provider.calls[0].messages[0].content
    assert "关键词趋势" in prompt
    assert "缺少动态图验证" in prompt


@pytest.mark.asyncio
async def test_analyze_hotspots_skips_llm_when_too_few_papers():
    client, provider = make_client(GAPS)
    report = await analyze_hotspots(MANY_BRIEFS[:2], client=client)
    assert report.gaps == []
    assert provider.calls == []
    # 统计结果仍然返回
    assert report.stats.total_papers == 2


@pytest.mark.asyncio
async def test_analyze_hotspots_drops_gap_without_evidence():
    client, _ = make_client(
        json.dumps(
            {
                "gaps": [
                    {"statement": "有证据", "evidence_indices": [1]},
                    {"statement": "凭空编造", "evidence_indices": []},
                ]
            },
            ensure_ascii=False,
        )
    )
    report = await analyze_hotspots(MANY_BRIEFS, client=client)
    assert [g.statement for g in report.gaps] == ["有证据"]


@pytest.mark.asyncio
async def test_analyze_hotspots_ignores_out_of_range_evidence():
    client, _ = make_client(
        json.dumps(
            {"gaps": [{"statement": "gap", "evidence_indices": [1, 99, 0]}]},
            ensure_ascii=False,
        )
    )
    report = await analyze_hotspots(MANY_BRIEFS, client=client)
    assert report.gaps[0].evidence_paper_ids == ["p1"]


@pytest.mark.asyncio
async def test_analyze_hotspots_survives_llm_failure():
    client, _ = make_client("not json")
    report = await analyze_hotspots(MANY_BRIEFS, client=client)
    assert report.gaps == []
    # 统计部分不受影响
    assert report.stats.trends


@pytest.mark.asyncio
async def test_gap_difficulty_normalized():
    client, _ = make_client(
        json.dumps(
            {"gaps": [{"statement": "g", "difficulty": 70, "evidence_indices": [1]}]},
            ensure_ascii=False,
        )
    )
    report = await analyze_hotspots(MANY_BRIEFS, client=client)
    assert report.gaps[0].difficulty == pytest.approx(0.7)
