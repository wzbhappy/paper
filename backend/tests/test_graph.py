"""引用图谱与社区发现测试：用内存实现，不依赖 Neo4j。"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_smoke.db")

import pytest

from app.graph import (
    GraphPaper,
    InMemoryCitationGraph,
    build_adjacency,
    connected_components,
    detect_communities,
    label_propagation,
)


@pytest.fixture
def graph():
    return InMemoryCitationGraph()


@pytest.mark.asyncio
async def test_add_paper_and_stats(graph):
    await graph.add_paper("proj", GraphPaper(key="10.1/a", title="A", year=2023))
    await graph.add_paper("proj", GraphPaper(key="10.1/b", title="B"))
    stats = await graph.stats("proj")
    assert stats.node_count == 2
    assert stats.edge_count == 0


@pytest.mark.asyncio
async def test_add_paper_merges_without_overwriting(graph):
    await graph.add_paper("proj", GraphPaper(key="k", title="Original", year=2020))
    await graph.add_paper("proj", GraphPaper(key="k", title="Replacement", paper_id="p1"))
    papers = await graph.papers("proj")
    assert len(papers) == 1
    assert papers[0].title == "Original"
    assert papers[0].paper_id == "p1"


@pytest.mark.asyncio
async def test_add_citations_creates_edges_and_nodes(graph):
    await graph.add_paper("proj", GraphPaper(key="a"))
    added = await graph.add_citations("proj", "a", ["b", "c"])
    assert added == 2
    stats = await graph.stats("proj")
    assert stats.edge_count == 2
    # 被引文献自动成为节点
    assert stats.node_count == 3


@pytest.mark.asyncio
async def test_add_citations_ignores_self_and_duplicates(graph):
    await graph.add_paper("proj", GraphPaper(key="a"))
    assert await graph.add_citations("proj", "a", ["a", "b"]) == 1
    assert await graph.add_citations("proj", "a", ["b"]) == 0


@pytest.mark.asyncio
async def test_neighbors_is_undirected(graph):
    await graph.add_citations("proj", "a", ["b"])
    assert await graph.neighbors("proj", "a") == ["b"]
    assert await graph.neighbors("proj", "b") == ["a"]


@pytest.mark.asyncio
async def test_most_cited_ranking(graph):
    await graph.add_citations("proj", "a", ["popular"])
    await graph.add_citations("proj", "b", ["popular"])
    await graph.add_citations("proj", "c", ["niche"])
    ranked = await graph.most_cited("proj")
    assert ranked[0] == ("popular", 2)
    assert ("niche", 1) in ranked


@pytest.mark.asyncio
async def test_projects_are_isolated(graph):
    await graph.add_citations("projA", "a", ["b"])
    await graph.add_citations("projB", "x", ["y"])
    assert (await graph.stats("projA")).edge_count == 1
    assert await graph.neighbors("projA", "x") == []


@pytest.mark.asyncio
async def test_clear_project(graph):
    await graph.add_citations("proj", "a", ["b"])
    await graph.clear_project("proj")
    stats = await graph.stats("proj")
    assert stats.node_count == 0
    assert stats.edge_count == 0


@pytest.mark.asyncio
async def test_all_edges_sorted(graph):
    await graph.add_citations("proj", "b", ["c"])
    await graph.add_citations("proj", "a", ["b"])
    edges = await graph.all_edges("proj")
    assert [(e.source_key, e.target_key) for e in edges] == [("a", "b"), ("b", "c")]


# ---------- 社区发现 ----------


def test_build_adjacency_is_undirected():
    adj = build_adjacency([("a", "b"), ("b", "c")])
    assert adj["a"] == {"b"}
    assert adj["b"] == {"a", "c"}


def test_build_adjacency_skips_self_loops():
    adj = build_adjacency([("a", "a"), ("a", "b")])
    assert adj["a"] == {"b"}


def test_build_adjacency_includes_isolated_nodes():
    adj = build_adjacency([("a", "b")], nodes=["a", "b", "lonely"])
    assert adj["lonely"] == set()


def test_connected_components_two_groups():
    adj = build_adjacency([("a", "b"), ("b", "c"), ("x", "y")])
    components = connected_components(adj)
    assert len(components) == 2
    assert components[0] == ["a", "b", "c"]
    assert components[1] == ["x", "y"]


def test_label_propagation_finds_two_clusters():
    # 两个紧密团，仅一条弱连接
    edges = [
        ("a1", "a2"), ("a2", "a3"), ("a1", "a3"),
        ("b1", "b2"), ("b2", "b3"), ("b1", "b3"),
    ]
    communities = label_propagation(build_adjacency(edges))
    assert len(communities) == 2
    groups = {frozenset(c.members) for c in communities}
    assert frozenset({"a1", "a2", "a3"}) in groups
    assert frozenset({"b1", "b2", "b3"}) in groups


def test_label_propagation_is_deterministic():
    edges = [("a", "b"), ("b", "c"), ("x", "y"), ("y", "z")]
    adj = build_adjacency(edges)
    first = [c.members for c in label_propagation(adj)]
    second = [c.members for c in label_propagation(adj)]
    assert first == second


def test_label_propagation_empty():
    assert label_propagation({}) == []


def test_detect_communities_falls_back_on_sparse_edges():
    # 边太少，走连通分量路径
    communities = detect_communities([("a", "b")], nodes=["a", "b", "c", "d"])
    members = {frozenset(c.members) for c in communities}
    assert frozenset({"a", "b"}) in members
    # 孤立节点合并为一簇，不产生两个单元素簇
    assert frozenset({"c", "d"}) in members


def test_detect_communities_no_edges():
    communities = detect_communities([], nodes=["a", "b", "c"])
    assert len(communities) == 1
    assert communities[0].members == ["a", "b", "c"]


def test_detect_communities_empty_input():
    assert detect_communities([], nodes=[]) == []


def test_detect_communities_all_members_present():
    edges = [("a", "b"), ("b", "c"), ("c", "a"), ("x", "y"), ("y", "z"), ("z", "x")]
    nodes = ["a", "b", "c", "x", "y", "z"]
    communities = detect_communities(edges, nodes=nodes)
    collected = sorted(m for c in communities for m in c.members)
    assert collected == sorted(nodes)


def test_community_ids_are_sequential():
    edges = [("a", "b"), ("b", "c"), ("x", "y"), ("y", "z"), ("p", "q"), ("q", "r")]
    communities = detect_communities(edges)
    assert [c.id for c in communities] == list(range(len(communities)))
