"""任务状态查询。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_project
from app.db import get_session
from app.models import Job, Project
from app.schemas import JobOut

router = APIRouter()


@router.get("", response_model=list[JobOut])
async def list_jobs(
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(Job).where(Job.project_id == project.id).order_by(Job.created_at.desc())
    )
    return (await session.execute(stmt)).scalars().all()


@router.get("/{job_id}", response_model=JobOut)
async def get_job(
    job_id: uuid.UUID,
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    job = (
        await session.execute(
            select(Job).where(Job.id == job_id, Job.project_id == project.id)
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job
