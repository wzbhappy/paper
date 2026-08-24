"""文献库 API：上传 PDF、列表、详情、重新解析、删除。"""

from __future__ import annotations

import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_project
from app.db import SessionLocal, get_session
from app.models import Paper, Project
from app.rag import delete_paper_vectors
from app.schemas import JobOut, PaperOut, PaperUpdate
from app.services.ingest import ingest_paper, storage_path
from app.services.jobs import create_job, run_job

router = APIRouter()

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


async def _ingest_in_background(paper_id: uuid.UUID, discipline: str | None) -> None:
    """后台执行流水线，使用独立 session。"""
    async with SessionLocal() as session:
        paper = (
            await session.execute(select(Paper).where(Paper.id == paper_id))
        ).scalar_one_or_none()
        if paper is None:
            return
        await ingest_paper(session, paper, discipline=discipline)


@router.post("", response_model=PaperOut, status_code=201)
async def upload_paper(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    """上传 PDF 并触发后台解析。立即返回 pending 状态的记录。"""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="only PDF files are supported")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file too large (max 50MB)")

    path = storage_path(project.id, file.filename or "upload.pdf")
    path.write_bytes(content)

    paper = Paper(
        project_id=project.id,
        source="manual",
        pdf_path=str(path),
        status="pending",
    )
    session.add(paper)
    await session.commit()
    await session.refresh(paper)

    background.add_task(_ingest_in_background, paper.id, project.discipline)
    return paper


@router.get("", response_model=list[PaperOut])
async def list_papers(
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
    status: str | None = Query(default=None),
    q: str | None = Query(default=None, description="标题模糊匹配"),
):
    stmt = select(Paper).where(Paper.project_id == project.id)
    if status:
        stmt = stmt.where(Paper.status == status)
    if q:
        stmt = stmt.where(Paper.title.ilike(f"%{q}%"))
    stmt = stmt.order_by(Paper.created_at.desc())
    return (await session.execute(stmt)).scalars().all()


async def _get_paper(session: AsyncSession, project_id: uuid.UUID, paper_id: uuid.UUID) -> Paper:
    paper = (
        await session.execute(
            select(Paper).where(Paper.id == paper_id, Paper.project_id == project_id)
        )
    ).scalar_one_or_none()
    if paper is None:
        raise HTTPException(status_code=404, detail="paper not found")
    return paper


@router.get("/{paper_id}", response_model=PaperOut)
async def get_paper(
    paper_id: uuid.UUID,
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    return await _get_paper(session, project.id, paper_id)


@router.patch("/{paper_id}", response_model=PaperOut)
async def update_paper(
    paper_id: uuid.UUID,
    data: PaperUpdate,
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    paper = await _get_paper(session, project.id, paper_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(paper, key, value)
    await session.commit()
    await session.refresh(paper)
    return paper


@router.post("/{paper_id}/parse", response_model=JobOut, status_code=202)
async def reparse_paper(
    paper_id: uuid.UUID,
    background: BackgroundTasks,
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    """重新解析已上传的 PDF（解析器升级或首次失败后重试）。"""
    paper = await _get_paper(session, project.id, paper_id)
    if not paper.pdf_path:
        raise HTTPException(status_code=400, detail="paper has no PDF to parse")

    job = await create_job(session, project.id, "parse_pdf")

    async def work(report):
        await report(0.1)
        async with SessionLocal() as bg_session:
            target = (
                await bg_session.execute(select(Paper).where(Paper.id == paper_id))
            ).scalar_one()
            result = await ingest_paper(
                bg_session, target, discipline=project.discipline
            )
        await report(1.0)
        return {
            "paper_id": result.paper_id,
            "status": result.status,
            "chunk_count": result.chunk_count,
            "has_summary": result.has_summary,
            "error": result.error,
        }

    background.add_task(run_job, job.id, work)
    return job


@router.delete("/{paper_id}", status_code=204)
async def delete_paper(
    paper_id: uuid.UUID,
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    paper = await _get_paper(session, project.id, paper_id)
    try:
        await delete_paper_vectors(str(project.id), str(paper.id))
    except Exception:
        # 向量库不可用时仍允许删除数据库记录
        pass
    await session.delete(paper)
    await session.commit()
