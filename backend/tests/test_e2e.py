"""端到端测试：上传 PDF → 解析 → 摘要 → 入库 → 生成方向 → 采纳。

LLM 与 embedding 全部替换为可控假实现，不打网络、不依赖容器。
"""

import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_e2e.db")

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import engine
from app.llm import LLMClient, LLMRequest, set_llm
from app.llm.base import LLMResponse, Usage
from app.main import app
from app.models import Base
from app.rag import HashEmbedder, InMemoryVectorStore, set_embedder, set_vector_store

SUMMARY_JSON = json.dumps(
    {
        "one_line": "提出两阶段编码器用于引文图表示学习",
        "problem": "稀疏监督下引文图表示学习效果差",
        "method": "两阶段编码器",
        "dataset": "Cora",
        "metrics": {"accuracy": "84.3%"},
        "conclusion": "优于基线",
        "limitations": ["未在大规模图验证"],
        "future_work": ["扩展到动态图"],
        "key_terms": ["图神经网络", "引文网络"],
    },
    ensure_ascii=False,
)

DIRECTIONS_JSON = json.dumps(
    {
        "directions": [
            {
                "statement": "在动态引文网络上验证两阶段编码器",
                "gap": "现有工作仅在静态图上验证",
                "innovation": "引入时序建模",
                "method_sketch": "时序图 + 两阶段编码",
                "feasibility": 0.8,
                "novelty": 0.7,
                "evidence_indices": [1],
            }
        ]
    },
    ensure_ascii=False,
)


class ScriptedProvider:
    """按任务类型返回预设 JSON，与调用顺序无关，更稳健。"""

    name = "scripted"

    def __init__(self) -> None:
        self.calls: list[LLMRequest] = []

    async def complete(self, req: LLMRequest, model: str) -> LLMResponse:
        self.calls.append(req)
        task = req.task.value
        if task == "summarize":
            content = SUMMARY_JSON
        elif task == "direction":
            content = DIRECTIONS_JSON
        else:
            content = "{}"
        return LLMResponse(
            content=content, model=model, usage=Usage(prompt_tokens=1, completion_tokens=1)
        )

    async def stream(self, req: LLMRequest, model: str) -> AsyncIterator[str]:
        yield ""


def make_pdf(path: Path) -> bool:
    try:
        import fitz
    except ImportError:
        return False

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 90), "Two Stage Encoder for Citation Graphs", fontsize=22)
    page.insert_text((72, 130), "Alice Chen, Bob Smith", fontsize=11)
    page.insert_text((72, 170), "Abstract", fontsize=15)
    y = 200
    for line in [
        "We propose a two stage encoder for citation graph representation learning",
        "under sparse supervision. Experiments on Cora show consistent gains over",
        "strong baselines across three benchmark datasets and ablation settings.",
    ]:
        page.insert_text((72, y), line, fontsize=11)
        y += 20
    page.insert_text((72, y + 20), "1 Introduction", fontsize=15)
    y += 50
    for line in [
        "Citation networks are widely used for scientific impact analysis and",
        "recommendation. Prior work assumes dense label availability, which rarely",
        "holds in practice for newly published papers in emerging research areas.",
    ]:
        page.insert_text((72, y), line, fontsize=11)
        y += 20
    doc.save(path)
    doc.close()
    return True


@pytest.fixture
async def client(tmp_path, monkeypatch):
    # 文献存储指向临时目录，避免污染工作区
    monkeypatch.setattr("app.services.ingest.settings.storage_dir", str(tmp_path / "papers"))

    provider = ScriptedProvider()
    llm = LLMClient()
    llm._provider_for = lambda model: provider  # type: ignore[method-assign]
    set_llm(llm)

    embedder = HashEmbedder(dim=128)
    store = InMemoryVectorStore()
    set_embedder(embedder)
    set_vector_store(store)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        ac.provider = provider  # type: ignore[attr-defined]
        ac.store = store  # type: ignore[attr-defined]
        yield ac

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    set_llm(None)
    set_embedder(None)
    set_vector_store(None)


async def create_project(client) -> str:
    resp = await client.post(
        "/api/v1/projects", json={"title": "引文图研究", "discipline": "计算机科学"}
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def upload_pdf(client, project_id: str, tmp_path: Path) -> dict:
    pdf = tmp_path / "paper.pdf"
    if not make_pdf(pdf):
        pytest.skip("pymupdf not installed")
    with pdf.open("rb") as fh:
        resp = await client.post(
            f"/api/v1/projects/{project_id}/papers",
            files={"file": ("paper.pdf", fh, "application/pdf")},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_full_pipeline(client, tmp_path):
    project_id = await create_project(client)
    paper = await upload_pdf(client, project_id, tmp_path)

    # BackgroundTasks 在 ASGITransport 下会在响应后同步执行完
    detail = (
        await client.get(f"/api/v1/projects/{project_id}/papers/{paper['id']}")
    ).json()
    assert detail["status"] == "ready", detail
    assert detail["title"] is not None
    assert "Two Stage Encoder" in detail["title"]
    assert detail["authors"] is not None
    assert detail["abstract"] is not None
    assert detail["summary"]["one_line"].startswith("提出两阶段编码器")
    assert detail["summary"]["metrics"] == {"accuracy": "84.3%"}
    assert detail["bibtex"] is not None and "@article" in detail["bibtex"]
    assert detail["chunk_count"] > 0

    # 向量确实入库
    assert await client.store.count({"project_id": project_id}) == detail["chunk_count"]

    # 生成方向
    gen = await client.post(
        f"/api/v1/projects/{project_id}/directions/generate",
        json={"n": 1, "intent": "动态图场景"},
    )
    assert gen.status_code == 202, gen.text
    job_id = gen.json()["id"]

    job = (await client.get(f"/api/v1/projects/{project_id}/jobs/{job_id}")).json()
    assert job["status"] == "done", job
    assert job["progress"] == 1.0
    assert job["result"]["count"] == 1

    directions = (
        await client.get(f"/api/v1/projects/{project_id}/directions")
    ).json()
    assert len(directions) == 1
    d = directions[0]
    assert d["evidence_paper_ids"] == [paper["id"]]
    assert d["feasibility"] == pytest.approx(0.8)
    assert d["selected"] is False

    # 采纳方向
    patched = await client.patch(
        f"/api/v1/projects/{project_id}/directions/{d['id']}",
        json={"selected": True, "feedback": "就做这个"},
    )
    assert patched.status_code == 200
    assert patched.json()["selected"] is True
    assert patched.json()["feedback"] == "就做这个"


@pytest.mark.asyncio
async def test_direction_generate_requires_parsed_papers(client):
    project_id = await create_project(client)
    resp = await client.post(
        f"/api/v1/projects/{project_id}/directions/generate", json={"n": 3}
    )
    assert resp.status_code == 400
    assert "no parsed papers" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_upload_rejects_non_pdf(client):
    project_id = await create_project(client)
    resp = await client.post(
        f"/api/v1/projects/{project_id}/papers",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_upload_rejects_empty_file(client):
    project_id = await create_project(client)
    resp = await client.post(
        f"/api/v1/projects/{project_id}/papers",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_paper_list_filters(client, tmp_path):
    project_id = await create_project(client)
    await upload_pdf(client, project_id, tmp_path)

    ready = (
        await client.get(f"/api/v1/projects/{project_id}/papers?status=ready")
    ).json()
    assert len(ready) == 1

    missing = (
        await client.get(f"/api/v1/projects/{project_id}/papers?status=failed")
    ).json()
    assert missing == []

    found = (
        await client.get(f"/api/v1/projects/{project_id}/papers?q=Two Stage")
    ).json()
    assert len(found) == 1


@pytest.mark.asyncio
async def test_reparse_creates_job(client, tmp_path):
    project_id = await create_project(client)
    paper = await upload_pdf(client, project_id, tmp_path)

    resp = await client.post(
        f"/api/v1/projects/{project_id}/papers/{paper['id']}/parse"
    )
    assert resp.status_code == 202
    job_id = resp.json()["id"]

    job = (await client.get(f"/api/v1/projects/{project_id}/jobs/{job_id}")).json()
    assert job["status"] == "done", job
    assert job["result"]["chunk_count"] > 0

    # 重新解析不应产生重复向量
    detail = (
        await client.get(f"/api/v1/projects/{project_id}/papers/{paper['id']}")
    ).json()
    assert await client.store.count({"project_id": project_id}) == detail["chunk_count"]


@pytest.mark.asyncio
async def test_delete_paper_removes_vectors(client, tmp_path):
    project_id = await create_project(client)
    paper = await upload_pdf(client, project_id, tmp_path)
    assert await client.store.count({"project_id": project_id}) > 0

    resp = await client.delete(f"/api/v1/projects/{project_id}/papers/{paper['id']}")
    assert resp.status_code == 204
    assert await client.store.count({"project_id": project_id}) == 0

    missing = await client.get(f"/api/v1/projects/{project_id}/papers/{paper['id']}")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_selecting_direction_deselects_others(client, tmp_path):
    project_id = await create_project(client)
    await upload_pdf(client, project_id, tmp_path)

    # 生成两批方向（replace=False 保留旧的）
    await client.post(
        f"/api/v1/projects/{project_id}/directions/generate",
        json={"n": 1, "replace": False},
    )
    await client.post(
        f"/api/v1/projects/{project_id}/directions/generate",
        json={"n": 1, "intent": "另一个角度", "replace": False},
    )
    directions = (
        await client.get(f"/api/v1/projects/{project_id}/directions")
    ).json()
    assert len(directions) == 2

    await client.patch(
        f"/api/v1/projects/{project_id}/directions/{directions[0]['id']}",
        json={"selected": True},
    )
    await client.patch(
        f"/api/v1/projects/{project_id}/directions/{directions[1]['id']}",
        json={"selected": True},
    )
    final = (await client.get(f"/api/v1/projects/{project_id}/directions")).json()
    selected = [d for d in final if d["selected"]]
    assert len(selected) == 1
    assert selected[0]["id"] == directions[1]["id"]


@pytest.mark.asyncio
async def test_project_stage_transitions(client):
    project_id = await create_project(client)
    resp = await client.patch(
        f"/api/v1/projects/{project_id}", json={"stage": "direction"}
    )
    assert resp.status_code == 200
    assert resp.json()["stage"] == "direction"

    bad = await client.patch(
        f"/api/v1/projects/{project_id}", json={"stage": "not_a_stage"}
    )
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_unknown_project_returns_404(client):
    fake = "00000000-0000-0000-0000-000000000000"
    assert (await client.get(f"/api/v1/projects/{fake}")).status_code == 404
    assert (await client.get(f"/api/v1/projects/{fake}/papers")).status_code == 404
    assert (await client.get(f"/api/v1/projects/{fake}/directions")).status_code == 404


@pytest.mark.asyncio
async def test_delete_project_cascades(client, tmp_path):
    project_id = await create_project(client)
    await upload_pdf(client, project_id, tmp_path)

    resp = await client.delete(f"/api/v1/projects/{project_id}")
    assert resp.status_code == 204
    assert (await client.get(f"/api/v1/projects/{project_id}")).status_code == 404
