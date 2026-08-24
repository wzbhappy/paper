"""arXiv 检索适配器。使用 Atom API，无需鉴权。"""

from __future__ import annotations

import re
from xml.etree import ElementTree

import httpx
from loguru import logger

from app.retriever.base import (
    PaperMeta,
    RateLimiter,
    RetrieverError,
    SearchFilters,
    fetch_with_retry,
)

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")


class ArxivRetriever:
    """arXiv Atom API。官方建议请求间隔 >= 3s，这里设 3s。"""

    name = "arxiv"

    def __init__(
        self,
        base_url: str = "http://export.arxiv.org/api/query",
        min_interval: float = 3.0,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url
        self.limiter = RateLimiter(min_interval)
        self.timeout = timeout

    async def search(self, query: str, filters: SearchFilters) -> list[PaperMeta]:
        if not query.strip():
            return []

        params = {
            "search_query": f"all:{query}",
            "start": 0,
            # 年份过滤只能在客户端做，先多取一些
            "max_results": min(filters.limit * 2 if filters.year_from else filters.limit, 100),
            "sortBy": "relevance",
            "sortOrder": "descending",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await fetch_with_retry(
                client, "GET", self.base_url, source=self.name,
                limiter=self.limiter, params=params,
            )

        try:
            root = ElementTree.fromstring(resp.text)
        except ElementTree.ParseError as exc:
            raise RetrieverError(f"{self.name}: malformed Atom response: {exc}") from exc

        results: list[PaperMeta] = []
        for entry in root.findall("atom:entry", ATOM_NS):
            meta = self._parse_entry(entry)
            if meta is None:
                continue
            if filters.year_from and (meta.year or 0) < filters.year_from:
                continue
            if filters.year_to and (meta.year or 9999) > filters.year_to:
                continue
            results.append(meta)
            if len(results) >= filters.limit:
                break

        logger.info("{}: {} results for {!r}", self.name, len(results), query)
        return results

    def _parse_entry(self, entry) -> PaperMeta | None:
        def text(path: str) -> str | None:
            node = entry.find(path, ATOM_NS)
            return node.text.strip() if node is not None and node.text else None

        title = text("atom:title")
        if not title:
            return None
        title = re.sub(r"\s+", " ", title)

        entry_id = text("atom:id") or ""
        arxiv_match = ARXIV_ID_RE.search(entry_id)
        arxiv_id = arxiv_match.group(1) if arxiv_match else None

        published = text("atom:published") or ""
        year = int(published[:4]) if published[:4].isdigit() else None

        authors = [
            node.text.strip()
            for node in entry.findall("atom:author/atom:name", ATOM_NS)
            if node is not None and node.text
        ]

        abstract = text("atom:summary")
        if abstract:
            abstract = re.sub(r"\s+", " ", abstract)

        pdf_url = None
        for link in entry.findall("atom:link", ATOM_NS):
            if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                pdf_url = link.get("href")
                break

        return PaperMeta(
            title=title,
            source=self.name,
            source_id=arxiv_id,
            authors=authors,
            abstract=abstract,
            year=year,
            doi=text("arxiv:doi"),
            arxiv_id=arxiv_id,
            venue=text("arxiv:journal_ref"),
            url=entry_id or None,
            pdf_url=pdf_url,
        )
