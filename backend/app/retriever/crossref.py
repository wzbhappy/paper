"""Crossref 检索适配器。DOI 权威源，适合补全元数据。"""

from __future__ import annotations

import httpx
from loguru import logger

from app.retriever.base import (
    PaperMeta,
    RateLimiter,
    SearchFilters,
    fetch_with_retry,
)


class CrossrefRetriever:
    """Crossref REST API。

    带联系邮箱（mailto）会进入 polite pool，限流更宽松。
    """

    name = "crossref"

    def __init__(
        self,
        mailto: str | None = None,
        base_url: str = "https://api.crossref.org",
        timeout: float = 30.0,
    ) -> None:
        self.mailto = mailto
        self.base_url = base_url.rstrip("/")
        self.limiter = RateLimiter(0.5)
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        ua = "paper-assistant/0.1"
        if self.mailto:
            ua += f" (mailto:{self.mailto})"
        return {"User-Agent": ua}

    async def search(self, query: str, filters: SearchFilters) -> list[PaperMeta]:
        if not query.strip():
            return []

        params: dict[str, str | int] = {
            "query.bibliographic": query,
            "rows": min(filters.limit, 100),
            "select": "DOI,title,author,abstract,issued,container-title,is-referenced-by-count,URL,reference",
        }
        constraints = []
        if filters.year_from:
            constraints.append(f"from-pub-date:{filters.year_from}-01-01")
        if filters.year_to:
            constraints.append(f"until-pub-date:{filters.year_to}-12-31")
        if constraints:
            params["filter"] = ",".join(constraints)
        if self.mailto:
            params["mailto"] = self.mailto

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await fetch_with_retry(
                client, "GET", f"{self.base_url}/works",
                source=self.name, limiter=self.limiter,
                params=params, headers=self._headers(),
            )

        items = (resp.json().get("message") or {}).get("items", [])
        results = [meta for item in items if (meta := self._parse(item)) is not None]
        logger.info("{}: {} results for {!r}", self.name, len(results), query)
        return results

    async def fetch_by_doi(self, doi: str) -> PaperMeta | None:
        """按 DOI 取权威元数据，用于补全其他源缺失的字段。"""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await fetch_with_retry(
                    client, "GET", f"{self.base_url}/works/{doi}",
                    source=self.name, limiter=self.limiter,
                    headers=self._headers(), retries=1,
                )
            except Exception as exc:
                logger.warning("{}: fetch_by_doi({}) failed: {}", self.name, doi, exc)
                return None
        return self._parse((resp.json().get("message") or {}))

    def _parse(self, item: dict) -> PaperMeta | None:
        titles = item.get("title") or []
        title = (titles[0] if titles else "").strip()
        if not title:
            return None

        authors: list[str] = []
        for author in item.get("author", []) or []:
            given = (author.get("given") or "").strip()
            family = (author.get("family") or "").strip()
            full = f"{given} {family}".strip()
            if full:
                authors.append(full)

        year = None
        issued = (item.get("issued") or {}).get("date-parts") or []
        if issued and issued[0] and isinstance(issued[0][0], int):
            year = issued[0][0]

        containers = item.get("container-title") or []

        # Crossref abstract 常带 JATS 标签，粗略清理
        abstract = item.get("abstract")
        if abstract:
            import re

            abstract = re.sub(r"<[^>]+>", " ", abstract)
            abstract = re.sub(r"\s+", " ", abstract).strip() or None

        references: list[str] = []
        for ref in item.get("reference", []) or []:
            if ref.get("DOI"):
                references.append(ref["DOI"].lower())
            elif ref.get("article-title"):
                references.append(ref["article-title"])

        return PaperMeta(
            title=title,
            source=self.name,
            source_id=item.get("DOI"),
            authors=authors,
            abstract=abstract,
            year=year,
            doi=item.get("DOI"),
            venue=containers[0] if containers else None,
            citation_count=item.get("is-referenced-by-count"),
            url=item.get("URL"),
            references=references,
        )
