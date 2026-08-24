"""Phase 2 端到端测试：检索 → 导入 → 引用图谱 → 综述生成。"""

import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_p2.db")

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import engine
from app.graph import InMemoryCitationGraph, set_citation_graph
from app.llm import LLMClient, LLMRequest, set_llm
from app.llm.base import LLMResponse, Usage
from app.main import app
from app.models import Base
from app.rag import HashEmbedder, InMemoryVectorStore, set_embedder, set_vector_store
from app.retriever import PaperMeta, SearchFilters

EXPANDED = json.dumps(
    {"queries": ["graph neural network"], "keywords": ["graph", "citation"]}
)

OUTLINE = json.dumps(
    {
        "sections": [
            {"cluster_id": 0, "title": "基于图神经网络的方法", "order": 1},
        ]
    },
    ensure_ascii=False,
)


class TaskProvider:
    """按 prompt 特征返回内容，覆盖关键词扩展 / 小节命名 / 正文生成。"""

    name = "task-fake"

    def __init__(self) -> None:
        self.calls: list[LLMRequest] = []

    async def complete(self, req: LLMRequest, model: str) -> LLMResponse:
        self.calls.append(req)
        prompt = req.messages[0].content
        if "检索需求" in prompt or "queries" in prompt:
            content = EXPANDED
        elif "文献分组" in prompt:
            content = OUTLINE
        elif "本节主题" in prompt:
            content = "已有研究表明该方法有效 [1]，后续工作进一步扩展 [2]。"
        else:
            content = "{}"
        return LLMResponse(
            content=content, model=model, usage=Usage(prompt_tokens=1, completion_tokens=1)
        )

    async def stream(self, req: LLMRequest, model: str) -> AsyncIterator[str]:
        yield ""


class FakeRetriever:
    name = "fake"

    def __init__(self, results: list[PaperMeta]) -> None:
        self.results = results

    async def search(self, query: str, filters: SearchFilters) -> list[PaperMeta]:
        return list(self.results)


SEARCH_RESULTS = [
    PaperMeta(
        title="Graph Neural Networks for Citations",
        source="semantic_scholar",
        source_id="s2-1",
        authors=["Alice Chen"],
        abstract="We study citation graphs with GNNs under sparse labels.",
        year=2023,
        doi="10.1/gnn",
        citation_count=120,
        references=["10.1/base"],
    ),
    PaperMeta(
        title="Foundations of Citation Analysis",
        source="crossref",
        source_id="10.1/base",
        authors=["Bob Smith"],
        abstract="Classical foundations of citation analysis and bibliometrics.",
        year=2015,
        doi="10.1/base",
        citation_count=800,
    ),
]


@pytest.fixture
async def client(monkeypatch):
    provider = TaskProvider()
    llm = LLMClient()
    llm._provider_for = lambda model: provider  # type: ignore[method-assign]
    set_llm(llm)

    set_embedder(HashEmbedder(dim=64))
    set_vector_store(InMemoryVectorStore())
    graph = InMemoryCitationGraph()
    set_citation_graph(graph)

    # 检索层替换为假源，不打网络
    monkeypatch.setattr(
        "app.services.search.build_retrievers",
        lambda sources=None: [FakeRetriever(SEARCH_RESULTS)],
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        ac.graph = graph  # type: ignore[attr-defined]
        ac.provider = provider  # type: ignore[attr-defined]
        yield ac

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    set_llm(None)
    set_embedder(None)
    set_vector_store(None)
    set_citation_graph(None)


async def new_project(client) -> str:
    resp = await client.post(
        "/api/v1/projects", json={"title": "引文分析综述", "discipline": "计算机科学"}
    )
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_search_returns_results_with_expansion(client):
    pid = await new_project(client)
    resp = await client.post(
        f"/api/v1/projects/{pid}/search",
        json={"query": "引文网络", "limit": 10},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["expanded_queries"] == ["graph neural network"]
    assert body["keywords"] == ["graph", "citation"]
    assert len(body["results"]) == 2
    assert all(r["already_in_library"] is False for r in body["results"])


@pytest.mark.asyncio
async def test_import_creates_papers_and_graph_edges(client):
    pid = await new_project(client)
    search = (
        await client.post(f"/api/v1/projects/{pid}/search", json={"query": "引文网络"})
    ).json()

    resp = await client.post(
        f"/api/v1/projects/{pid}/search/import", json={"items": search["results"]}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["imported"] == 2
    assert body["skipped"] == 0

    papers = (await client.get(f"/api/v1/projects/{pid}/papers")).json()
    assert len(papers) == 2
    assert all(p["status"] == "metadata_only" for p in papers)

    # 引用关系写入图谱：10.1/gnn -> 10.1/base
    stats = await client.graph.stats(pid)
    assert stats.edge_count == 1
    ranked = await client.graph.most_cited(pid)
    assert ranked[0][0] == "10.1/base"


@pytest.mark.asyncio
async def test_search_marks_already_imported(client):
    pid = await new_project(client)
    first = (
        await client.post(f"/api/v1/projects/{pid}/search", json={"query": "引文网络"})
    ).json()
    await client.post(
        f"/api/v1/projects/{pid}/search/import", json={"items": first["results"]}
    )

    second = (
        await client.post(f"/api/v1/projects/{pid}/search", json={"query": "引文网络"})
    ).json()
    assert all(r["already_in_library"] for r in second["results"])


@pytest.mark.asyncio
async def test_import_is_idempotent(client):
    pid = await new_project(client)
    search = (
        await client.post(f"/api/v1/projects/{pid}/search", json={"query": "引文网络"})
    ).json()
    await client.post(
        f"/api/v1/projects/{pid}/search/import", json={"items": search["results"]}
    )
    again = await client.post(
        f"/api/v1/projects/{pid}/search/import", json={"items": search["results"]}
    )
    assert again.json() == {"imported": 0, "skipped": 2, "paper_ids": []}


@pytest.mark.asyncio
async def test_graph_stats_endpoint(client):
    pid = await new_project(client)
    search = (
        await client.post(f"/api/v1/projects/{pid}/search", json={"query": "引文网络"})
    ).json()
    await client.post(
        f"/api/v1/projects/{pid}/search/import", json={"items": search["results"]}
    )

    resp = await client.get(f"/api/v1/projects/{pid}/graph/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["edge_count"] == 1
    assert body["node_count"] >= 2
    top = body["most_cited"][0]
    assert top["key"] == "10.1/base"
    assert top["title"] == "Foundations of Citation Analysis"


@pytest.mark.asyncio
async def test_graph_stats_degrades_when_unavailable(client):
    class BrokenGraph:
        async def stats(self, project_id):
            raise RuntimeError("neo4j unreachable")

        async def most_cited(self, project_id, limit=10):
            raise RuntimeError("neo4j unreachable")

    set_citation_graph(BrokenGraph())
    pid = await new_project(client)
    body = (await client.get(f"/api/v1/projects/{pid}/graph/stats")).json()
    assert body["available"] is False
    assert "neo4j" in body["error"]


@pytest.mark.asyncio
async def test_review_generation_end_to_end(client):
    pid = await new_project(client)
    search = (
        await client.post(f"/api/v1/projects/{pid}/search", json={"query": "引文网络"})
    ).json()
    await client.post(
        f"/api/v1/projects/{pid}/search/import", json={"items": search["results"]}
    )

    gen = await client.post(
        f"/api/v1/projects/{pid}/review/generate",
        json={"organization": "topic", "words_per_section": 300},
    )
    assert gen.status_code == 202, gen.text
    job_id = gen.json()["id"]

    job = (await client.get(f"/api/v1/projects/{pid}/jobs/{job_id}")).json()
    assert job["status"] == "done", job
    assert job["result"]["sections"] == 1
    assert job["result"]["references"] == 2

    review = (await client.get(f"/api/v1/projects/{pid}/review/latest")).json()
    assert review["organization"] == "topic"
    assert len(review["sections"]) == 1
    assert review["sections"][0]["title"] == "基于图神经网络的方法"
    assert "## 参考文献" in review["markdown"]
    assert "@article{" in review["bibtex"]
    assert review["invalid_citation_count"] == 0


@pytest.mark.asyncio
async def test_review_requires_papers(client):
    pid = await new_project(client)
    resp = await client.post(f"/api/v1/projects/{pid}/review/generate", json={})
    assert resp.status_code == 400
    assert "no papers" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_review_latest_404_when_none(client):
    pid = await new_project(client)
    assert (await client.get(f"/api/v1/projects/{pid}/review/latest")).status_code == 404


@pytest.mark.asyncio
async def test_review_manual_edit_persists(client):
    pid = await new_project(client)
    search = (
        await client.post(f"/api/v1/projects/{pid}/search", json={"query": "引文网络"})
    ).json()
    await client.post(
        f"/api/v1/projects/{pid}/search/import", json={"items": search["results"]}
    )
    await client.post(f"/api/v1/projects/{pid}/review/generate", json={})
    review = (await client.get(f"/api/v1/projects/{pid}/review/latest")).json()

    edited = "# 我改过的综述\n\n手工内容。"
    resp = await client.put(
        f"/api/v1/projects/{pid}/review/{review['id']}", json={"markdown": edited}
    )
    assert resp.status_code == 200
    assert resp.json()["markdown"] == edited

    again = (await client.get(f"/api/v1/projects/{pid}/review/latest")).json()
    assert again["markdown"] == edited


@pytest.mark.asyncio
async def test_review_list_returns_history(client):
    pid = await new_project(client)
    search = (
        await client.post(f"/api/v1/projects/{pid}/search", json={"query": "引文网络"})
    ).json()
    await client.post(
        f"/api/v1/projects/{pid}/search/import", json={"items": search["results"]}
    )
    await client.post(f"/api/v1/projects/{pid}/review/generate", json={})
    await client.post(
        f"/api/v1/projects/{pid}/review/generate", json={"organization": "timeline"}
    )

    reviews = (await client.get(f"/api/v1/projects/{pid}/review")).json()
    assert len(reviews) == 2
    assert {r["organization"] for r in reviews} == {"topic", "timeline"}


@pytest.mark.asyncio
async def test_review_rejects_invalid_organization(client):
    pid = await new_project(client)
    resp = await client.post(
        f"/api/v1/projects/{pid}/review/generate", json={"organization": "nonsense"}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_import_requires_items(client):
    pid = await new_project(client)
    resp = await client.post(f"/api/v1/projects/{pid}/search/import", json={"items": []})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_unknown_project_404(client):
    fake = "00000000-0000-0000-0000-000000000000"
    resp = await client.post(f"/api/v1/projects/{fake}/search", json={"query": "x"})
    assert resp.status_code == 404
