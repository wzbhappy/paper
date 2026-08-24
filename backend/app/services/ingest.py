"""文献入库流水线：PDF → Markdown → 元数据 → 摘要 → 向量库。

设计要点：
- 每一步失败都写回 paper.status / paper.error，前端可见。
- 摘要与入库失败不阻塞解析结果的保存（部分成功优于全丢）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.llm import LLMClient
from app.models import Paper
from app.parser import extract_markdown, extract_metadata, split_markdown
from app.rag import Embedder, VectorStore, delete_paper_vectors, index_chunks
from app.services.summarize import PaperSummary, summarize_paper


@dataclass
class IngestResult:
    paper_id: str
    status: str
    chunk_count: int = 0
    has_summary: bool = False
    error: str | None = None


def storage_path(project_id: uuid.UUID | str, filename: str) -> Path:
    """每个项目一个目录，文件名加 uuid 前缀避免冲突。"""
    root = Path(settings.storage_dir) / str(project_id)
    root.mkdir(parents=True, exist_ok=True)
    safe = Path(filename).name or "upload.pdf"
    return root / f"{uuid.uuid4().hex[:8]}_{safe}"


def build_bibtex(paper: Paper) -> str:
    """生成最简 BibTeX 条目。key 用首作者姓+年份+标题首词。"""
    authors = (paper.authors or "").strip()
    first_author = authors.split(",")[0].strip() if authors else "unknown"
    surname = first_author.split()[-1].lower() if first_author else "unknown"
    year = str(paper.year or "n.d.")
    title = (paper.title or "untitled").strip()
    first_word = "".join(ch for ch in title.split()[0] if ch.isalnum()).lower() if title.split() else "untitled"
    key = f"{surname}{year}{first_word}"

    fields = [f"  title = {{{title}}}"]
    if authors:
        fields.append(f"  author = {{{authors}}}")
    if paper.year:
        fields.append(f"  year = {{{paper.year}}}")
    if paper.doi:
        fields.append(f"  doi = {{{paper.doi}}}")
    return "@article{" + key + ",\n" + ",\n".join(fields) + "\n}"


async def ingest_paper(
    session: AsyncSession,
    paper: Paper,
    llm: LLMClient | None = None,
    embedder: Embedder | None = None,
    store: VectorStore | None = None,
    do_summarize: bool = True,
    do_index: bool = True,
    discipline: str | None = None,
) -> IngestResult:
    """对一篇已落库的 Paper 执行完整解析流水线。"""
    paper.status = "parsing"
    paper.error = None
    await session.commit()

    if not paper.pdf_path:
        paper.status = "failed"
        paper.error = "no pdf_path"
        await session.commit()
        return IngestResult(str(paper.id), "failed", error="no pdf_path")

    # --- 1. PDF → Markdown ---
    try:
        parsed = extract_markdown(paper.pdf_path, prefer_marker=False)
    except Exception as exc:
        logger.error("parse failed for paper {}: {}", paper.id, exc)
        paper.status = "failed"
        paper.error = f"parse failed: {exc}"
        await session.commit()
        return IngestResult(str(paper.id), "failed", error=str(exc))

    paper.parsed_md = parsed.markdown

    # --- 2. 元数据（仅补空字段，不覆盖用户已填内容）---
    meta = extract_metadata(parsed.markdown, parsed.metadata)
    paper.title = paper.title or meta.title
    paper.authors = paper.authors or meta.authors
    paper.abstract = paper.abstract or meta.abstract
    paper.doi = paper.doi or meta.doi
    paper.year = paper.year or meta.year
    if meta.arxiv_id and not paper.source_id:
        paper.source_id = meta.arxiv_id
    paper.bibtex = build_bibtex(paper)
    await session.commit()

    result = IngestResult(str(paper.id), "ready")

    # --- 3. 结构化摘要（失败不致命）---
    if do_summarize:
        try:
            summary: PaperSummary = await summarize_paper(
                parsed.markdown,
                title=paper.title,
                discipline=discipline,
                client=llm,
            )
            paper.summary = summary.model_dump()
            result.has_summary = True
        except Exception as exc:
            logger.warning("summarize failed for paper {}: {}", paper.id, exc)
            paper.error = f"summarize failed: {exc}"

    # --- 4. 切块 + 向量入库（失败不致命）---
    if do_index:
        try:
            chunks = split_markdown(parsed.markdown)
            # 重新解析时先清旧向量，避免残留
            await delete_paper_vectors(
                str(paper.project_id), str(paper.id), store=store
            )
            count = await index_chunks(
                str(paper.project_id),
                str(paper.id),
                chunks,
                paper_title=paper.title,
                embedder=embedder,
                store=store,
            )
            paper.chunk_count = count
            result.chunk_count = count
        except Exception as exc:
            logger.warning("indexing failed for paper {}: {}", paper.id, exc)
            paper.error = f"indexing failed: {exc}"

    paper.status = "ready"
    await session.commit()
    logger.info(
        "ingested paper {} (chunks={}, summary={})",
        paper.id,
        result.chunk_count,
        result.has_summary,
    )
    return result
