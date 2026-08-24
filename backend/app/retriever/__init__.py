"""多源文献检索：适配器、去重、关键词扩展。"""

from app.retriever.arxiv import ArxivRetriever
from app.retriever.base import (
    PaperMeta,
    RateLimiter,
    Retriever,
    RetrieverError,
    SearchFilters,
    fetch_with_retry,
    normalize_title,
    title_similarity,
)
from app.retriever.crossref import CrossrefRetriever
from app.retriever.dedup import deduplicate, find_duplicate, merge_meta
from app.retriever.semantic_scholar import SemanticScholarRetriever

__all__ = [
    "ArxivRetriever",
    "CrossrefRetriever",
    "PaperMeta",
    "RateLimiter",
    "Retriever",
    "RetrieverError",
    "SearchFilters",
    "SemanticScholarRetriever",
    "deduplicate",
    "fetch_with_retry",
    "find_duplicate",
    "merge_meta",
    "normalize_title",
    "title_similarity",
]
