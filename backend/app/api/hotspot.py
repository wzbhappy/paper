"""研究热点分析 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_project
from app.db import get_session
from app.models import Paper, Project
from app.schemas import (
    HotspotReportOut,
    HotspotRequest,
    ResearchGapOut,
    TermPairOut,
    TermTrendOut,
)
from app.services.direction import PaperBrief
from app.services.hotspot import analyze_hotspots
from app.services.summarize import PaperSummary

router = APIRouter()

AVAILABLE_STATUSES = ("ready", "metadata_only")


def _to_brief(paper: Paper) -> PaperBrief:
    summary = None
    if paper.summary:
        try:
            summary = PaperSummary.model_validate(paper.summary)
        except Exception:
            summary = None
    return PaperBrief(
        paper_id=str(paper.id), title=paper.title, year=paper.year, summary=summary
    )


@router.post("", response_model=HotspotReportOut)
async def analyze(
    data: HotspotRequest,
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    """分析项目文献库的关键词趋势与研究空白。

    同步执行：统计部分是纯计算，gap 推断只需一次 LLM 调用。
    """
    papers = (
        (
            await session.execute(
                select(Paper).where(
                    Paper.project_id == project.id,
                    Paper.status.in_(AVAILABLE_STATUSES),
                )
            )
        )
        .scalars()
        .all()
    )
    if not papers:
        raise HTTPException(
            status_code=400,
            detail="no papers in this project; import or upload papers first",
        )

    report = await analyze_hotspots(
        [_to_brief(p) for p in papers],
        seed_keywords=data.seed_keywords,
        n=data.n,
        discipline=project.discipline,
    )

    stats = report.stats
    return HotspotReportOut(
        total_papers=stats.total_papers,
        papers_with_terms=stats.papers_with_terms,
        year_from=stats.year_range[0],
        year_to=stats.year_range[1],
        trends=[
            TermTrendOut(
                term=t.term,
                count=t.count,
                recent_count=t.recent_count,
                trend=t.trend,
                recent_share=t.recent_share,
            )
            for t in stats.trends
        ],
        cooccurrence=[
            TermPairOut(a=p.a, b=p.b, count=p.count) for p in stats.cooccurrence
        ],
        isolated_terms=stats.isolated_terms,
        limitations=stats.limitations[:20],
        gaps=[
            ResearchGapOut(
                statement=g.statement,
                reason=g.reason,
                signal=g.signal,
                difficulty=g.difficulty,
                evidence_paper_ids=g.evidence_paper_ids,
                evidence_titles=g.evidence_titles,
            )
            for g in report.gaps
        ],
        seed_keywords=report.seed_keywords,
    )
