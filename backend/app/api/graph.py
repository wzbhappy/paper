"""引用图谱 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_project
from app.db import get_session
from app.graph import get_citation_graph
from app.models import Paper, Project
from app.schemas import GraphStatsOut
from app.services.importer import graph_key

router = APIRouter()


@router.get("/stats", response_model=GraphStatsOut)
async def graph_stats(
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    """引用图谱概况。图谱不可用时返回 available=false 而非报错。"""
    try:
        graph = get_citation_graph()
        stats = await graph.stats(str(project.id))
        ranked = await graph.most_cited(str(project.id), limit=10)
    except Exception as exc:
        return GraphStatsOut(available=False, error=str(exc)[:300])

    # 把图谱 key 映射回库内文献标题
    papers = (
        (await session.execute(select(Paper).where(Paper.project_id == project.id)))
        .scalars()
        .all()
    )
    title_by_key = {graph_key(p): p.title for p in papers}

    return GraphStatsOut(
        node_count=stats.node_count,
        edge_count=stats.edge_count,
        most_cited=[
            {"key": key, "citations": count, "title": title_by_key.get(key)}
            for key, count in ranked
        ],
    )
