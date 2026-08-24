"""Phase 4 端到端测试：热点分析、完整质量检查、进展汇总。"""

import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_p4.db")

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import engine
from app.llm import LLMClient, LLMRequest, set_llm
from app.llm.base import LLMResponse, Usage
from app.main import app
from app.models import Base
from app.rag import HashEmbedder, InMemoryVectorStore, set_embedder, set_vector_store
from app.services.templates import flatten, get_template

GAPS = json.dumps(
    {
        "gaps": [
            {
                "statement": "动态引文网络上的对比学习尚未被系统验证",
                "reason": "现有工作集中在静态图",
                "signal": "missing_intersection",
                "difficulty": 0.6,
                "evidence_indices": [1, 2],
            }
        ]
    },
    ensure_ascii=False,
)


def outline_payload() -> str:
    paths = [p for p, _ in flatten(get_template("imrad").nodes)]
    return json.dumps(
        {
            "sections": [
                {"path": p, "key_points": [f"{p} 要点"], "est_words": 400} for p in paths
            ]
        },
        ensure_ascii=False,
    )


class TaskProvider:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[LLMRequest] = []

    async def complete(self, req: LLMRequest, model: str) -> LLMResponse:
        self.calls.append(req)
        prompt = req.messages[0].content
        if "关键词趋势" in prompt:
            content = GAPS
        elif "章节骨架" in prompt:
            content = outline_payload()
        elif "本节位置" in prompt:
            content = "已有研究表明该方向具有价值 [1]。"
        else:
            content = "{}"
        return LLMResponse(
            content=content, model=model, usage=Usage(prompt_tokens=1, completion_tokens=1)
        )

    async def stream(self, req: LLMRequest, model: str) -> AsyncIterator[str]:
        yield ""


@pytest.fixture
async def client():
    provider = TaskProvider()
    llm = LLMClient()
    llm._provider_for = lambda model: provider  # type: ignore[method-assign]
    set_llm(llm)
    set_embedder(HashEmbedder(dim=64))
    set_vector_store(InMemoryVectorStore())

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        ac.provider = provider  # type: ignore[attr-defined]
        yield ac
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    set_llm(None)
    set_embedder(None)
    set_vector_store(None)


async def new_project(client) -> str:
    resp = await client.post(
        "/api/v1/projects", json={"title": "引文网络研究", "discipline": "计算机科学"}
    )
    return resp.json()["id"]


async def import_papers(client, pid: str, count: int = 4) -> list[str]:
    """导入若干带关键术语的文献。"""
    term_sets = [
        ["图神经网络", "对比学习"],
        ["图神经网络", "引文网络"],
        ["图神经网络", "对比学习"],
        ["引文网络", "时序建模"],
    ]
    items = []
    for i in range(count):
        items.append(
            {
                "title": f"论文 {i}",
                "source": "crossref",
                "authors": ["Alice Chen"],
                "abstract": f"第 {i} 篇论文的摘要内容。",
                "year": 2024 - (i % 3),
                "doi": f"10.1/paper{i}",
            }
        )
    resp = await client.post(f"/api/v1/projects/{pid}/search/import", json={"items": items})
    assert resp.status_code == 201, resp.text
    paper_ids = resp.json()["paper_ids"]

    # 直接写入结构化摘要，模拟已完成 LLM 摘要的状态
    import uuid as uuid_mod

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Paper

    async with SessionLocal() as session:
        for pid_str, terms in zip(paper_ids, term_sets):
            paper = (
                await session.execute(
                    select(Paper).where(Paper.id == uuid_mod.UUID(pid_str))
                )
            ).scalar_one()
            paper.summary = {
                "one_line": f"{paper.title} 的一句话总结",
                "problem": "某问题",
                "method": "某方法",
                "key_terms": terms,
                "limitations": ["缺少动态图验证"],
                "future_work": [],
                "metrics": {},
            }
        await session.commit()
    return paper_ids


# ---------- 热点分析 ----------


@pytest.mark.asyncio
async def test_hotspot_requires_papers(client):
    pid = await new_project(client)
    resp = await client.post(f"/api/v1/projects/{pid}/hotspot", json={})
    assert resp.status_code == 400
    assert "no papers" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_hotspot_returns_trends_and_gaps(client):
    pid = await new_project(client)
    await import_papers(client, pid)

    resp = await client.post(
        f"/api/v1/projects/{pid}/hotspot",
        json={"seed_keywords": ["图神经网络"], "n": 1},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["total_papers"] == 4
    assert body["papers_with_terms"] == 4
    assert body["year_from"] and body["year_to"]
    assert body["seed_keywords"] == ["图神经网络"]

    terms = {t["term"] for t in body["trends"]}
    assert "图神经网络" in terms
    top = next(t for t in body["trends"] if t["term"] == "图神经网络")
    assert top["count"] == 3
    assert top["trend"] in ("rising", "stable", "declining", "unknown")

    assert body["cooccurrence"]
    assert "缺少动态图验证" in body["limitations"]

    assert len(body["gaps"]) == 1
    gap = body["gaps"][0]
    assert gap["signal"] == "missing_intersection"
    assert len(gap["evidence_paper_ids"]) == 2
    assert gap["evidence_titles"]


@pytest.mark.asyncio
async def test_hotspot_stats_only_with_few_papers(client):
    pid = await new_project(client)
    await import_papers(client, pid, count=2)
    resp = await client.post(f"/api/v1/projects/{pid}/hotspot", json={})
    body = resp.json()
    assert body["total_papers"] == 2
    # 数据不足时不做 gap 推断
    assert body["gaps"] == []


# ---------- 完整质量检查 ----------


@pytest.mark.asyncio
async def test_quality_report_includes_severity_and_kinds(client):
    pid = await new_project(client)
    await import_papers(client, pid)
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={})
    sections = (await client.get(f"/api/v1/projects/{pid}/outline")).json()

    # 在「相关工作」写一段无引用且口语化的文字
    related = next(s for s in sections if "相关工作" in s["path"])
    await client.put(
        f"/api/v1/projects/{pid}/manuscript/{related['id']}",
        json={"content": "已有研究讨论了这个问题，我们觉得效果挺好。"},
    )

    resp = await client.get(f"/api/v1/projects/{pid}/manuscript/quality")
    assert resp.status_code == 200
    body = resp.json()

    assert body["error_count"] >= 1
    assert body["warning_count"] >= 1
    assert body["empty_sections"] > 0
    kinds = body["kind_counts"]
    assert "missing_citation" in kinds
    assert "informal_language" in kinds

    # 错误应排在前面
    severities = [i["severity"] for i in body["issues"]]
    assert severities[0] == "error"
    assert all("section" in i and "detail" in i for i in body["issues"])


@pytest.mark.asyncio
async def test_quality_detects_duplicate_across_sections(client):
    pid = await new_project(client)
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={})
    sections = (await client.get(f"/api/v1/projects/{pid}/outline")).json()
    duplicate = "本文提出的两阶段编码器在稀疏监督场景下具有显著的性能优势。"

    for section in sections[:2]:
        await client.put(
            f"/api/v1/projects/{pid}/manuscript/{section['id']}",
            json={"content": duplicate},
        )

    body = (await client.get(f"/api/v1/projects/{pid}/manuscript/quality")).json()
    assert "duplicate_sentence" in body["kind_counts"]


@pytest.mark.asyncio
async def test_quality_clean_manuscript_has_no_errors(client):
    pid = await new_project(client)
    await import_papers(client, pid)
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={})
    sections = (await client.get(f"/api/v1/projects/{pid}/outline")).json()

    # 给每个章节写规范内容；含引用的章节用 AI 动作以建立引用关系
    for section in sections:
        if any(k in section["path"] for k in ("引言", "相关工作", "绪论", "背景")):
            await client.post(
                f"/api/v1/projects/{pid}/manuscript/{section['id']}/ai",
                json={"action": "draft", "apply": True},
            )
        else:
            await client.put(
                f"/api/v1/projects/{pid}/manuscript/{section['id']}",
                json={"content": "本节论述该部分的具体设计与实现细节。"},
            )

    body = (await client.get(f"/api/v1/projects/{pid}/manuscript/quality")).json()
    assert body["error_count"] == 0
    assert body["empty_sections"] == 0


# ---------- 进展汇总 ----------


@pytest.mark.asyncio
async def test_progress_empty_project(client):
    pid = await new_project(client)
    resp = await client.get(f"/api/v1/projects/{pid}/progress")
    assert resp.status_code == 200
    body = resp.json()
    assert body["suggested_stage"] == "discovery"
    assert body["completion"] == 0.0
    assert body["paper_count"] == 0
    assert len(body["stages"]) == 8
    assert body["next_action"]


@pytest.mark.asyncio
async def test_progress_tracks_paper_import(client):
    pid = await new_project(client)
    await import_papers(client, pid)
    body = (await client.get(f"/api/v1/projects/{pid}/progress")).json()
    assert body["paper_count"] == 4
    assert body["summarized_count"] == 4
    assert body["completion"] > 0
    discovery = next(s for s in body["stages"] if s["key"] == "discovery")
    assert discovery["done"] is True


@pytest.mark.asyncio
async def test_progress_tracks_outline_and_writing(client):
    pid = await new_project(client)
    await import_papers(client, pid)
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={})
    sections = (await client.get(f"/api/v1/projects/{pid}/outline")).json()
    await client.put(
        f"/api/v1/projects/{pid}/manuscript/{sections[0]['id']}",
        json={"content": "第一节的正文内容。"},
    )

    body = (await client.get(f"/api/v1/projects/{pid}/progress")).json()
    assert body["outline_section_count"] == len(sections)
    assert body["written_section_count"] == 1
    assert body["total_word_count"] == len("第一节的正文内容。")
    outline_stage = next(s for s in body["stages"] if s["key"] == "outline")
    assert outline_stage["done"] is True


@pytest.mark.asyncio
async def test_progress_reflects_selected_direction(client):
    pid = await new_project(client)
    await import_papers(client, pid)
    await client.post(f"/api/v1/projects/{pid}/directions/generate", json={"n": 1})
    directions = (await client.get(f"/api/v1/projects/{pid}/directions")).json()

    before = (await client.get(f"/api/v1/projects/{pid}/progress")).json()
    assert before["has_selected_direction"] is False

    if directions:
        await client.patch(
            f"/api/v1/projects/{pid}/directions/{directions[0]['id']}",
            json={"selected": True},
        )
        after = (await client.get(f"/api/v1/projects/{pid}/progress")).json()
        assert after["has_selected_direction"] is True


@pytest.mark.asyncio
async def test_progress_preserves_current_stage(client):
    pid = await new_project(client)
    await client.patch(f"/api/v1/projects/{pid}", json={"stage": "writing"})
    body = (await client.get(f"/api/v1/projects/{pid}/progress")).json()
    assert body["current_stage"] == "writing"
    assert body["suggested_stage"] == "discovery"


@pytest.mark.asyncio
async def test_progress_counts_quality_errors(client):
    pid = await new_project(client)
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={})
    sections = (await client.get(f"/api/v1/projects/{pid}/outline")).json()
    related = next(s for s in sections if "相关工作" in s["path"])
    await client.put(
        f"/api/v1/projects/{pid}/manuscript/{related['id']}",
        json={"content": "已有研究讨论了该问题但此处没有任何引用标记。"},
    )
    body = (await client.get(f"/api/v1/projects/{pid}/progress")).json()
    assert body["quality_error_count"] >= 1


@pytest.mark.asyncio
async def test_progress_unknown_project_404(client):
    fake = "00000000-0000-0000-0000-000000000000"
    assert (await client.get(f"/api/v1/projects/{fake}/progress")).status_code == 404
