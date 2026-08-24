"""研究方向生成与聚类测试：LLM 用 fake provider，向量用 HashEmbedder。"""

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
from app.parser.chunker import Chunk
from app.rag import HashEmbedder, InMemoryVectorStore, index_chunks
from app.services.cluster import kmeans, label_clusters_by_keywords, suggest_k
from app.services.direction import (
    PaperBrief,
    RawDirection,
    ResearchDirection,
    generate_directions,
)
from app.services.summarize import PaperSummary


class FakeProvider:
    name = "fake"

    def __init__(self, replies: list[str] | str) -> None:
        # 单个字符串直接传入时包装成列表，避免被拆成字符
        self.replies = [replies] if isinstance(replies, str) else list(replies)
        self.calls: list[LLMRequest] = []

    async def complete(self, req: LLMRequest, model: str) -> LLMResponse:
        self.calls.append(req)
        content = self.replies.pop(0) if self.replies else "{}"
        return LLMResponse(
            content=content, model=model, usage=Usage(prompt_tokens=1, completion_tokens=1)
        )

    async def stream(self, req: LLMRequest, model: str) -> AsyncIterator[str]:
        yield ""


def make_client(replies: list[str] | str) -> tuple[LLMClient, FakeProvider]:
    client = LLMClient()
    provider = FakeProvider(replies)
    client._provider_for = lambda model: provider  # type: ignore[method-assign]
    return client, provider


def brief(pid: str, title: str, terms: list[str], problem: str = "问题") -> PaperBrief:
    return PaperBrief(
        paper_id=pid,
        title=title,
        year=2024,
        summary=PaperSummary(
            one_line=f"{title} 的一句话总结",
            problem=problem,
            method="某方法",
            key_terms=terms,
            limitations=["样本有限"],
            future_work=["扩展到新场景"],
        ),
    )


BRIEFS = [
    brief("p1", "图神经网络稀疏监督", ["图神经网络", "稀疏监督"]),
    brief("p2", "引文网络表示学习", ["引文网络", "表示学习"]),
    brief("p3", "对比学习预训练", ["对比学习", "预训练"]),
    brief("p4", "动态图建模", ["动态图", "时序建模"]),
]


def payload(directions: list[dict]) -> str:
    return json.dumps({"directions": directions}, ensure_ascii=False)


@pytest.mark.asyncio
async def test_generate_directions_basic():
    client, provider = make_client(
        payload(
            [
                {
                    "statement": "在动态引文网络上引入对比学习预训练",
                    "gap": "现有工作只考虑静态图",
                    "innovation": "结合时序与对比学习",
                    "method_sketch": "两阶段训练",
                    "feasibility": 0.7,
                    "novelty": 0.8,
                    "evidence_indices": [1, 4],
                }
            ]
        )
    )
    result = await generate_directions("proj", BRIEFS, n=1, client=client)
    assert len(result) == 1
    d = result[0]
    assert d.evidence_paper_ids == ["p1", "p4"]
    assert d.evidence_titles == ["图神经网络稀疏监督", "动态图建模"]
    assert 0 < d.score <= 1
    # prompt 应包含所有文献编号
    prompt = provider.calls[0].messages[0].content
    assert "[1]" in prompt and "[4]" in prompt


@pytest.mark.asyncio
async def test_generate_directions_drops_unsupported():
    client, _ = make_client(
        payload(
            [
                {"statement": "有证据的方向", "evidence_indices": [2]},
                {"statement": "凭空编造的方向", "evidence_indices": []},
            ]
        )
    )
    result = await generate_directions("proj", BRIEFS, client=client)
    assert [d.statement for d in result] == ["有证据的方向"]


@pytest.mark.asyncio
async def test_generate_directions_keeps_unsupported_when_allowed():
    client, _ = make_client(
        payload([{"statement": "无证据方向", "evidence_indices": []}])
    )
    result = await generate_directions(
        "proj", BRIEFS, client=client, require_evidence=False
    )
    assert len(result) == 1
    assert result[0].evidence_paper_ids == []


@pytest.mark.asyncio
async def test_generate_directions_ignores_out_of_range_evidence():
    client, _ = make_client(
        payload([{"statement": "方向 A", "evidence_indices": [1, 99, 0, -3]}])
    )
    result = await generate_directions("proj", BRIEFS, client=client)
    assert result[0].evidence_paper_ids == ["p1"]


@pytest.mark.asyncio
async def test_generate_directions_dedups_evidence():
    client, _ = make_client(
        payload([{"statement": "方向 B", "evidence_indices": [2, 2, 2]}])
    )
    result = await generate_directions("proj", BRIEFS, client=client)
    assert result[0].evidence_paper_ids == ["p2"]


@pytest.mark.asyncio
async def test_generate_directions_sorted_by_score():
    client, _ = make_client(
        payload(
            [
                {
                    "statement": "低分方向",
                    "feasibility": 0.2,
                    "novelty": 0.2,
                    "evidence_indices": [1],
                },
                {
                    "statement": "高分方向",
                    "feasibility": 0.9,
                    "novelty": 0.9,
                    "evidence_indices": [2],
                },
            ]
        )
    )
    result = await generate_directions("proj", BRIEFS, client=client)
    assert [d.statement for d in result] == ["高分方向", "低分方向"]


@pytest.mark.asyncio
async def test_generate_directions_empty_briefs_skips_llm():
    client, provider = make_client(payload([{"statement": "x"}]))
    assert await generate_directions("proj", [], client=client) == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_generate_directions_includes_intent_in_prompt():
    client, provider = make_client(
        payload([{"statement": "方向", "evidence_indices": [1]}])
    )
    await generate_directions(
        "proj", BRIEFS, intent="我想做小样本场景", client=client
    )
    assert "我想做小样本场景" in provider.calls[0].messages[0].content


@pytest.mark.asyncio
async def test_generate_directions_with_clustering_and_rag():
    embedder = HashEmbedder(dim=128)
    store = InMemoryVectorStore()
    await index_chunks(
        "proj",
        "p1",
        [Chunk(text="graph neural network sparse supervision detail", index=0, section="Method")],
        paper_title="图神经网络稀疏监督",
        embedder=embedder,
        store=store,
    )
    client, provider = make_client(
        payload([{"statement": "方向", "evidence_indices": [1]}])
    )
    result = await generate_directions(
        "proj",
        BRIEFS,
        intent="graph neural network sparse supervision",
        client=client,
        embedder=embedder,
        store=store,
    )
    assert len(result) == 1
    prompt = provider.calls[0].messages[0].content
    # 应带上主题分布与原文片段
    assert "文献主题分布" in prompt
    assert "相关原文片段" in prompt


@pytest.mark.asyncio
async def test_generate_directions_survives_embedder_failure():
    class BrokenEmbedder:
        dim = 8

        async def embed(self, texts):
            raise RuntimeError("embedding service down")

    client, _ = make_client(payload([{"statement": "方向", "evidence_indices": [1]}]))
    result = await generate_directions(
        "proj", BRIEFS, client=client, embedder=BrokenEmbedder(), store=InMemoryVectorStore()
    )
    assert len(result) == 1


def test_raw_direction_normalizes_percent_scores():
    d = RawDirection(statement="x", feasibility=80, novelty=7)
    assert d.feasibility == pytest.approx(0.8)
    assert d.novelty == pytest.approx(0.7)


def test_raw_direction_clamps_out_of_range():
    d = RawDirection(statement="x", feasibility=-5, novelty=1000)
    assert d.feasibility == 0.0
    assert d.novelty <= 1.0


def test_direction_score_penalizes_lopsided():
    balanced = ResearchDirection(statement="a", feasibility=0.5, novelty=0.5)
    lopsided = ResearchDirection(statement="b", feasibility=0.95, novelty=0.05)
    assert balanced.score > lopsided.score


def test_direction_score_zero_when_both_zero():
    d = ResearchDirection(statement="a", feasibility=0.0, novelty=0.0)
    assert d.score == 0.0


# ---- 聚类 ----


def test_suggest_k_bounds():
    assert suggest_k(1) == 1
    assert suggest_k(2) == 1
    assert suggest_k(8) >= 2
    assert suggest_k(1000, max_k=6) == 6


def test_kmeans_separates_two_groups():
    # 两组正交向量，应被分到不同簇
    group_a = [[1.0, 0.0, 0.0], [0.9, 0.1, 0.0], [0.95, 0.05, 0.0]]
    group_b = [[0.0, 0.0, 1.0], [0.0, 0.1, 0.9], [0.05, 0.0, 0.95]]
    clusters = kmeans(group_a + group_b, k=2)
    assert len(clusters) == 2
    assignment = {}
    for cluster in clusters:
        for idx in cluster.member_indices:
            assignment[idx] = cluster.id
    assert assignment[0] == assignment[1] == assignment[2]
    assert assignment[3] == assignment[4] == assignment[5]
    assert assignment[0] != assignment[3]


def test_kmeans_empty_and_single():
    assert kmeans([]) == []
    single = kmeans([[1.0, 0.0]], k=3)
    assert len(single) == 1
    assert single[0].member_indices == [0]


def test_kmeans_is_deterministic():
    vectors = [[1.0, 0.0], [0.0, 1.0], [0.9, 0.1], [0.1, 0.9]]
    a = kmeans(vectors, k=2)
    b = kmeans(vectors, k=2)
    assert [c.member_indices for c in a] == [c.member_indices for c in b]


def test_kmeans_all_members_assigned():
    vectors = [[float(i % 3), float(i % 5), 1.0] for i in range(20)]
    clusters = kmeans(vectors, k=4)
    assigned = sorted(i for c in clusters for i in c.member_indices)
    assert assigned == list(range(20))


def test_label_clusters_by_keywords():
    clusters = kmeans([[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]], k=2)
    keyword_lists = [["图神经网络"], ["图神经网络"], ["强化学习"]]
    label_clusters_by_keywords(clusters, keyword_lists)
    labels = {c.label for c in clusters}
    assert "图神经网络" in labels
    assert "强化学习" in labels
