"""研究方向 API：生成、列表、采纳/反馈。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_project
from app.db import SessionLocal, get_session
from app.models import Paper, Project
from app.models import ResearchDirection as DirectionModel
from app.rag import get_embedder, get_vector_store
from app.schemas import DirectionGenerateRequest, DirectionOut, DirectionUpdate, JobOut
from app.services.direction import PaperBrief, generate_directions
from app.services.jobs import create_job, run_job
from app.services.summarize import PaperSummary

router = APIRouter()


def _to_brief(paper: Paper) -> PaperBrief:
    summary = None
    if paper.summary:
        try:
            summary = PaperSummary.model_validate(paper.summary)
        except Exception:
            summary = None
    return PaperBrief(
        paper_id=str(paper.id),
        title=paper.title,
        year=paper.year,
        summary=summary,
    )


@router.post("/generate", response_model=JobOut, status_code=202)
async def generate(
    data: DirectionGenerateRequest,
    background: BackgroundTasks,
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    """基于项目内已解析文献生成方向建议（异步）。"""
    ready_count = len(
        (
            await session.execute(
                select(Paper.id).where(
                    Paper.project_id == project.id, Paper.status == "ready"
                )
            )
        ).all()
    )
    if ready_count == 0:
        raise HTTPException(
            status_code=400,
            detail="no parsed papers in this project; upload and parse PDFs first",
        )

    job = await create_job(session, project.id, "gen_direction")
    project_id = project.id
    discipline = project.discipline

    async def work(report):
        await report(0.1)
        async with SessionLocal() as bg_session:
            papers = (
                (
                    await bg_session.execute(
                        select(Paper).where(
                            Paper.project_id == project_id, Paper.status == "ready"
                        )
                    )
                )
                .scalars()
                .all()
            )
            briefs = [_to_brief(p) for p in papers]
            await report(0.3)

            # embedder/store 不可用时降级为纯摘要模式
            try:
                embedder = get_embedder()
                store = get_vector_store()
            except Exception:
                embedder, store = None, None

            directions = await generate_directions(
                str(project_id),
                briefs,
                n=data.n,
                intent=data.intent,
                discipline=discipline,
                embedder=embedder,
                store=store,
            )
            await report(0.8)

            if data.replace:
                await bg_session.execute(
                    delete(DirectionModel).where(
                        DirectionModel.project_id == project_id
                    )
                )

            for d in directions:
                bg_session.add(
                    DirectionModel(
                        project_id=project_id,
                        statement=d.statement,
                        gap=d.gap,
                        innovation=d.innovation,
                        method_sketch=d.method_sketch,
                        feasibility=d.feasibility,
                        novelty=d.novelty,
                        evidence_paper_ids=d.evidence_paper_ids,
                        evidence_titles=d.evidence_titles,
                    )
                )
            await bg_session.commit()

        await report(1.0)
        return {"count": len(directions), "papers_used": len(briefs)}

    background.add_task(run_job, job.id, work)
    return job


@router.get("", response_model=list[DirectionOut])
async def list_directions(
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(DirectionModel)
        .where(DirectionModel.project_id == project.id)
        .order_by(DirectionModel.created_at.desc())
    )
    return (await session.execute(stmt)).scalars().all()


@router.patch("/{direction_id}", response_model=DirectionOut)
async def update_direction(
    direction_id: uuid.UUID,
    data: DirectionUpdate,
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    """采纳方向或记录反馈。采纳时自动取消其他方向的选中状态。"""
    direction = (
        await session.execute(
            select(DirectionModel).where(
                DirectionModel.id == direction_id,
                DirectionModel.project_id == project.id,
            )
        )
    ).scalar_one_or_none()
    if direction is None:
        raise HTTPException(status_code=404, detail="direction not found")

    fields = data.model_dump(exclude_unset=True)
    if fields.get("selected"):
        others = (
            (
                await session.execute(
                    select(DirectionModel).where(
                        DirectionModel.project_id == project.id,
                        DirectionModel.id != direction_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        for other in others:
            other.selected = False

    for key, value in fields.items():
        setattr(direction, key, value)
    await session.commit()
    await session.refresh(direction)
    return direction
