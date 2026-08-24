"""RAG 索引与检索：把文献片段写入向量库，按查询取回带溯源的上下文。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from app.parser.chunker import Chunk
from app.rag.embedder import Embedder, get_embedder
from app.rag.store import (
    SearchHit,
    VectorRecord,
    VectorStore,
    get_vector_store,
    make_point_id,
)

EMBED_BATCH = 32


@dataclass
class RetrievedChunk:
    """检索结果，带完整溯源信息，供引用标注使用。"""

    text: str
    score: float
    paper_id: str
    paper_title: str | None
    section: str
    chunk_index: int

    def citation_label(self) -> str:
        title = self.paper_title or self.paper_id
        return f"{title}" + (f" §{self.section}" if self.section else "")


async def index_chunks(
    project_id: str,
    paper_id: str,
    chunks: list[Chunk],
    paper_title: str | None = None,
    embedder: Embedder | None = None,
    store: VectorStore | None = None,
) -> int:
    """把片段写入向量库，返回写入条数。重复调用会覆盖同一片段。"""
    if not chunks:
        return 0

    embedder = embedder or get_embedder()
    store = store or get_vector_store()
    await store.ensure_collection(embedder.dim)

    records: list[VectorRecord] = []
    # 分批 embedding，避免单请求过大
    for start in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[start : start + EMBED_BATCH]
        vectors = await embedder.embed([c.text for c in batch])
        for chunk, vector in zip(batch, vectors):
            records.append(
                VectorRecord(
                    id=make_point_id(project_id, paper_id, chunk.index),
                    vector=vector,
                    payload={
                        "project_id": project_id,
                        "paper_id": paper_id,
                        "paper_title": paper_title,
                        "chunk_index": chunk.index,
                        "section": chunk.section,
                        "text": chunk.text,
                    },
                )
            )

    await store.upsert(records)
    logger.info(
        "indexed {} chunks for paper {} in project {}", len(records), paper_id, project_id
    )
    return len(records)


def _to_retrieved(hit: SearchHit) -> RetrievedChunk:
    payload = hit.payload or {}
    return RetrievedChunk(
        text=payload.get("text", ""),
        score=hit.score,
        paper_id=str(payload.get("paper_id", "")),
        paper_title=payload.get("paper_title"),
        section=payload.get("section", "") or "",
        chunk_index=int(payload.get("chunk_index", 0) or 0),
    )


async def retrieve(
    project_id: str,
    query: str,
    limit: int = 8,
    paper_ids: list[str] | None = None,
    max_per_paper: int | None = None,
    embedder: Embedder | None = None,
    store: VectorStore | None = None,
) -> list[RetrievedChunk]:
    """语义检索项目内文献片段。

    max_per_paper 用于保证结果覆盖多篇文献，避免单篇霸榜。
    """
    if not query.strip():
        return []

    embedder = embedder or get_embedder()
    store = store or get_vector_store()

    filters: dict[str, Any] = {"project_id": project_id}
    if paper_ids:
        filters["paper_id"] = list(paper_ids)

    vectors = await embedder.embed([query])
    if not vectors:
        return []

    # 多取一些再做每篇限流，保证限流后仍有足够结果
    fetch = limit * 3 if max_per_paper else limit
    hits = await store.search(vectors[0], limit=fetch, filters=filters)

    results: list[RetrievedChunk] = []
    per_paper: dict[str, int] = {}
    for hit in hits:
        chunk = _to_retrieved(hit)
        if max_per_paper is not None:
            seen = per_paper.get(chunk.paper_id, 0)
            if seen >= max_per_paper:
                continue
            per_paper[chunk.paper_id] = seen + 1
        results.append(chunk)
        if len(results) >= limit:
            break
    return results


def build_context(chunks: list[RetrievedChunk], max_chars: int = 8000) -> str:
    """把检索结果拼成带编号的上下文，编号供 LLM 引用溯源。"""
    parts: list[str] = []
    used = 0
    for i, chunk in enumerate(chunks, start=1):
        block = f"[{i}] {chunk.citation_label()}\n{chunk.text}"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


async def delete_paper_vectors(
    project_id: str, paper_id: str, store: VectorStore | None = None
) -> None:
    """删除某篇文献的全部向量（重新解析或删除文献时调用）。"""
    store = store or get_vector_store()
    await store.delete_by_filter({"project_id": project_id, "paper_id": paper_id})
