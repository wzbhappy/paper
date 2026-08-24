"""多源检索基础层：统一的文献元数据模型、检索协议、带重试与限流的 HTTP 客户端。"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx
from loguru import logger


class RetrieverError(RuntimeError):
    """检索源调用失败。"""


@dataclass
class PaperMeta:
    """跨源统一的文献元数据。source_id 为该源内的唯一标识。"""

    title: str
    source: str
    source_id: str | None = None
    authors: list[str] = field(default_factory=list)
    abstract: str | None = None
    year: int | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    venue: str | None = None
    citation_count: int | None = None
    url: str | None = None
    pdf_url: str | None = None
    references: list[str] = field(default_factory=list)
    """被引文献的 DOI 或标题，用于构建引用图谱。"""

    @property
    def authors_str(self) -> str:
        return ", ".join(self.authors)

    def normalized_doi(self) -> str | None:
        if not self.doi:
            return None
        doi = self.doi.strip().lower()
        # 去掉常见前缀
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if doi.startswith(prefix):
                doi = doi[len(prefix) :]
        return doi.strip().rstrip(".,;") or None


@dataclass
class SearchFilters:
    year_from: int | None = None
    year_to: int | None = None
    limit: int = 20
    open_access_only: bool = False


@runtime_checkable
class Retriever(Protocol):
    """所有检索源实现该协议。"""

    name: str

    async def search(self, query: str, filters: SearchFilters) -> list[PaperMeta]: ...


class RateLimiter:
    """简单令牌间隔限流：保证相邻请求间隔不小于 min_interval 秒。

    各家 API 限流严格（Semantic Scholar 未鉴权约 1 req/s），必须节流。
    """

    def __init__(self, min_interval: float) -> None:
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self.min_interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


async def fetch_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    source: str,
    limiter: RateLimiter | None = None,
    retries: int = 3,
    backoff: float = 1.0,
    **kwargs: Any,
) -> httpx.Response:
    """带指数退避的请求。429/5xx 重试，4xx（非 429）直接失败。"""
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        if limiter:
            await limiter.acquire()
        try:
            resp = await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            last_error = exc
            logger.warning(
                "{}: request error (attempt {}/{}): {}",
                source,
                attempt + 1,
                retries + 1,
                exc,
            )
        else:
            if resp.status_code < 400:
                return resp
            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = RetrieverError(
                    f"{source}: HTTP {resp.status_code}: {resp.text[:200]}"
                )
                logger.warning(
                    "{}: retryable HTTP {} (attempt {}/{})",
                    source,
                    resp.status_code,
                    attempt + 1,
                    retries + 1,
                )
            else:
                raise RetrieverError(
                    f"{source}: HTTP {resp.status_code}: {resp.text[:200]}"
                )

        if attempt < retries:
            # 指数退避，缓解限流
            await asyncio.sleep(backoff * (2**attempt))

    raise RetrieverError(f"{source}: failed after {retries + 1} attempts: {last_error}")


_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s\u4e00-\u9fff]")


def normalize_title(title: str) -> str:
    """标题归一化：小写、去标点、压空白。用于无 DOI 时的去重比对。"""
    text = _PUNCT.sub(" ", (title or "").lower())
    return _WS.sub(" ", text).strip()


def title_similarity(a: str, b: str) -> float:
    """标题相似度：归一化后的 token Jaccard。"""
    ta = set(normalize_title(a).split())
    tb = set(normalize_title(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
