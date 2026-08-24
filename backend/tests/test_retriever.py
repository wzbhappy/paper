"""检索层测试：HTTP 用 httpx MockTransport 拦截，不打真实网络。"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_smoke.db")

import httpx
import pytest

from app.retriever import (
    ArxivRetriever,
    CrossrefRetriever,
    PaperMeta,
    RetrieverError,
    SearchFilters,
    SemanticScholarRetriever,
    deduplicate,
    find_duplicate,
    merge_meta,
    normalize_title,
    title_similarity,
)
from app.retriever.base import RateLimiter, fetch_with_retry

# ---------- 基础工具 ----------


def test_normalize_title_strips_punctuation_and_case():
    assert normalize_title("Deep Learning: A Survey!") == "deep learning a survey"
    assert normalize_title("  Multiple   Spaces  ") == "multiple spaces"


def test_title_similarity():
    assert title_similarity("graph neural networks", "Graph Neural Networks!") == 1.0
    assert title_similarity("graph neural network survey", "graph neural network review") > 0.5
    assert title_similarity("deep learning", "quantum computing") == 0.0
    assert title_similarity("", "anything") == 0.0


def test_normalized_doi_strips_prefixes():
    assert PaperMeta(title="t", source="s", doi="https://doi.org/10.1/ABC").normalized_doi() == "10.1/abc"
    assert PaperMeta(title="t", source="s", doi="doi:10.2/x").normalized_doi() == "10.2/x"
    assert PaperMeta(title="t", source="s", doi=None).normalized_doi() is None


@pytest.mark.asyncio
async def test_rate_limiter_enforces_interval():
    import time

    interval = 0.2
    limiter = RateLimiter(interval)
    start = time.monotonic()
    await limiter.acquire()
    await limiter.acquire()
    elapsed = time.monotonic() - start
    # Windows 计时器分辨率约 15ms，留出容差避免偶发失败
    assert elapsed >= interval - 0.02


@pytest.mark.asyncio
async def test_rate_limiter_zero_interval_does_not_block():
    import time

    limiter = RateLimiter(0.0)
    start = time.monotonic()
    for _ in range(5):
        await limiter.acquire()
    assert time.monotonic() - start < 0.1


@pytest.mark.asyncio
async def test_fetch_with_retry_retries_on_500():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await fetch_with_retry(
            client, "GET", "http://x/y", source="test", retries=3, backoff=0.001
        )
    assert resp.json() == {"ok": True}
    assert attempts["n"] == 3


@pytest.mark.asyncio
async def test_fetch_with_retry_gives_up_on_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RetrieverError):
            await fetch_with_retry(
                client, "GET", "http://x/y", source="test", retries=2, backoff=0.001
            )


@pytest.mark.asyncio
async def test_fetch_with_retry_exhausts_on_persistent_429():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, text="slow down")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RetrieverError):
            await fetch_with_retry(
                client, "GET", "http://x/y", source="test", retries=2, backoff=0.001
            )
    assert calls["n"] == 3


# ---------- arXiv ----------

ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.01234v1</id>
    <published>2024-01-05T00:00:00Z</published>
    <title>Graph Neural Networks
      for Citation Analysis</title>
    <summary>We study   citation graphs
      under sparse supervision.</summary>
    <author><name>Alice Chen</name></author>
    <author><name>Bob Smith</name></author>
    <arxiv:journal_ref>NeurIPS 2024</arxiv:journal_ref>
    <link href="http://arxiv.org/pdf/2401.01234v1" type="application/pdf" title="pdf"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/1901.00001v1</id>
    <published>2019-03-01T00:00:00Z</published>
    <title>Older Paper</title>
    <summary>Old work.</summary>
    <author><name>Carol Wang</name></author>
  </entry>
</feed>
"""


def arxiv_with(xml: str) -> ArxivRetriever:
    retriever = ArxivRetriever(min_interval=0.0)

    async def fake_search(query, filters):
        # 复用真实解析逻辑，仅替换 HTTP
        from xml.etree import ElementTree

        root = ElementTree.fromstring(xml)
        results = []
        for entry in root.findall("{http://www.w3.org/2005/Atom}entry"):
            meta = retriever._parse_entry(entry)
            if meta is None:
                continue
            if filters.year_from and (meta.year or 0) < filters.year_from:
                continue
            if filters.year_to and (meta.year or 9999) > filters.year_to:
                continue
            results.append(meta)
            if len(results) >= filters.limit:
                break
        return results

    retriever.search = fake_search  # type: ignore[assignment]
    return retriever


@pytest.mark.asyncio
async def test_arxiv_parses_entries():
    retriever = ArxivRetriever(min_interval=0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=ARXIV_XML)

    original = httpx.AsyncClient

    class PatchedClient(original):  # type: ignore[misc,valid-type]
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(**kwargs)

    import app.retriever.arxiv as mod

    mod.httpx.AsyncClient = PatchedClient  # type: ignore[attr-defined]
    try:
        results = await retriever.search("graph neural network", SearchFilters(limit=10))
    finally:
        mod.httpx.AsyncClient = original  # type: ignore[attr-defined]

    assert len(results) == 2
    first = results[0]
    assert first.title == "Graph Neural Networks for Citation Analysis"
    assert first.arxiv_id == "2401.01234"
    assert first.year == 2024
    assert first.authors == ["Alice Chen", "Bob Smith"]
    assert first.abstract == "We study citation graphs under sparse supervision."
    assert first.venue == "NeurIPS 2024"
    assert first.pdf_url == "http://arxiv.org/pdf/2401.01234v1"
    assert first.source == "arxiv"


@pytest.mark.asyncio
async def test_arxiv_year_filter():
    retriever = arxiv_with(ARXIV_XML)
    results = await retriever.search("x", SearchFilters(year_from=2020, limit=10))
    assert [r.year for r in results] == [2024]


@pytest.mark.asyncio
async def test_arxiv_empty_query_short_circuits():
    retriever = ArxivRetriever(min_interval=0.0)
    assert await retriever.search("   ", SearchFilters()) == []


@pytest.mark.asyncio
async def test_arxiv_malformed_xml_raises():
    retriever = ArxivRetriever(min_interval=0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<not valid xml")

    import app.retriever.arxiv as mod

    original = httpx.AsyncClient

    class PatchedClient(original):  # type: ignore[misc,valid-type]
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(**kwargs)

    mod.httpx.AsyncClient = PatchedClient  # type: ignore[attr-defined]
    try:
        with pytest.raises(RetrieverError):
            await retriever.search("x", SearchFilters())
    finally:
        mod.httpx.AsyncClient = original  # type: ignore[attr-defined]


# ---------- Semantic Scholar ----------

S2_JSON = {
    "data": [
        {
            "paperId": "abc123",
            "title": "Two Stage Encoder",
            "abstract": "An encoder.",
            "year": 2023,
            "venue": "ICML",
            "citationCount": 42,
            "externalIds": {"DOI": "10.1/xyz", "ArXiv": "2301.00001"},
            "authors": [{"name": "Dana Lee"}],
            "openAccessPdf": {"url": "http://example.com/p.pdf"},
            "url": "http://s2.org/abc123",
        },
        {"paperId": "no-title", "title": ""},
    ]
}


def patch_client(module, handler):
    original = httpx.AsyncClient

    class PatchedClient(original):  # type: ignore[misc,valid-type]
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(**kwargs)

    module.httpx.AsyncClient = PatchedClient
    return original


@pytest.mark.asyncio
async def test_semantic_scholar_parses_and_skips_untitled():
    import app.retriever.semantic_scholar as mod

    retriever = SemanticScholarRetriever()
    retriever.limiter = RateLimiter(0.0)

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=S2_JSON)

    original = patch_client(mod, handler)
    try:
        results = await retriever.search(
            "encoder", SearchFilters(limit=5, year_from=2020, year_to=2024)
        )
    finally:
        mod.httpx.AsyncClient = original

    assert len(results) == 1
    paper = results[0]
    assert paper.doi == "10.1/xyz"
    assert paper.arxiv_id == "2301.00001"
    assert paper.citation_count == 42
    assert paper.authors == ["Dana Lee"]
    assert "year=2020-2024" in captured["url"]


@pytest.mark.asyncio
async def test_semantic_scholar_fetch_references():
    import app.retriever.semantic_scholar as mod

    retriever = SemanticScholarRetriever()
    retriever.limiter = RateLimiter(0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"citedPaper": {"externalIds": {"DOI": "10.5/AAA"}, "title": "A"}},
                    {"citedPaper": {"externalIds": {}, "title": "No DOI Paper"}},
                    {"citedPaper": {}},
                ]
            },
        )

    original = patch_client(mod, handler)
    try:
        refs = await retriever.fetch_references("abc123")
    finally:
        mod.httpx.AsyncClient = original

    assert refs == ["10.5/aaa", "No DOI Paper"]


# ---------- Crossref ----------

CROSSREF_JSON = {
    "message": {
        "items": [
            {
                "DOI": "10.1145/12345",
                "title": ["Citation Graph Learning"],
                "author": [
                    {"given": "Eve", "family": "Zhang"},
                    {"given": "Frank", "family": "Wu"},
                ],
                "abstract": "<jats:p>We propose  a method.</jats:p>",
                "issued": {"date-parts": [[2022, 5, 1]]},
                "container-title": ["ACM TOIS"],
                "is-referenced-by-count": 17,
                "URL": "http://doi.org/10.1145/12345",
                "reference": [
                    {"DOI": "10.1/REF1"},
                    {"article-title": "Untitled Ref"},
                    {},
                ],
            }
        ]
    }
}


@pytest.mark.asyncio
async def test_crossref_parses_and_cleans_abstract():
    import app.retriever.crossref as mod

    retriever = CrossrefRetriever(mailto="me@example.com")
    retriever.limiter = RateLimiter(0.0)

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, json=CROSSREF_JSON)

    original = patch_client(mod, handler)
    try:
        results = await retriever.search(
            "citation graph", SearchFilters(limit=5, year_from=2020, year_to=2023)
        )
    finally:
        mod.httpx.AsyncClient = original

    assert len(results) == 1
    paper = results[0]
    assert paper.doi == "10.1145/12345"
    assert paper.authors == ["Eve Zhang", "Frank Wu"]
    assert paper.abstract == "We propose a method."
    assert paper.year == 2022
    assert paper.venue == "ACM TOIS"
    assert paper.references == ["10.1/ref1", "Untitled Ref"]
    assert "from-pub-date%3A2020" in captured["url"] or "from-pub-date:2020" in captured["url"]
    assert "me@example.com" in captured["ua"]


@pytest.mark.asyncio
async def test_crossref_fetch_by_doi_returns_none_on_error():
    import app.retriever.crossref as mod

    retriever = CrossrefRetriever()
    retriever.limiter = RateLimiter(0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope")

    original = patch_client(mod, handler)
    try:
        assert await retriever.fetch_by_doi("10.1/missing") is None
    finally:
        mod.httpx.AsyncClient = original


# ---------- 去重 ----------


def meta(**kwargs) -> PaperMeta:
    kwargs.setdefault("title", "Some Title")
    kwargs.setdefault("source", "arxiv")
    return PaperMeta(**kwargs)


def test_deduplicate_merges_by_doi():
    papers = [
        meta(title="A Paper", source="arxiv", doi="10.1/X", arxiv_id="2301.1"),
        meta(title="A Paper (preprint)", source="crossref", doi="10.1/x", year=2023, venue="ICML"),
    ]
    result = deduplicate(papers)
    assert len(result) == 1
    # Crossref 优先级更高，作为主记录
    assert result[0].source == "crossref"
    assert result[0].arxiv_id == "2301.1"
    assert result[0].venue == "ICML"


def test_deduplicate_merges_by_title_when_no_doi():
    papers = [
        meta(title="Graph Neural Network Survey", source="arxiv", year=2023),
        meta(title="graph neural network survey!", source="semantic_scholar", year=2023, citation_count=10),
    ]
    result = deduplicate(papers)
    assert len(result) == 1
    assert result[0].citation_count == 10


def test_deduplicate_keeps_distinct_papers():
    papers = [
        meta(title="Graph Neural Networks", doi="10.1/a"),
        meta(title="Quantum Computing Advances", doi="10.1/b"),
    ]
    assert len(deduplicate(papers)) == 2


def test_deduplicate_respects_year_gap():
    # 同名但年份差 5 年，视为不同文献
    papers = [
        meta(title="Annual Review of Methods", source="arxiv", year=2018),
        meta(title="Annual Review of Methods", source="arxiv", year=2023),
    ]
    assert len(deduplicate(papers)) == 2


def test_deduplicate_empty():
    assert deduplicate([]) == []


def test_merge_meta_does_not_overwrite_existing():
    primary = meta(title="T", abstract="original", year=2020)
    other = meta(title="T", abstract="replacement", year=2021, venue="ICML")
    merged = merge_meta(primary, other)
    assert merged.abstract == "original"
    assert merged.year == 2020
    assert merged.venue == "ICML"


def test_merge_meta_takes_max_citations():
    primary = meta(title="T", citation_count=5)
    merge_meta(primary, meta(title="T", citation_count=50))
    assert primary.citation_count == 50
    merge_meta(primary, meta(title="T", citation_count=1))
    assert primary.citation_count == 50


def test_merge_meta_unions_references():
    primary = meta(title="T", references=["10.1/a"])
    merge_meta(primary, meta(title="T", references=["10.1/a", "10.1/b"]))
    assert primary.references == ["10.1/a", "10.1/b"]


def test_find_duplicate_by_doi_and_title():
    existing = [meta(title="Known Paper", doi="10.1/known", year=2022)]
    assert find_duplicate(meta(title="Different", doi="10.1/KNOWN"), existing) is not None
    assert find_duplicate(meta(title="known paper!", year=2022), existing) is not None
    assert find_duplicate(meta(title="Unrelated Work"), existing) is None
