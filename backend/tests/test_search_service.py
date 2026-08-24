"""检索服务编排测试：假 retriever + 假 LLM，不打网络。"""

import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_smoke.db")

import pytest

from app.llm import LLMClient, LLMRequest
from app.llm.base import LLMResponse, Usage
from app.retriever import PaperMeta, SearchFilters
from app.services.search import (
    build_retrievers,
    expand_query,
    score_relevance,
    search_papers,
)


class FakeProvider:
    name = "fake"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[LLMRequest] = []

    async def complete(self, req: LLMRequest, model: str) -> LLMResponse:
        self.calls.append(req)
        return LLMResponse(
            content=self.reply, model=model, usage=Usage(prompt_tokens=1, completion_tokens=1)
        )

    async def stream(self, req: LLMRequest, model: str) -> AsyncIterator[str]:
        yield ""


def make_client(reply: str) -> tuple[LLMClient, FakeProvider]:
    client = LLMClient()
    provider = FakeProvider(reply)
    client._provider_for = lambda model: provider  # type: ignore[method-assign]
    return client, provider


EXPANDED = json.dumps(
    {
        "queries": ["graph neural network", "citation network embedding"],
        "keywords": ["graph", "citation"],
    }
)


class FakeRetriever:
    def __init__(self, name: str, results: list[PaperMeta], fail: bool = False) -> None:
        self.name = name
        self.results = results
        self.fail = fail
        self.queries: list[str] = []

    async def search(self, query: str, filters: SearchFilters) -> list[PaperMeta]:
        self.queries.append(query)
        if self.fail:
            raise RuntimeError(f"{self.name} is down")
        return list(self.results)


def paper(title: str, **kwargs) -> PaperMeta:
    kwargs.setdefault("source", "arxiv")
    return PaperMeta(title=title, **kwargs)


@pytest.mark.asyncio
async def test_expand_query_returns_multiple():
    client, provider = make_client(EXPANDED)
    result = await expand_query("引文网络研究", client=client)
    assert result.queries == ["graph neural network", "citation network embedding"]
    assert result.keywords == ["graph", "citation"]
    assert "引文网络研究" in provider.calls[0].messages[0].content


@pytest.mark.asyncio
async def test_expand_query_falls_back_on_llm_failure():
    client, _ = make_client("not json at all")
    result = await expand_query("原始查询", client=client)
    assert result.queries == ["原始查询"]


@pytest.mark.asyncio
async def test_expand_query_empty_intent():
    client, provider = make_client(EXPANDED)
    result = await expand_query("   ", client=client)
    assert result.queries == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_search_papers_queries_all_sources_with_all_queries():
    a = FakeRetriever("a", [paper("Paper A", doi="10.1/a")])
    b = FakeRetriever("b", [paper("Paper B", doi="10.1/b")])
    client, _ = make_client(EXPANDED)

    results, expanded = await search_papers(
        "topic", client=client, retrievers=[a, b], filters=SearchFilters(limit=10)
    )
    assert len(results) == 2
    assert len(expanded.queries) == 2
    # 每个源都收到了两个查询
    assert len(a.queries) == 2
    assert len(b.queries) == 2


@pytest.mark.asyncio
async def test_search_papers_isolates_source_failure():
    good = FakeRetriever("good", [paper("Good Paper", doi="10.1/good")])
    bad = FakeRetriever("bad", [], fail=True)
    client, _ = make_client(EXPANDED)

    results, _ = await search_papers(
        "topic", client=client, retrievers=[good, bad], filters=SearchFilters(limit=10)
    )
    assert [r.title for r in results] == ["Good Paper"]


@pytest.mark.asyncio
async def test_search_papers_deduplicates_across_sources():
    same = paper("Identical Title", doi="10.1/same", source="arxiv")
    other = paper("Identical Title", doi="10.1/SAME", source="crossref", year=2023)
    a = FakeRetriever("a", [same])
    b = FakeRetriever("b", [other])
    client, _ = make_client(EXPANDED)

    results, _ = await search_papers(
        "topic", client=client, retrievers=[a, b], filters=SearchFilters(limit=10)
    )
    assert len(results) == 1


@pytest.mark.asyncio
async def test_search_papers_respects_limit():
    many = [paper(f"Paper {i}", doi=f"10.1/{i}") for i in range(30)]
    r = FakeRetriever("r", many)
    client, _ = make_client(EXPANDED)

    results, _ = await search_papers(
        "topic", client=client, retrievers=[r], filters=SearchFilters(limit=5)
    )
    assert len(results) == 5


@pytest.mark.asyncio
async def test_search_papers_without_expansion_uses_raw_query():
    r = FakeRetriever("r", [paper("P", doi="10.1/p")])
    client, provider = make_client(EXPANDED)

    await search_papers(
        "exact phrase", expand=False, client=client, retrievers=[r]
    )
    assert r.queries == ["exact phrase"]
    assert provider.calls == []


@pytest.mark.asyncio
async def test_search_papers_no_retrievers():
    client, _ = make_client(EXPANDED)
    results, _ = await search_papers("topic", client=client, retrievers=[])
    assert results == []


@pytest.mark.asyncio
async def test_search_papers_sorts_by_relevance():
    relevant = paper(
        "graph citation analysis",
        doi="10.1/rel",
        abstract="graph and citation methods",
        citation_count=500,
    )
    irrelevant = paper("unrelated quantum topic", doi="10.1/irr")
    r = FakeRetriever("r", [irrelevant, relevant])
    client, _ = make_client(EXPANDED)

    results, _ = await search_papers(
        "topic", client=client, retrievers=[r], filters=SearchFilters(limit=10)
    )
    assert results[0].title == "graph citation analysis"


def test_score_relevance_keyword_coverage():
    high = paper("graph citation network", abstract="graph citation")
    low = paper("unrelated topic")
    assert score_relevance(high, ["graph", "citation"]) > score_relevance(low, ["graph", "citation"])


def test_score_relevance_no_keywords_is_neutral():
    p = paper("anything")
    assert 0 < score_relevance(p, []) <= 1


def test_score_relevance_citation_bonus_capped():
    a = paper("t", citation_count=100)
    b = paper("t", citation_count=1_000_000)
    assert score_relevance(b, []) - score_relevance(a, []) <= 0.3


def test_score_relevance_within_bounds():
    p = paper("graph citation", abstract="graph citation", citation_count=99999)
    assert 0.0 <= score_relevance(p, ["graph", "citation"]) <= 1.0


def test_build_retrievers_defaults_to_all():
    assert len(build_retrievers()) == 3
    names = {r.name for r in build_retrievers()}
    assert names == {"arxiv", "semantic_scholar", "crossref"}


def test_build_retrievers_filters_by_name():
    retrievers = build_retrievers(["arxiv", "unknown_source"])
    assert [r.name for r in retrievers] == ["arxiv"]
