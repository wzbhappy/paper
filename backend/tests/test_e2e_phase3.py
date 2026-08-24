"""Phase 3 端到端测试：大纲生成 → 正文写作 → 质量检查 → 导出。"""

import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_p3.db")

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import engine
from app.llm import LLMClient, LLMRequest, set_llm
from app.llm.base import LLMResponse, Usage
from app.main import app
from app.models import Base
from app.rag import HashEmbedder, InMemoryVectorStore, set_embedder, set_vector_store
from app.services.templates import flatten, get_template


def outline_payload(template_key: str = "imrad") -> str:
    paths = [p for p, _ in flatten(get_template(template_key).nodes)]
    return json.dumps(
        {
            "sections": [
                {"path": p, "key_points": [f"{p} 要点一", f"{p} 要点二"], "est_words": 500}
                for p in paths
            ]
        },
        ensure_ascii=False,
    )


class TaskProvider:
    """按 prompt 特征返回内容。"""

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[LLMRequest] = []
        self.write_reply = "本节论述该问题的重要性 [1]。"

    async def complete(self, req: LLMRequest, model: str) -> LLMResponse:
        self.calls.append(req)
        prompt = req.messages[0].content
        if "章节骨架" in prompt:
            content = outline_payload()
        elif "本节位置" in prompt:
            content = self.write_reply
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


async def add_paper(client, pid: str, title: str = "基础工作") -> str:
    """通过检索导入接口造一篇 metadata_only 文献。"""
    resp = await client.post(
        f"/api/v1/projects/{pid}/search/import",
        json={
            "items": [
                {
                    "title": title,
                    "source": "crossref",
                    "authors": ["Alice Chen"],
                    "abstract": "这是一篇基础工作的摘要，用于测试引用。",
                    "year": 2020,
                    "doi": f"10.1/{title}",
                }
            ]
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["paper_ids"][0]


# ---------- 模板与大纲 ----------


@pytest.mark.asyncio
async def test_list_templates(client):
    pid = await new_project(client)
    resp = await client.get(f"/api/v1/projects/{pid}/outline/templates")
    assert resp.status_code == 200
    keys = {t["key"] for t in resp.json()}
    assert {"imrad", "review", "engineering", "thesis"} <= keys


@pytest.mark.asyncio
async def test_generate_outline_creates_hierarchy(client):
    pid = await new_project(client)
    resp = await client.post(
        f"/api/v1/projects/{pid}/outline/generate", json={"template": "imrad"}
    )
    assert resp.status_code == 201, resp.text
    sections = resp.json()

    expected = len(flatten(get_template("imrad").nodes))
    assert len(sections) == expected
    assert all(s["key_points"] for s in sections)
    assert all(s["template"] == "imrad" for s in sections)

    # 层级关系正确：子节点有 parent_id 且 level 更深
    children = [s for s in sections if s["parent_id"]]
    assert children
    by_id = {s["id"]: s for s in sections}
    for child in children:
        parent = by_id[child["parent_id"]]
        assert child["level"] == parent["level"] + 1
        assert child["path"].startswith(parent["path"] + " > ")


@pytest.mark.asyncio
async def test_generate_outline_uses_selected_direction(client):
    pid = await new_project(client)
    await add_paper(client, pid)

    # 先造一个已采纳的方向
    await client.post(
        f"/api/v1/projects/{pid}/directions/generate", json={"n": 1}
    )
    directions = (await client.get(f"/api/v1/projects/{pid}/directions")).json()
    if directions:
        await client.patch(
            f"/api/v1/projects/{pid}/directions/{directions[0]['id']}",
            json={"selected": True},
        )

    client.provider.calls.clear()
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={})
    outline_prompt = next(
        c.messages[0].content for c in client.provider.calls if "章节骨架" in c.messages[0].content
    )
    if directions:
        assert "选定的研究方向" in outline_prompt


@pytest.mark.asyncio
async def test_regenerate_outline_replaces(client):
    pid = await new_project(client)
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={"template": "imrad"})
    first = (await client.get(f"/api/v1/projects/{pid}/outline")).json()

    await client.post(
        f"/api/v1/projects/{pid}/outline/generate",
        json={"template": "review", "replace": True},
    )
    second = (await client.get(f"/api/v1/projects/{pid}/outline")).json()
    assert len(second) == len(flatten(get_template("review").nodes))
    assert {s["id"] for s in first}.isdisjoint({s["id"] for s in second})


@pytest.mark.asyncio
async def test_add_and_delete_section(client):
    pid = await new_project(client)
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={})
    sections = (await client.get(f"/api/v1/projects/{pid}/outline")).json()
    parent = next(s for s in sections if s["level"] == 1)

    added = await client.post(
        f"/api/v1/projects/{pid}/outline",
        json={"title": "补充小节", "parent_id": parent["id"], "key_points": ["要点"]},
    )
    assert added.status_code == 201
    body = added.json()
    assert body["level"] == parent["level"] + 1
    assert body["path"] == f"{parent['path']} > 补充小节"

    deleted = await client.delete(f"/api/v1/projects/{pid}/outline/{body['id']}")
    assert deleted.status_code == 204
    remaining = (await client.get(f"/api/v1/projects/{pid}/outline")).json()
    assert body["id"] not in {s["id"] for s in remaining}


@pytest.mark.asyncio
async def test_rename_section_updates_descendant_paths(client):
    pid = await new_project(client)
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={"template": "imrad"})
    sections = (await client.get(f"/api/v1/projects/{pid}/outline")).json()
    parent = next(s for s in sections if s["path"] == "引言")

    resp = await client.patch(
        f"/api/v1/projects/{pid}/outline/{parent['id']}", json={"title": "绪论"}
    )
    assert resp.status_code == 200
    assert resp.json()["path"] == "绪论"

    updated = (await client.get(f"/api/v1/projects/{pid}/outline")).json()
    descendants = [s for s in updated if s["path"].startswith("绪论 > ")]
    assert descendants
    assert not any(s["path"].startswith("引言") for s in updated)


@pytest.mark.asyncio
async def test_delete_section_cascades_to_children(client):
    pid = await new_project(client)
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={"template": "imrad"})
    sections = (await client.get(f"/api/v1/projects/{pid}/outline")).json()
    parent = next(s for s in sections if s["path"] == "引言")
    child_count = len([s for s in sections if s["path"].startswith("引言 > ")])
    assert child_count > 0

    await client.delete(f"/api/v1/projects/{pid}/outline/{parent['id']}")
    remaining = (await client.get(f"/api/v1/projects/{pid}/outline")).json()
    assert not any(s["path"].startswith("引言") for s in remaining)


# ---------- 正文写作 ----------


@pytest.mark.asyncio
async def test_manuscript_lists_empty_sections(client):
    pid = await new_project(client)
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={})
    resp = await client.get(f"/api/v1/projects/{pid}/manuscript")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == len(flatten(get_template("imrad").nodes))
    assert all(s["content"] == "" for s in body)


@pytest.mark.asyncio
async def test_ai_draft_applies_content(client):
    pid = await new_project(client)
    await add_paper(client, pid)
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={})
    sections = (await client.get(f"/api/v1/projects/{pid}/outline")).json()
    target = next(s for s in sections if s["path"] == "引言")

    resp = await client.post(
        f"/api/v1/projects/{pid}/manuscript/{target['id']}/ai",
        json={"action": "draft", "apply": True, "target_words": 300},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied"] is True
    assert body["action"] == "draft"
    assert "[1]" in body["content"]
    assert len(body["paper_ids"]) == 1

    manuscript = (await client.get(f"/api/v1/projects/{pid}/manuscript")).json()
    written = next(s for s in manuscript if s["outline_section_id"] == target["id"])
    assert written["content"] == body["content"]
    assert written["ai_generated"] is True
    assert written["source_paper_ids"] == body["paper_ids"]


@pytest.mark.asyncio
async def test_ai_action_without_apply_does_not_persist(client):
    pid = await new_project(client)
    await add_paper(client, pid)
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={})
    sections = (await client.get(f"/api/v1/projects/{pid}/outline")).json()
    target = sections[0]

    resp = await client.post(
        f"/api/v1/projects/{pid}/manuscript/{target['id']}/ai",
        json={"action": "draft", "apply": False},
    )
    assert resp.json()["applied"] is False

    manuscript = (await client.get(f"/api/v1/projects/{pid}/manuscript")).json()
    written = next(s for s in manuscript if s["outline_section_id"] == target["id"])
    assert written["content"] == ""


@pytest.mark.asyncio
async def test_polish_requires_selection(client):
    pid = await new_project(client)
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={})
    sections = (await client.get(f"/api/v1/projects/{pid}/outline")).json()

    resp = await client.post(
        f"/api/v1/projects/{pid}/manuscript/{sections[0]['id']}/ai",
        json={"action": "polish"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_unknown_action_rejected(client):
    pid = await new_project(client)
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={})
    sections = (await client.get(f"/api/v1/projects/{pid}/outline")).json()
    resp = await client.post(
        f"/api/v1/projects/{pid}/manuscript/{sections[0]['id']}/ai",
        json={"action": "hallucinate"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_manual_save_clears_ai_flag(client):
    pid = await new_project(client)
    await add_paper(client, pid)
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={})
    sections = (await client.get(f"/api/v1/projects/{pid}/outline")).json()
    target = sections[0]

    await client.post(
        f"/api/v1/projects/{pid}/manuscript/{target['id']}/ai",
        json={"action": "draft", "apply": True},
    )
    resp = await client.put(
        f"/api/v1/projects/{pid}/manuscript/{target['id']}",
        json={"content": "我自己写的内容。", "status": "done"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == "我自己写的内容。"
    assert body["ai_generated"] is False
    assert body["status"] == "done"
    assert body["word_count"] == len("我自己写的内容。")


@pytest.mark.asyncio
async def test_outline_reports_word_count(client):
    pid = await new_project(client)
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={})
    sections = (await client.get(f"/api/v1/projects/{pid}/outline")).json()
    target = sections[0]

    await client.put(
        f"/api/v1/projects/{pid}/manuscript/{target['id']}",
        json={"content": "一二三四五"},
    )
    updated = (await client.get(f"/api/v1/projects/{pid}/outline")).json()
    written = next(s for s in updated if s["id"] == target["id"])
    assert written["word_count"] == 5
    assert written["has_content"] is True


# ---------- 质量检查与导出 ----------


@pytest.mark.asyncio
async def test_quality_check_reports_empty_chapters(client):
    pid = await new_project(client)
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={})
    resp = await client.get(f"/api/v1/projects/{pid}/manuscript/quality")
    assert resp.status_code == 200
    body = resp.json()
    assert body["section_count"] > 0
    assert any(i["kind"] == "empty_section" for i in body["issues"])


@pytest.mark.asyncio
async def test_quality_check_counts_ai_sections(client):
    pid = await new_project(client)
    await add_paper(client, pid)
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={})
    sections = (await client.get(f"/api/v1/projects/{pid}/outline")).json()
    await client.post(
        f"/api/v1/projects/{pid}/manuscript/{sections[0]['id']}/ai",
        json={"action": "draft", "apply": True},
    )
    body = (await client.get(f"/api/v1/projects/{pid}/manuscript/quality")).json()
    assert body["ai_generated_sections"] == 1
    assert body["reference_count"] == 1


@pytest.mark.asyncio
async def test_export_markdown(client):
    pid = await new_project(client)
    await add_paper(client, pid)
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={})
    sections = (await client.get(f"/api/v1/projects/{pid}/outline")).json()
    await client.post(
        f"/api/v1/projects/{pid}/manuscript/{sections[0]['id']}/ai",
        json={"action": "draft", "apply": True},
    )

    resp = await client.get(f"/api/v1/projects/{pid}/manuscript/export?format=markdown")
    assert resp.status_code == 200
    assert "text/markdown" in resp.headers["content-type"]
    assert "attachment" in resp.headers["content-disposition"]
    text = resp.text
    assert "# 引文网络研究" in text
    assert "## 参考文献" in text
    assert "人工智能辅助" in text


@pytest.mark.asyncio
async def test_export_markdown_without_disclosure(client):
    pid = await new_project(client)
    await add_paper(client, pid)
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={})
    sections = (await client.get(f"/api/v1/projects/{pid}/outline")).json()
    await client.post(
        f"/api/v1/projects/{pid}/manuscript/{sections[0]['id']}/ai",
        json={"action": "draft", "apply": True},
    )
    resp = await client.get(
        f"/api/v1/projects/{pid}/manuscript/export?format=markdown&disclosure=false"
    )
    assert "人工智能辅助" not in resp.text


@pytest.mark.asyncio
async def test_export_latex(client):
    pid = await new_project(client)
    await add_paper(client, pid)
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={})
    sections = (await client.get(f"/api/v1/projects/{pid}/outline")).json()
    await client.post(
        f"/api/v1/projects/{pid}/manuscript/{sections[0]['id']}/ai",
        json={"action": "draft", "apply": True},
    )

    resp = await client.get(f"/api/v1/projects/{pid}/manuscript/export?format=latex")
    assert resp.status_code == 200
    text = resp.text
    assert r"\documentclass" in text
    assert r"\end{document}" in text
    assert r"\cite{" in text


@pytest.mark.asyncio
async def test_export_bibtex(client):
    pid = await new_project(client)
    await add_paper(client, pid)
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={})
    sections = (await client.get(f"/api/v1/projects/{pid}/outline")).json()
    await client.post(
        f"/api/v1/projects/{pid}/manuscript/{sections[0]['id']}/ai",
        json={"action": "draft", "apply": True},
    )
    resp = await client.get(f"/api/v1/projects/{pid}/manuscript/export?format=bibtex")
    assert resp.status_code == 200
    assert "@article{" in resp.text


@pytest.mark.asyncio
async def test_export_docx(client):
    pid = await new_project(client)
    await client.post(f"/api/v1/projects/{pid}/outline/generate", json={})
    resp = await client.get(f"/api/v1/projects/{pid}/manuscript/export?format=docx")
    assert resp.status_code == 200
    assert resp.content[:2] == b"PK"
    assert "wordprocessingml" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_export_rejects_unknown_format(client):
    pid = await new_project(client)
    resp = await client.get(f"/api/v1/projects/{pid}/manuscript/export?format=pdf")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_export_empty_project(client):
    pid = await new_project(client)
    resp = await client.get(f"/api/v1/projects/{pid}/manuscript/export")
    assert resp.status_code == 200
    assert "# 引文网络研究" in resp.text


@pytest.mark.asyncio
async def test_outline_section_404_for_wrong_project(client):
    pid_a = await new_project(client)
    pid_b = await new_project(client)
    await client.post(f"/api/v1/projects/{pid_a}/outline/generate", json={})
    sections = (await client.get(f"/api/v1/projects/{pid_a}/outline")).json()

    resp = await client.put(
        f"/api/v1/projects/{pid_b}/manuscript/{sections[0]['id']}",
        json={"content": "越权写入"},
    )
    assert resp.status_code == 404
