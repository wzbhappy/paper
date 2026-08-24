"""文献检索与导入 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_project
from app.db import get_session
from app.graph import get_citation_graph
from app.models import Paper, Project
from app.retriever import PaperMeta, SearchFilters, find_duplicate
from app.schemas import (
    ImportRequest,
    ImportResponse,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
)
from app.services.importer import import_papers, paper_to_meta
from app.services.search import search_papers
from sqlalchemy import select

router = APIRouter()


def _to_item(meta: PaperMeta, in_library: bool) -> SearchResultItem:
    return SearchResultItem(
        title=meta.title,
        source=meta.source,
        source_id=meta.source_id,
        authors=meta.authors,
        abstract=meta.abstract,
        year=meta.year,
        doi=meta.doi,
        arxiv_id=meta.arxiv_id,
        venue=meta.venue,
        citation_count=meta.citation_count,
        url=meta.url,
        pdf_url=meta.pdf_url,
        references=meta.references,
        already_in_library=in_library,
    )


def _to_meta(item: SearchResultItem) -> PaperMeta:
    return PaperMeta(
        title=item.title,
        source=item.source,
        source_id=item.source_id,
        authors=item.authors,
        abstract=item.abstract,
        year=item.year,
        doi=item.doi,
        arxiv_id=item.arxiv_id,
        venue=item.venue,
        citation_count=item.citation_count,
        url=item.url,
        pdf_url=item.pdf_url,
        references=item.references,
    )


@router.post("", response_model=SearchResponse)
async def search(
    data: SearchRequest,
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    """跨源检索。同步返回（各源限流后通常几秒内完成）。

    结果标注 already_in_library，避免用户重复导入。
    """
    results, expanded = await search_papers(
        data.query,
        sources=data.sources,
        filters=SearchFilters(
            limit=data.limit, year_from=data.year_from, year_to=data.year_to
        ),
        expand=data.expand,
        discipline=project.discipline,
    )

    existing = (
        (await session.execute(select(Paper).where(Paper.project_id == project.id)))
        .scalars()
        .all()
    )
    existing_metas = [paper_to_meta(p) for p in existing]

    items = [
        _to_item(meta, find_duplicate(meta, existing_metas) is not None)
        for meta in results
    ]
    return SearchResponse(
        query=data.query,
        expanded_queries=expanded.queries,
        keywords=expanded.keywords,
        results=items,
    )


@router.post("/import", response_model=ImportResponse, status_code=201)
async def import_results(
    data: ImportRequest,
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    """把选中的检索结果导入文献库，并同步引用图谱。"""
    try:
        graph = get_citation_graph()
    except Exception:
        graph = None

    created, skipped = await import_papers(
        session, project.id, [_to_meta(i) for i in data.items], graph=graph
    )
    return ImportResponse(
        imported=len(created),
        skipped=skipped,
        paper_ids=[str(p.id) for p in created],
    )
