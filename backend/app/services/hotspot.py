"""研究热点分析：关键词趋势、共现网络、研究空白识别。

数据来源优先用项目文献库（已有结构化摘要，质量高）；库内文献不足时
可选择性地补充外部检索结果的元数据。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from loguru import logger
from pydantic import BaseModel, Field, field_validator

from app.llm import LLMClient, LLMRequest, Message, Role, TaskType, get_llm
from app.llm.prompts import render
from app.services.direction import PaperBrief

# 近期窗口：默认把最近 N 年视为「近期」，用于判断趋势
RECENT_YEARS = 3
MIN_TERM_COUNT = 2
TOP_TERMS = 25
TOP_PAIRS = 15

_STOPWORDS = {
    "method",
    "methods",
    "approach",
    "model",
    "models",
    "based",
    "using",
    "novel",
    "new",
    "study",
    "analysis",
    "research",
    "paper",
    "results",
    "data",
    "方法",
    "模型",
    "研究",
    "分析",
    "基于",
    "一种",
    "新型",
}


def normalize_term(term: str) -> str:
    """术语归一化：小写、去首尾标点、压空白。"""
    cleaned = re.sub(r"[^\w\s\u4e00-\u9fff-]", " ", (term or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()


@dataclass
class TermTrend:
    term: str
    count: int
    recent_count: int
    trend: str
    """rising / stable / declining / unknown"""
    recent_share: float | None = None
    years: list[int] = field(default_factory=list)


@dataclass
class TermPair:
    a: str
    b: str
    count: int


@dataclass
class HotspotStats:
    """纯统计结果，不含 LLM 推断，可独立展示。"""

    total_papers: int = 0
    papers_with_terms: int = 0
    year_range: tuple[int | None, int | None] = (None, None)
    trends: list[TermTrend] = field(default_factory=list)
    cooccurrence: list[TermPair] = field(default_factory=list)
    isolated_terms: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


class RawGap(BaseModel):
    statement: str
    reason: str | None = None
    signal: str = "limitation_cluster"
    difficulty: float = 0.5
    evidence_indices: list[int] = Field(default_factory=list)

    @field_validator("difficulty")
    @classmethod
    def clamp(cls, v: float) -> float:
        if v > 1:
            v = v / 100 if v > 10 else v / 10
        return max(0.0, min(1.0, v))


class RawGapList(BaseModel):
    gaps: list[RawGap] = Field(default_factory=list)


class ResearchGap(BaseModel):
    statement: str
    reason: str | None = None
    signal: str = "limitation_cluster"
    difficulty: float = 0.5
    evidence_paper_ids: list[str] = Field(default_factory=list)
    evidence_titles: list[str] = Field(default_factory=list)


@dataclass
class HotspotReport:
    stats: HotspotStats
    gaps: list[ResearchGap] = field(default_factory=list)
    seed_keywords: list[str] = field(default_factory=list)


def _terms_of(brief: PaperBrief) -> list[str]:
    """提取一篇文献的关键术语，去停用词后归一化。"""
    summary = brief.summary
    raw: list[str] = list(summary.key_terms) if summary else []
    out: list[str] = []
    for term in raw:
        norm = normalize_term(term)
        if not norm or norm in _STOPWORDS or len(norm) < 2:
            continue
        if norm not in out:
            out.append(norm)
    return out


def compute_stats(
    briefs: list[PaperBrief], recent_years: int = RECENT_YEARS
) -> HotspotStats:
    """计算关键词趋势与共现。纯统计，无 LLM 调用。"""
    stats = HotspotStats(total_papers=len(briefs))
    if not briefs:
        return stats

    years = [b.year for b in briefs if b.year]
    if years:
        stats.year_range = (min(years), max(years))
        # 近期窗口相对于库内最新年份，而非当前系统时间，避免旧文献库全被判为过时
        recent_threshold = max(years) - recent_years + 1
    else:
        recent_threshold = None

    total_counter: Counter[str] = Counter()
    recent_counter: Counter[str] = Counter()
    years_by_term: dict[str, list[int]] = {}
    pair_counter: Counter[tuple[str, str]] = Counter()
    cooccur_partners: dict[str, set[str]] = {}

    for brief in briefs:
        # 局限信息独立收集：没有关键术语的文献同样可能报告有价值的局限
        if brief.summary:
            for limitation in brief.summary.limitations:
                text = limitation.strip()
                if text and text not in stats.limitations:
                    stats.limitations.append(text)

        terms = _terms_of(brief)
        if not terms:
            continue
        stats.papers_with_terms += 1

        is_recent = (
            recent_threshold is not None
            and brief.year is not None
            and brief.year >= recent_threshold
        )
        for term in terms:
            total_counter[term] += 1
            if is_recent:
                recent_counter[term] += 1
            if brief.year:
                years_by_term.setdefault(term, []).append(brief.year)

        for i, a in enumerate(terms):
            for b in terms[i + 1 :]:
                pair_counter[tuple(sorted((a, b)))] += 1
                cooccur_partners.setdefault(a, set()).add(b)
                cooccur_partners.setdefault(b, set()).add(a)

    for term, count in total_counter.most_common(TOP_TERMS):
        if count < MIN_TERM_COUNT:
            continue
        recent = recent_counter.get(term, 0)
        share = recent / count if count else None
        if recent_threshold is None or not years:
            trend = "unknown"
        elif share is None:
            trend = "unknown"
        elif share >= 0.6:
            trend = "rising"
        elif share <= 0.2:
            trend = "declining"
        else:
            trend = "stable"
        stats.trends.append(
            TermTrend(
                term=term,
                count=count,
                recent_count=recent,
                trend=trend,
                recent_share=round(share, 2) if share is not None else None,
                years=sorted(years_by_term.get(term, [])),
            )
        )

    stats.cooccurrence = [
        TermPair(a=a, b=b, count=count)
        for (a, b), count in pair_counter.most_common(TOP_PAIRS)
        if count >= MIN_TERM_COUNT
    ]

    # 热度不低但共现伙伴少 → 可能是尚未被交叉研究的方向
    hot_terms = {t.term for t in stats.trends}
    stats.isolated_terms = sorted(
        term
        for term in hot_terms
        if len(cooccur_partners.get(term, set())) <= 1
    )

    logger.info(
        "hotspot stats: {} papers, {} terms, {} pairs",
        stats.total_papers,
        len(stats.trends),
        len(stats.cooccurrence),
    )
    return stats


def _resolve_evidence(
    raw: RawGap, briefs: list[PaperBrief]
) -> tuple[list[str], list[str]]:
    paper_ids: list[str] = []
    titles: list[str] = []
    for index in raw.evidence_indices:
        if 1 <= index <= len(briefs):
            brief = briefs[index - 1]
            if brief.paper_id not in paper_ids:
                paper_ids.append(brief.paper_id)
                titles.append(brief.title or brief.paper_id)
        else:
            logger.warning("hotspot: gap cites out-of-range index {}", index)
    return paper_ids, titles


async def analyze_hotspots(
    briefs: list[PaperBrief],
    seed_keywords: list[str] | None = None,
    n: int = 3,
    language: str = "中文",
    discipline: str | None = None,
    require_evidence: bool = True,
    client: LLMClient | None = None,
) -> HotspotReport:
    """统计 + LLM 推断研究空白。

    文献不足时只返回统计结果，不调 LLM——数据太少时的 gap 推断没有意义。
    """
    stats = compute_stats(briefs)
    report = HotspotReport(stats=stats, seed_keywords=list(seed_keywords or []))

    if len(briefs) < 3 or not stats.trends:
        logger.info("hotspot: insufficient data for gap inference")
        return report

    prompt = render(
        "hotspot",
        discipline=discipline,
        n=n,
        language=language,
        trends=[
            {
                "term": t.term,
                "count": t.count,
                "trend": t.trend,
                "recent_share": t.recent_share,
            }
            for t in stats.trends
        ],
        cooccurrence=[
            {"a": p.a, "b": p.b, "count": p.count} for p in stats.cooccurrence
        ],
        isolated_terms=stats.isolated_terms,
        limitations=stats.limitations[:20],
        papers=[
            {
                "title": b.title,
                "year": b.year,
                "one_line": b.summary.one_line if b.summary else None,
            }
            for b in briefs
        ],
    )

    llm = client or get_llm()
    req = LLMRequest(
        messages=[Message(role=Role.USER, content=prompt)],
        task=TaskType.DIRECTION,
        temperature=0.5,
    )
    try:
        raw_list = await llm.complete_json(req, RawGapList, retries=1)
    except Exception as exc:
        logger.warning("hotspot gap inference failed: {}", exc)
        return report

    for raw in raw_list.gaps:
        paper_ids, titles = _resolve_evidence(raw, briefs)
        if require_evidence and not paper_ids:
            logger.warning("hotspot: drop gap without evidence: {}", raw.statement[:60])
            continue
        report.gaps.append(
            ResearchGap(
                statement=raw.statement,
                reason=raw.reason,
                signal=raw.signal,
                difficulty=raw.difficulty,
                evidence_paper_ids=paper_ids,
                evidence_titles=titles,
            )
        )

    logger.info("hotspot: {} gaps identified", len(report.gaps))
    return report
