"""综述生成测试：LLM 用 fake provider，图谱与向量库用内存实现。"""

import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_smoke.db")

import pytest

from app.graph import InMemoryCitationGraph
from app.llm import LLMClient, LLMRequest, TaskType
from app.llm.base import LLMResponse, Usage
from app.services.direction import PaperBrief
from app.services.review import (
    ReviewDraft,
    ReviewSection,
    build_clusters,
    extract_citations,
    generate_review,
    generate_section,
    name_sections,
    validate_citations,
)
from app.services.summarize import PaperSummary


class TaskProvider:
    """按 task 返回预设内容。review_gen 分两种：命名用 JSON，正文用文本。"""

    name = "task-fake"

    def __init__(self, outline: str | None = None, section_text: str = "默认正文 [1]。") -> None:
        self.outline = outline
        self.section_text = section_text
        self.calls: list[LLMRequest] = []

    async def complete(self, req: LLMRequest, model: str) -> LLMResponse:
        self.calls.append(req)
        prompt = req.messages[0].content
        # 命名 prompt 含「文献分组」，正文 prompt 含「本节主题」
        if "文献分组" in prompt and self.outline is not None:
            content = self.outline
        elif "本节主题" in prompt:
            content = self.section_text
        else:
            content = "{}"
        return LLMResponse(
            content=content, model=model, usage=Usage(prompt_tokens=1, completion_tokens=1)
        )

    async def stream(self, req: LLMRequest, model: str) -> AsyncIterator[str]:
        yield ""


def make_client(provider) -> LLMClient:
    client = LLMClient()
    client._provider_for = lambda model: provider  # type: ignore[method-assign]
    return client


def brief(pid: str, title: str, year: int = 2023) -> PaperBrief:
    return PaperBrief(
        paper_id=pid,
        title=title,
        year=year,
        summary=PaperSummary(
            one_line=f"{title} 的总结",
            problem="某问题",
            method="某方法",
            conclusion="某结论",
            limitations=["局限"],
            key_terms=["术语"],
        ),
    )


BRIEFS = [brief(f"p{i}", f"论文 {i}") for i in range(1, 7)]


# ---------- 引用校验 ----------


def test_extract_citations():
    assert extract_citations("论断 [1]，另一个 [2,3]。") == [1, 2, 3]
    assert extract_citations("无引用") == []
    assert extract_citations("重复 [1] 又 [1]") == [1]


def test_extract_citations_handles_spaces():
    assert extract_citations("多个 [1, 2 , 3]") == [1, 2, 3]


def test_validate_citations_keeps_valid():
    content, invalid = validate_citations("论断 [1] 和 [2]。", max_index=3)
    assert content == "论断 [1] 和 [2]。"
    assert invalid == []


def test_validate_citations_strips_out_of_range():
    content, invalid = validate_citations("真的 [1]，编造的 [99]。", max_index=2)
    assert "[99]" not in content
    assert "[1]" in content
    assert invalid == [99]


def test_validate_citations_partial_group():
    content, invalid = validate_citations("混合 [1,50]。", max_index=2)
    assert "[1]" in content
    assert "50" not in content
    assert invalid == [50]


def test_validate_citations_strips_zero_and_all_invalid():
    content, invalid = validate_citations("全错 [0] 和 [7]。", max_index=3)
    assert "[" not in content
    assert invalid == [0, 7]


# ---------- 分簇 ----------


@pytest.mark.asyncio
async def test_build_clusters_single_when_few_papers():
    clusters = await build_clusters("proj", BRIEFS[:2])
    assert len(clusters) == 1
    assert set(clusters[0].members) == {"p1", "p2"}


@pytest.mark.asyncio
async def test_build_clusters_single_when_no_edges():
    graph = InMemoryCitationGraph()
    clusters = await build_clusters("proj", BRIEFS, graph=graph)
    assert len(clusters) == 1
    assert len(clusters[0].members) == 6


@pytest.mark.asyncio
async def test_build_clusters_uses_citation_edges():
    graph = InMemoryCitationGraph()
    # 两个团：p1-p2-p3 与 p4-p5-p6
    for src, targets in [
        ("p1", ["p2", "p3"]),
        ("p2", ["p3"]),
        ("p4", ["p5", "p6"]),
        ("p5", ["p6"]),
    ]:
        await graph.add_citations("proj", src, targets)

    clusters = await build_clusters("proj", BRIEFS, graph=graph)
    assert len(clusters) == 2
    groups = {frozenset(c.members) for c in clusters}
    assert frozenset({"p1", "p2", "p3"}) in groups
    assert frozenset({"p4", "p5", "p6"}) in groups


@pytest.mark.asyncio
async def test_build_clusters_ignores_external_edges():
    graph = InMemoryCitationGraph()
    # p1 引用库外文献，不应影响分簇
    await graph.add_citations("proj", "p1", ["external-doi-1", "external-doi-2"])
    clusters = await build_clusters("proj", BRIEFS, graph=graph)
    assert len(clusters) == 1


@pytest.mark.asyncio
async def test_build_clusters_survives_graph_failure():
    class BrokenGraph:
        async def all_edges(self, project_id):
            raise RuntimeError("neo4j down")

    clusters = await build_clusters("proj", BRIEFS, graph=BrokenGraph())
    assert len(clusters) == 1


# ---------- 小节命名 ----------


@pytest.mark.asyncio
async def test_name_sections_uses_llm_titles():
    from app.graph import Community

    clusters = [Community(id=0, members=["p1", "p2"]), Community(id=1, members=["p3"])]
    outline = json.dumps(
        {
            "sections": [
                {"cluster_id": 1, "title": "早期方法", "order": 1},
                {"cluster_id": 0, "title": "深度学习方法", "order": 2},
            ]
        },
        ensure_ascii=False,
    )
    client = make_client(TaskProvider(outline=outline))
    briefs_by_key = {b.paper_id: b for b in BRIEFS}

    sections = await name_sections(clusters, briefs_by_key, client=client)
    # 按 order 排序
    assert [s.title for s in sections] == ["早期方法", "深度学习方法"]


@pytest.mark.asyncio
async def test_name_sections_falls_back_on_bad_json():
    from app.graph import Community

    clusters = [Community(id=0, members=["p1"])]
    client = make_client(TaskProvider(outline="not json"))
    sections = await name_sections(clusters, {b.paper_id: b for b in BRIEFS}, client=client)
    assert len(sections) == 1
    assert "研究主题" in sections[0].title


@pytest.mark.asyncio
async def test_name_sections_fills_missing_clusters():
    from app.graph import Community

    clusters = [Community(id=0, members=["p1"]), Community(id=1, members=["p2"])]
    outline = json.dumps({"sections": [{"cluster_id": 0, "title": "有名字", "order": 1}]})
    client = make_client(TaskProvider(outline=outline))
    sections = await name_sections(clusters, {b.paper_id: b for b in BRIEFS}, client=client)
    assert len(sections) == 2
    titles = [s.title for s in sections]
    assert "有名字" in titles


@pytest.mark.asyncio
async def test_name_sections_empty_clusters():
    assert await name_sections([], {}) == []


# ---------- 单节生成 ----------


@pytest.mark.asyncio
async def test_generate_section_records_paper_ids():
    provider = TaskProvider(section_text="研究表明 [1] 和 [2] 都有效。")
    client = make_client(provider)
    section = await generate_section("proj", "某主题", BRIEFS[:2], client=client)
    assert section.title == "某主题"
    assert section.paper_ids == ["p1", "p2"]
    assert "[1]" in section.content
    assert section.invalid_citations == []


@pytest.mark.asyncio
async def test_generate_section_strips_hallucinated_citations():
    provider = TaskProvider(section_text="真实 [1]，编造 [42]。")
    client = make_client(provider)
    section = await generate_section("proj", "主题", BRIEFS[:2], client=client)
    assert "[42]" not in section.content
    assert section.invalid_citations == [42]


@pytest.mark.asyncio
async def test_generate_section_empty_briefs():
    client = make_client(TaskProvider())
    section = await generate_section("proj", "空节", [], client=client)
    assert section.content == ""
    assert section.paper_ids == []


@pytest.mark.asyncio
async def test_generate_section_prompt_contains_only_section_papers():
    provider = TaskProvider(section_text="内容 [1]。")
    client = make_client(provider)
    await generate_section("proj", "主题", BRIEFS[:2], client=client)
    prompt = provider.calls[0].messages[0].content
    assert "论文 1" in prompt
    assert "论文 2" in prompt
    # 不在本节的文献不应出现
    assert "论文 5" not in prompt


@pytest.mark.asyncio
async def test_generate_section_uses_rag_context():
    from app.parser.chunker import Chunk
    from app.rag import HashEmbedder, InMemoryVectorStore, index_chunks

    embedder = HashEmbedder(dim=64)
    store = InMemoryVectorStore()
    await index_chunks(
        "proj",
        "p1",
        [Chunk(text="detailed method description here", index=0, section="Method")],
        paper_title="论文 1",
        embedder=embedder,
        store=store,
    )
    provider = TaskProvider(section_text="内容 [1]。")
    client = make_client(provider)
    await generate_section(
        "proj", "detailed method", BRIEFS[:1], client=client, embedder=embedder, store=store
    )
    assert "相关原文片段" in provider.calls[0].messages[0].content


# ---------- 完整综述 ----------


@pytest.mark.asyncio
async def test_generate_review_end_to_end():
    graph = InMemoryCitationGraph()
    for src, targets in [("p1", ["p2", "p3"]), ("p2", ["p3"]), ("p4", ["p5", "p6"]), ("p5", ["p6"])]:
        await graph.add_citations("proj", src, targets)

    outline = json.dumps(
        {
            "sections": [
                {"cluster_id": 0, "title": "主题甲", "order": 1},
                {"cluster_id": 1, "title": "主题乙", "order": 2},
            ]
        },
        ensure_ascii=False,
    )
    provider = TaskProvider(outline=outline, section_text="本节论述 [1] 与 [2]。")
    client = make_client(provider)

    draft = await generate_review("proj", BRIEFS, client=client, graph=graph)
    assert len(draft.sections) == 2
    assert {s.title for s in draft.sections} == {"主题甲", "主题乙"}
    assert len(draft.references) == 6
    assert draft.word_count > 0


@pytest.mark.asyncio
async def test_generate_review_empty_briefs():
    client = make_client(TaskProvider())
    draft = await generate_review("proj", [], client=client)
    assert draft.sections == []
    assert draft.references == []


@pytest.mark.asyncio
async def test_generate_review_single_cluster():
    provider = TaskProvider(
        outline=json.dumps({"sections": [{"cluster_id": 0, "title": "综合评述", "order": 1}]}),
        section_text="综述内容 [1]。",
    )
    client = make_client(provider)
    draft = await generate_review("proj", BRIEFS[:3], client=client)
    assert len(draft.sections) == 1
    assert draft.sections[0].title == "综合评述"


# ---------- 渲染 ----------


def test_review_draft_markdown_renumbers_globally():
    draft = ReviewDraft(
        sections=[
            ReviewSection(title="第一节", content="内容 [1]。", paper_ids=["p1"]),
            # 第二节的 [1] 指向 p2，全局应变成 [2]
            ReviewSection(title="第二节", content="内容 [1]。", paper_ids=["p2"]),
        ],
        references=[brief("p1", "论文一"), brief("p2", "论文二")],
    )
    md = draft.to_markdown()
    assert "## 第一节" in md
    assert "## 参考文献" in md
    assert "[1] 论文一" in md
    assert "[2] 论文二" in md
    # 第二节引用被重编号为 [2]
    second_part = md.split("## 第二节")[1].split("## 参考文献")[0]
    assert "[2]" in second_part


def test_review_draft_markdown_multi_citation_group():
    draft = ReviewDraft(
        sections=[ReviewSection(title="节", content="见 [1,2]。", paper_ids=["p1", "p2"])],
        references=[brief("p1", "一"), brief("p2", "二")],
    )
    md = draft.to_markdown()
    assert "[1,2]" in md


def test_review_draft_bibtex():
    draft = ReviewDraft(references=[brief("p1", "Graph Neural Networks", year=2022)])
    bib = draft.to_bibtex()
    assert "@article{" in bib
    assert "Graph Neural Networks" in bib
    assert "2022" in bib


def test_review_draft_empty_renders_without_error():
    draft = ReviewDraft()
    assert "文献综述" in draft.to_markdown()
    assert draft.to_bibtex() == ""
