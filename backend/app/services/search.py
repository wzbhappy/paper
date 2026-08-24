"""检索服务编排：关键词扩展 → 并行多源检索 → 去重 → 相关性排序。"""

from __future__ import annotations

import asyncio

from loguru import logger
from pydantic import BaseModel, Field

from app.config import settings
from app.llm import LLMClient, LLMRequest, Message, Role, TaskType, get_llm
from app.llm.prompts import render
from app.retriever import (
    ArxivRetriever,
    CrossrefRetriever,
    PaperMeta,
    Retriever,
    SearchFilters,
    SemanticScholarRetriever,
    deduplicate,
    normalize_title,
)


class ExpandedQuery(BaseModel):
    queries: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


def build_retrievers(sources: list[str] | None = None) -> list[Retriever]:
    """按名称构建检索器。未指定时用全部可用源。"""
    available: dict[str, Retriever] = {
        "arxiv": ArxivRetriever(),
        "semantic_scholar": SemanticScholarRetriever(
            api_key=settings.semantic_scholar_api_key
        ),
        "crossref": CrossrefRetriever(mailto=settings.crossref_mailto),
    }
    if not sources:
        return list(available.values())
    return [available[s] for s in sources if s in available]


async def expand_query(
    intent: str,
    n: int = 3,
    discipline: str | None = None,
    client: LLMClient | None = None,
) -> ExpandedQuery:
    """用 LLM 把检索意图扩展成多个互补查询。失败时回退为原始查询。"""
    if not intent.strip():
        return ExpandedQuery()

    prompt = render(
        "keyword_expand", discipline=discipline, intent=intent, n=n
    )
    llm = client or get_llm()
    req = LLMRequest(
        messages=[Message(role=Role.USER, content=prompt)],
        task=TaskType.KEYWORD_EXPAND,
        temperature=0.4,
    )
    try:
        result = await llm.complete_json(req, ExpandedQuery, retries=1)
    except Exception as exc:
        logger.warning("query expansion failed, using raw query: {}", exc)
        return ExpandedQuery(queries=[intent], keywords=[])

    if not result.queries:
        result.queries = [intent]
    return result


def score_relevance(paper: PaperMeta, keywords: list[str]) -> float:
    """关键词覆盖度 + 引用数加权的启发式相关性分。

    纯启发式，仅用于排序展示；真正的语义筛选在入库后由 RAG 承担。
    """
    if not keywords:
        base = 0.5
    else:
        haystack = normalize_title(
            f"{paper.title} {paper.abstract or ''} {paper.venue or ''}"
        )
        hits = sum(1 for kw in keywords if normalize_title(kw) in haystack)
        base = hits / len(keywords)

    # 引用数做温和加成，避免完全压制新论文
    citations = paper.citation_count or 0
    citation_bonus = min(0.3, citations / 1000)
    # 有摘要的记录信息更完整，略微加分
    completeness = 0.1 if paper.abstract else 0.0
    return min(1.0, base * 0.7 + citation_bonus + completeness)


async def search_papers(
    intent: str,
    sources: list[str] | None = None,
    filters: SearchFilters | None = None,
    expand: bool = True,
    discipline: str | None = None,
    client: LLMClient | None = None,
    retrievers: list[Retriever] | None = None,
) -> tuple[list[PaperMeta], ExpandedQuery]:
    """跨源检索并返回去重排序后的结果。

    单个源失败不影响其他源（并行 + 异常隔离）。
    """
    filters = filters or SearchFilters()

    expanded = (
        await expand_query(intent, discipline=discipline, client=client)
        if expand
        else ExpandedQuery(queries=[intent], keywords=[])
    )
    queries = expanded.queries or [intent]
    active = retrievers if retrievers is not None else build_retrievers(sources)
    if not active:
        return [], expanded

    async def run(retriever: Retriever, query: str) -> list[PaperMeta]:
        try:
            return await retriever.search(query, filters)
        except Exception as exc:
            logger.warning("{} failed for {!r}: {}", retriever.name, query, exc)
            return []

    tasks = [run(r, q) for r in active for q in queries]
    batches = await asyncio.gather(*tasks)

    combined: list[PaperMeta] = [p for batch in batches for p in batch]
    unique = deduplicate(combined)
    unique.sort(key=lambda p: score_relevance(p, expanded.keywords), reverse=True)

    logger.info(
        "search {!r}: {} raw -> {} unique across {} sources x {} queries",
        intent,
        len(combined),
        len(unique),
        len(active),
        len(queries),
    )
    return unique[: filters.limit], expanded
