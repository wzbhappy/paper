"""API 依赖：项目存在性校验等共用逻辑。"""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Project


async def get_project(
    project_id: uuid.UUID = Path(...),
    session: AsyncSession = Depends(get_session),
) -> Project:
    project = (
        await session.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project
