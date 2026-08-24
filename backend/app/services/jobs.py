"""Job 辅助：创建、更新进度、包装后台任务的异常处理。"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Job


async def create_job(session, project_id: uuid.UUID, job_type: str) -> Job:
    job = Job(project_id=project_id, type=job_type, status="queued", progress=0.0)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def _update(job_id: uuid.UUID, **fields: Any) -> None:
    """后台任务用独立 session 更新 job，避免与请求 session 冲突。"""
    async with SessionLocal() as session:
        job = (
            await session.execute(select(Job).where(Job.id == job_id))
        ).scalar_one_or_none()
        if job is None:
            return
        for key, value in fields.items():
            setattr(job, key, value)
        await session.commit()


async def run_job(
    job_id: uuid.UUID,
    work: Callable[[Callable[[float], Awaitable[None]]], Awaitable[dict[str, Any]]],
) -> None:
    """执行后台任务并维护 job 状态。

    work 接收一个 report_progress 回调，返回值写入 job.result。
    """

    async def report(progress: float) -> None:
        await _update(job_id, progress=max(0.0, min(1.0, progress)))

    await _update(job_id, status="running", progress=0.0)
    try:
        result = await work(report)
    except Exception as exc:
        logger.exception("job {} failed", job_id)
        await _update(job_id, status="failed", error=str(exc)[:2000])
        return
    await _update(job_id, status="done", progress=1.0, result=result)
