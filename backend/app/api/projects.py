"""项目 CRUD。单用户部署，暂不做鉴权。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Project
from app.schemas import ProjectCreate, ProjectOut, ProjectUpdate

router = APIRouter()

VALID_STAGES = {
    "discovery",
    "search",
    "reading",
    "direction",
    "review",
    "outline",
    "writing",
    "review_check",
    "done",
}


@router.get("", response_model=list[ProjectOut])
async def list_projects(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Project).order_by(Project.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(data: ProjectCreate, session: AsyncSession = Depends(get_session)):
    if data.stage not in VALID_STAGES:
        raise HTTPException(status_code=400, detail=f"invalid stage: {data.stage}")
    project = Project(**data.model_dump())
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project


async def _get_or_404(session: AsyncSession, project_id: uuid.UUID) -> Project:
    project = (
        await session.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project_detail(
    project_id: uuid.UUID, session: AsyncSession = Depends(get_session)
):
    return await _get_or_404(session, project_id)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: uuid.UUID,
    data: ProjectUpdate,
    session: AsyncSession = Depends(get_session),
):
    project = await _get_or_404(session, project_id)
    fields = data.model_dump(exclude_unset=True)
    if "stage" in fields and fields["stage"] not in VALID_STAGES:
        raise HTTPException(status_code=400, detail=f"invalid stage: {fields['stage']}")
    for key, value in fields.items():
        setattr(project, key, value)
    await session.commit()
    await session.refresh(project)
    return project


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: uuid.UUID, session: AsyncSession = Depends(get_session)
):
    project = await _get_or_404(session, project_id)
    await session.delete(project)
    await session.commit()
