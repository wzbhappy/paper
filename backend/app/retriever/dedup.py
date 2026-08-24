"""跨源去重：DOI 优先，无 DOI 时用标题相似度 + 年份联合判定，并合并字段。"""

from __future__ import annotations

from loguru import logger

from app.retriever.base import PaperMeta, normalize_title, title_similarity

TITLE_THRESHOLD = 0.85

# 元数据质量排序：Crossref 的 DOI/年份最权威，S2 的引用数与摘要好，arXiv 兜底
SOURCE_PRIORITY = {"crossref": 0, "semantic_scholar": 1, "arxiv": 2, "manual": 3}


def _priority(meta: PaperMeta) -> int:
    return SOURCE_PRIORITY.get(meta.source, 99)


def merge_meta(primary: PaperMeta, other: PaperMeta) -> PaperMeta:
    """把 other 的非空字段补进 primary（不覆盖已有值）。"""
    for attr in (
        "abstract",
        "year",
        "doi",
        "arxiv_id",
        "venue",
        "url",
        "pdf_url",
        "source_id",
    ):
        if getattr(primary, attr, None) in (None, "") and getattr(other, attr, None):
            setattr(primary, attr, getattr(other, attr))

    if not primary.authors and other.authors:
        primary.authors = list(other.authors)

    # 引用数取最大值（不同源统计口径不同，取大者更接近真实）
    if other.citation_count is not None:
        if primary.citation_count is None or other.citation_count > primary.citation_count:
            primary.citation_count = other.citation_count

    # 引用列表取并集
    if other.references:
        known = set(primary.references)
        primary.references.extend(r for r in other.references if r not in known)

    return primary


def deduplicate(papers: list[PaperMeta]) -> list[PaperMeta]:
    """跨源去重。

    两阶段：先按归一化 DOI 精确合并，再对无 DOI 的按标题相似度合并。
    同一组内保留元数据质量最高的源作为主记录。
    """
    if not papers:
        return []

    # 质量高的源先处理，成为主记录
    ordered = sorted(papers, key=_priority)

    by_doi: dict[str, PaperMeta] = {}
    no_doi: list[PaperMeta] = []
    merged_count = 0

    for paper in ordered:
        doi = paper.normalized_doi()
        if doi:
            if doi in by_doi:
                merge_meta(by_doi[doi], paper)
                merged_count += 1
            else:
                by_doi[doi] = paper
        else:
            no_doi.append(paper)

    results = list(by_doi.values())

    # 无 DOI 的：先尝试匹配已有记录（可能是同一篇的预印本），再互相比对
    for paper in no_doi:
        matched = None
        for existing in results:
            if title_similarity(paper.title, existing.title) < TITLE_THRESHOLD:
                continue
            # 年份都存在且相差超过 1 年，视为不同文献（避免同名不同版本误合）
            if (
                paper.year
                and existing.year
                and abs(paper.year - existing.year) > 1
            ):
                continue
            matched = existing
            break

        if matched is not None:
            merge_meta(matched, paper)
            merged_count += 1
        else:
            results.append(paper)

    if merged_count:
        logger.info("deduplicate: {} -> {} ({} merged)", len(papers), len(results), merged_count)
    return results


def find_duplicate(
    candidate: PaperMeta, existing: list[PaperMeta]
) -> PaperMeta | None:
    """在已有列表中找与 candidate 重复的记录，用于入库前查重。"""
    doi = candidate.normalized_doi()
    if doi:
        for item in existing:
            if item.normalized_doi() == doi:
                return item

    norm = normalize_title(candidate.title)
    if not norm:
        return None
    for item in existing:
        if title_similarity(candidate.title, item.title) >= TITLE_THRESHOLD:
            if candidate.year and item.year and abs(candidate.year - item.year) > 1:
                continue
            return item
    return None
