"""Semantic Scholar 检索适配器。元数据与引用关系质量最好。"""

from __future__ import annotations

import httpx
from loguru import logger

from app.retriever.base import (
    PaperMeta,
    RateLimiter,
    SearchFilters,
    fetch_with_retry,
)

FIELDS = ",".join(
    [
        "paperId",
        "title",
        "abstract",
        "year",
        "venue",
        "citationCount",
        "externalIds",
        "authors.name",
        "openAccessPdf",
        "url",
    ]
)

REFERENCE_FIELDS = "title,externalIds"


class SemanticScholarRetriever:
    """Semantic Scholar Graph API。

    未鉴权限流约 1 req/s；配置 api_key 后可放宽。
    """

    name = "semantic_scholar"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.semanticscholar.org/graph/v1",
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.limiter = RateLimiter(0.4 if api_key else 1.1)
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key} if self.api_key else {}

    async def search(self, query: str, filters: SearchFilters) -> list[PaperMeta]:
        if not query.strip():
            return []

        params: dict[str, str | int] = {
            "query": query,
            "limit": min(filters.limit, 100),
            "fields": FIELDS,
        }
        if filters.year_from or filters.year_to:
            start = filters.year_from or ""
            end = filters.year_to or ""
            params["year"] = f"{start}-{end}"
        if filters.open_access_only:
            params["openAccessPdf"] = ""

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await fetch_with_retry(
                client, "GET", f"{self.base_url}/paper/search",
                source=self.name, limiter=self.limiter,
                params=params, headers=self._headers(),
            )

        data = resp.json()
        results = [
            meta
            for item in data.get("data", [])
            if (meta := self._parse(item)) is not None
        ]
        logger.info("{}: {} results for {!r}", self.name, len(results), query)
        return results

    async def fetch_references(self, paper_id: str, limit: int = 100) -> list[str]:
        """取某篇文献的参考文献 DOI 列表，用于构建引用图谱。"""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await fetch_with_retry(
                client, "GET", f"{self.base_url}/paper/{paper_id}/references",
                source=self.name, limiter=self.limiter,
                params={"fields": REFERENCE_FIELDS, "limit": limit},
                headers=self._headers(),
            )

        out: list[str] = []
        for item in resp.json().get("data", []):
            cited = item.get("citedPaper") or {}
            doi = (cited.get("externalIds") or {}).get("DOI")
            if doi:
                out.append(doi.lower())
            elif cited.get("title"):
                out.append(cited["title"])
        return out

    def _parse(self, item: dict) -> PaperMeta | None:
        title = (item.get("title") or "").strip()
        if not title:
            return None

        external = item.get("externalIds") or {}
        oa = item.get("openAccessPdf") or {}

        return PaperMeta(
            title=title,
            source=self.name,
            source_id=item.get("paperId"),
            authors=[a["name"] for a in item.get("authors", []) if a.get("name")],
            abstract=item.get("abstract"),
            year=item.get("year"),
            doi=external.get("DOI"),
            arxiv_id=external.get("ArXiv"),
            venue=item.get("venue") or None,
            citation_count=item.get("citationCount"),
            url=item.get("url"),
            pdf_url=oa.get("url"),
        )
