"""综述生成与引用图谱 API。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_project
from app.db import SessionLocal, get_session
from app.graph import get_citation_graph
from app.models import Paper, Project
from app.models import ReviewDraft as ReviewModel
from app.rag import get_embedder, get_vector_store
from app.schemas import (
    JobOut,
    ReviewGenerateRequest,
    ReviewOut,
    ReviewUpdate,
)
from app.services.direction import PaperBrief
from app.services.jobs import create_job, run_job
from app.services.review import generate_review
from app.services.summarize import PaperSummary

router = APIRouter()

# 有摘要或已解析的文献才能进入综述
REVIEW_READY_STATUSES = ("ready", "metadata_only")


def _to_brief(paper: Paper) -> PaperBrief:
    summary = None
    if paper.summary:
        try:
            summary = PaperSummary.model_validate(paper.summary)
        except Exception:
            summary = None
    if summary is None and paper.abstract:
        # 没跑过 LLM 摘要时，用原始摘要兜底，保证综述有内容可依据
        summary = PaperSummary(one_line=paper.abstract[:300])
    return PaperBrief(
        paper_id=str(paper.id), title=paper.title, year=paper.year, summary=summary
    )


@router.post("/generate", response_model=JobOut, status_code=202)
async def generate(
    data: ReviewGenerateRequest,
    background: BackgroundTasks,
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    """基于文献库生成综述草稿（异步）。"""
    count = len(
        (
            await session.execute(
                select(Paper.id).where(
                    Paper.project_id == project.id,
                    Paper.status.in_(REVIEW_READY_STATUSES),
                )
            )
        ).all()
    )
    if count == 0:
        raise HTTPException(
            status_code=400,
            detail="no papers available for review; import or upload papers first",
        )

    job = await create_job(session, project.id, "gen_review")
    project_id = project.id
    discipline = project.discipline

    async def work(report):
        await report(0.1)
        async with SessionLocal() as bg_session:
            papers = (
                (
                    await bg_session.execute(
                        select(Paper).where(
                            Paper.project_id == project_id,
                            Paper.status.in_(REVIEW_READY_STATUSES),
                        )
                    )
                )
                .scalars()
                .all()
            )
            briefs = [_to_brief(p) for p in papers]
            await report(0.25)

            try:
                embedder = get_embedder()
                store = get_vector_store()
            except Exception:
                embedder, store = None, None
            try:
                graph = get_citation_graph()
            except Exception:
                graph = None

            draft = await generate_review(
                str(project_id),
                briefs,
                organization=data.organization,
                words_per_section=data.words_per_section,
                discipline=discipline,
                embedder=embedder,
                store=store,
                graph=graph,
            )
            await report(0.85)

            invalid_total = sum(len(s.invalid_citations) for s in draft.sections)
            record = ReviewModel(
                project_id=project_id,
                organization=data.organization,
                sections=[
                    {
                        "title": s.title,
                        "content": s.content,
                        "paper_ids": s.paper_ids,
                        "invalid_citations": s.invalid_citations,
                    }
                    for s in draft.sections
                ],
                references=[
                    {"paper_id": b.paper_id, "title": b.title, "year": b.year}
                    for b in draft.references
                ],
                markdown=draft.to_markdown(),
                bibtex=draft.to_bibtex(),
                word_count=draft.word_count,
                invalid_citation_count=invalid_total,
            )
            bg_session.add(record)
            await bg_session.commit()
            await bg_session.refresh(record)
            review_id = str(record.id)

        await report(1.0)
        return {
            "review_id": review_id,
            "sections": len(draft.sections),
            "references": len(draft.references),
            "word_count": draft.word_count,
            "invalid_citations": invalid_total,
        }

    background.add_task(run_job, job.id, work)
    return job


@router.get("", response_model=list[ReviewOut])
async def list_reviews(
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(ReviewModel)
        .where(ReviewModel.project_id == project.id)
        .order_by(ReviewModel.created_at.desc())
    )
    return (await session.execute(stmt)).scalars().all()


@router.get("/latest", response_model=ReviewOut)
async def latest_review(
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    review = (
        await session.execute(
            select(ReviewModel)
            .where(ReviewModel.project_id == project.id)
            .order_by(ReviewModel.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=404, detail="no review draft yet")
    return review


@router.put("/{review_id}", response_model=ReviewOut)
async def update_review(
    review_id: uuid.UUID,
    data: ReviewUpdate,
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    """保存用户手工编辑后的综述正文。"""
    review = (
        await session.execute(
            select(ReviewModel).where(
                ReviewModel.id == review_id, ReviewModel.project_id == project.id
            )
        )
    ).scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")

    review.markdown = data.markdown
    review.word_count = len(data.markdown)
    await session.commit()
    await session.refresh(review)
    return review
