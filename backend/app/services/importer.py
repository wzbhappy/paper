"""文献导入服务：把检索结果落库，并把引用关系写入图谱。"""

from __future__ import annotations

import uuid

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph import CitationGraph, GraphPaper
from app.models import Paper
from app.retriever import PaperMeta, find_duplicate


def paper_to_meta(paper: Paper) -> PaperMeta:
    """数据库记录转检索元数据，用于查重比对。"""
    return PaperMeta(
        title=paper.title or "",
        source=paper.source,
        source_id=paper.source_id,
        authors=[a.strip() for a in (paper.authors or "").split(",") if a.strip()],
        abstract=paper.abstract,
        year=paper.year,
        doi=paper.doi,
        arxiv_id=paper.arxiv_id,
        references=list(paper.references or []),
    )


def graph_key(paper: Paper) -> str:
    """图谱节点 key：DOI 优先（跨源一致），回退 paper_id。"""
    if paper.doi:
        return paper.doi.strip().lower()
    return str(paper.id)


async def sync_graph_for_paper(
    project_id: uuid.UUID | str,
    paper: Paper,
    graph: CitationGraph,
) -> int:
    """把一篇文献及其引用关系写入图谱，返回新增边数。"""
    key = graph_key(paper)
    await graph.add_paper(
        str(project_id),
        GraphPaper(key=key, paper_id=str(paper.id), title=paper.title, year=paper.year),
    )
    targets = [r.strip().lower() for r in (paper.references or []) if r and r.strip()]
    if not targets:
        return 0
    return await graph.add_citations(str(project_id), key, targets)


async def import_papers(
    session: AsyncSession,
    project_id: uuid.UUID,
    metas: list[PaperMeta],
    graph: CitationGraph | None = None,
) -> tuple[list[Paper], int]:
    """批量导入文献，跳过已存在的。返回 (新增记录, 跳过数)。"""
    existing = (
        (await session.execute(select(Paper).where(Paper.project_id == project_id)))
        .scalars()
        .all()
    )
    existing_metas = [paper_to_meta(p) for p in existing]

    created: list[Paper] = []
    skipped = 0

    for meta in metas:
        if find_duplicate(meta, existing_metas) is not None:
            skipped += 1
            continue

        paper = Paper(
            project_id=project_id,
            source=meta.source,
            source_id=meta.source_id,
            title=meta.title,
            authors=meta.authors_str or None,
            abstract=meta.abstract,
            year=meta.year,
            doi=meta.normalized_doi(),
            arxiv_id=meta.arxiv_id,
            venue=meta.venue,
            citation_count=meta.citation_count,
            url=meta.url,
            pdf_url=meta.pdf_url,
            references=meta.references or None,
            # 没有本地 PDF，元数据即可用；有 PDF 时后续解析会升级状态
            status="metadata_only",
        )
        session.add(paper)
        created.append(paper)
        # 同批次内也要查重
        existing_metas.append(meta)

    await session.commit()
    for paper in created:
        await session.refresh(paper)

    if graph is not None:
        for paper in created:
            try:
                await sync_graph_for_paper(project_id, paper, graph)
            except Exception as exc:
                logger.warning("graph sync failed for paper {}: {}", paper.id, exc)

    logger.info(
        "import: {} created, {} skipped in project {}", len(created), skipped, project_id
    )
    return created, skipped
