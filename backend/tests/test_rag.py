"""RAG 测试：用 HashEmbedder + InMemoryVectorStore，不依赖 Qdrant 容器。"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_smoke.db")

import pytest

from app.parser.chunker import Chunk
from app.rag import (
    HashEmbedder,
    InMemoryVectorStore,
    build_context,
    delete_paper_vectors,
    index_chunks,
    make_point_id,
    retrieve,
)


@pytest.fixture
def rag():
    return HashEmbedder(dim=128), InMemoryVectorStore()


def chunks_from(texts: list[str], section: str = "Method") -> list[Chunk]:
    return [Chunk(text=t, index=i, section=section) for i, t in enumerate(texts)]


@pytest.mark.asyncio
async def test_index_chunks_returns_count(rag):
    embedder, store = rag
    n = await index_chunks(
        "proj1",
        "paper1",
        chunks_from(["graph neural network for citation", "sparse supervision setting"]),
        paper_title="GNN Survey",
        embedder=embedder,
        store=store,
    )
    assert n == 2
    assert await store.count({"project_id": "proj1"}) == 2


@pytest.mark.asyncio
async def test_index_chunks_empty_is_noop(rag):
    embedder, store = rag
    assert await index_chunks("p", "paper", [], embedder=embedder, store=store) == 0
    assert await store.count() == 0


@pytest.mark.asyncio
async def test_reindex_overwrites_not_duplicates(rag):
    embedder, store = rag
    texts = ["alpha beta", "gamma delta"]
    await index_chunks("proj1", "paper1", chunks_from(texts), embedder=embedder, store=store)
    await index_chunks("proj1", "paper1", chunks_from(texts), embedder=embedder, store=store)
    assert await store.count({"project_id": "proj1"}) == 2


@pytest.mark.asyncio
async def test_retrieve_ranks_relevant_chunk_first(rag):
    embedder, store = rag
    await index_chunks(
        "proj1",
        "paper1",
        chunks_from(
            [
                "transformer attention mechanism scaling",
                "citation graph sparse supervision learning",
                "reinforcement learning robotics control",
            ]
        ),
        paper_title="Mixed Paper",
        embedder=embedder,
        store=store,
    )
    results = await retrieve(
        "proj1", "citation graph sparse supervision", limit=3, embedder=embedder, store=store
    )
    assert results
    assert "citation graph" in results[0].text


@pytest.mark.asyncio
async def test_retrieve_isolates_projects(rag):
    embedder, store = rag
    await index_chunks(
        "projA", "p1", chunks_from(["shared topic text here"]), embedder=embedder, store=store
    )
    await index_chunks(
        "projB", "p2", chunks_from(["shared topic text here"]), embedder=embedder, store=store
    )
    results = await retrieve("projA", "shared topic", limit=10, embedder=embedder, store=store)
    assert results
    assert all(r.paper_id == "p1" for r in results)


@pytest.mark.asyncio
async def test_retrieve_filters_by_paper_ids(rag):
    embedder, store = rag
    for pid in ("p1", "p2", "p3"):
        await index_chunks(
            "proj", pid, chunks_from(["common research topic"]), embedder=embedder, store=store
        )
    results = await retrieve(
        "proj", "common research", paper_ids=["p1", "p3"], limit=10,
        embedder=embedder, store=store,
    )
    assert {r.paper_id for r in results} <= {"p1", "p3"}


@pytest.mark.asyncio
async def test_retrieve_max_per_paper_diversifies(rag):
    embedder, store = rag
    # p1 有 5 个相似片段，p2 只有 1 个
    await index_chunks(
        "proj",
        "p1",
        chunks_from([f"graph learning variant {i}" for i in range(5)]),
        embedder=embedder,
        store=store,
    )
    await index_chunks(
        "proj", "p2", chunks_from(["graph learning variant x"]), embedder=embedder, store=store
    )
    results = await retrieve(
        "proj", "graph learning variant", limit=4, max_per_paper=2,
        embedder=embedder, store=store,
    )
    counts: dict[str, int] = {}
    for r in results:
        counts[r.paper_id] = counts.get(r.paper_id, 0) + 1
    assert all(c <= 2 for c in counts.values())


@pytest.mark.asyncio
async def test_retrieve_empty_query(rag):
    embedder, store = rag
    await index_chunks("proj", "p1", chunks_from(["text"]), embedder=embedder, store=store)
    assert await retrieve("proj", "   ", embedder=embedder, store=store) == []


@pytest.mark.asyncio
async def test_retrieve_carries_provenance(rag):
    embedder, store = rag
    await index_chunks(
        "proj",
        "paper-42",
        [Chunk(text="two stage encoder design", index=7, section="3 Method > 3.1 Encoder")],
        paper_title="Encoder Paper",
        embedder=embedder,
        store=store,
    )
    results = await retrieve("proj", "two stage encoder", embedder=embedder, store=store)
    hit = results[0]
    assert hit.paper_id == "paper-42"
    assert hit.paper_title == "Encoder Paper"
    assert hit.chunk_index == 7
    assert "3.1 Encoder" in hit.section
    assert "Encoder Paper" in hit.citation_label()


@pytest.mark.asyncio
async def test_delete_paper_vectors(rag):
    embedder, store = rag
    await index_chunks("proj", "p1", chunks_from(["a b c"]), embedder=embedder, store=store)
    await index_chunks("proj", "p2", chunks_from(["d e f"]), embedder=embedder, store=store)
    await delete_paper_vectors("proj", "p1", store=store)
    assert await store.count({"project_id": "proj"}) == 1
    assert await store.count({"project_id": "proj", "paper_id": "p1"}) == 0


def test_build_context_numbers_sources():
    from app.rag import RetrievedChunk

    chunks = [
        RetrievedChunk("first text", 0.9, "p1", "Paper One", "Intro", 0),
        RetrievedChunk("second text", 0.8, "p2", "Paper Two", "Method", 1),
    ]
    ctx = build_context(chunks)
    assert "[1]" in ctx and "[2]" in ctx
    assert "Paper One" in ctx and "first text" in ctx


def test_build_context_respects_char_budget():
    from app.rag import RetrievedChunk

    chunks = [
        RetrievedChunk("x" * 500, 0.9, f"p{i}", f"Paper {i}", "S", i) for i in range(10)
    ]
    ctx = build_context(chunks, max_chars=1200)
    assert len(ctx) <= 1400
    assert "[1]" in ctx
    assert "[10]" not in ctx


def test_make_point_id_is_deterministic():
    a = make_point_id("proj", "paper", 3)
    b = make_point_id("proj", "paper", 3)
    c = make_point_id("proj", "paper", 4)
    assert a == b
    assert a != c


@pytest.mark.asyncio
async def test_hash_embedder_is_deterministic_and_normalized():
    embedder = HashEmbedder(dim=64)
    v1, v2 = await embedder.embed(["same text here", "same text here"])
    assert v1 == v2
    norm = sum(x * x for x in v1) ** 0.5
    assert abs(norm - 1.0) < 1e-6
