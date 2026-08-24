"""项目进展汇总 API：驱动前端阶段引导。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_project
from app.db import get_session
from app.models import (
    ManuscriptSection,
    OutlineSection,
    Paper,
    Project,
    ResearchDirection,
    ReviewDraft,
)
from app.schemas import ProgressOut, StageStatusOut
from app.services.progress import ProgressSignals, build_progress

router = APIRouter()


async def _count(session: AsyncSession, stmt) -> int:
    return int((await session.execute(stmt)).scalar() or 0)


@router.get("", response_model=ProgressOut)
async def get_progress(
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    """汇总客观进展信号，给出建议的下一步。"""
    pid = project.id

    paper_count = await _count(
        session, select(func.count(Paper.id)).where(Paper.project_id == pid)
    )
    parsed_count = await _count(
        session,
        select(func.count(Paper.id)).where(
            Paper.project_id == pid, Paper.status == "ready"
        ),
    )
    summarized_count = await _count(
        session,
        select(func.count(Paper.id)).where(
            Paper.project_id == pid, Paper.summary.is_not(None)
        ),
    )
    direction_count = await _count(
        session,
        select(func.count(ResearchDirection.id)).where(
            ResearchDirection.project_id == pid
        ),
    )
    selected_count = await _count(
        session,
        select(func.count(ResearchDirection.id)).where(
            ResearchDirection.project_id == pid,
            ResearchDirection.selected.is_(True),
        ),
    )
    review_count = await _count(
        session,
        select(func.count(ReviewDraft.id)).where(ReviewDraft.project_id == pid),
    )
    outline_count = await _count(
        session,
        select(func.count(OutlineSection.id)).where(OutlineSection.project_id == pid),
    )
    written_count = await _count(
        session,
        select(func.count(ManuscriptSection.id)).where(
            ManuscriptSection.project_id == pid, ManuscriptSection.word_count > 0
        ),
    )
    total_words = await _count(
        session,
        select(func.coalesce(func.sum(ManuscriptSection.word_count), 0)).where(
            ManuscriptSection.project_id == pid
        ),
    )

    # 质量错误数需要组装稿件，仅在已有正文时计算，避免无谓开销
    quality_errors = 0
    if total_words > 0:
        from app.api.manuscript import _assemble
        from app.services.quality import check_manuscript

        report = check_manuscript(await _assemble(session, project))
        quality_errors = report.error_count

    signals = ProgressSignals(
        paper_count=paper_count,
        parsed_paper_count=parsed_count,
        summarized_count=summarized_count,
        direction_count=direction_count,
        has_selected_direction=selected_count > 0,
        review_count=review_count,
        outline_section_count=outline_count,
        written_section_count=written_count,
        total_word_count=total_words,
        quality_error_count=quality_errors,
    )
    progress = build_progress(signals, project.stage)

    return ProgressOut(
        current_stage=progress.current_stage,
        suggested_stage=progress.suggested_stage,
        next_action=progress.next_action,
        completion=progress.completion,
        stages=[
            StageStatusOut(key=s.key, label=s.label, done=s.done, detail=s.detail)
            for s in progress.stages
        ],
        paper_count=paper_count,
        parsed_paper_count=parsed_count,
        summarized_count=summarized_count,
        direction_count=direction_count,
        has_selected_direction=selected_count > 0,
        review_count=review_count,
        outline_section_count=outline_count,
        written_section_count=written_count,
        total_word_count=total_words,
        quality_error_count=quality_errors,
    )
