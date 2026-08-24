"""大纲模板与生成测试。"""

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
from app.services.direction import PaperBrief
from app.services.outline import (
    DirectionInput,
    flatten_outline,
    generate_outline,
)
from app.services.summarize import PaperSummary
from app.services.templates import (
    TEMPLATES,
    flatten,
    get_template,
    list_templates,
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


# ---------- 模板 ----------


def test_all_templates_have_required_fields():
    for key, template in TEMPLATES.items():
        assert template.key == key
        assert template.name
        assert template.description
        assert template.nodes


def test_get_template_falls_back_to_default():
    assert get_template(None).key == "imrad"
    assert get_template("no_such_template").key == "imrad"
    assert get_template("review").key == "review"


def test_list_templates_shape():
    items = list_templates()
    assert len(items) == len(TEMPLATES)
    assert all({"key", "name", "description"} == set(i) for i in items)


def test_flatten_builds_hierarchical_paths():
    flat = flatten(get_template("imrad").nodes)
    paths = [p for p, _ in flat]
    assert "引言" in paths
    assert "引言 > 研究背景与意义" in paths
    # 父节点先于子节点
    assert paths.index("引言") < paths.index("引言 > 研究背景与意义")


def test_flatten_paths_are_unique():
    for key in TEMPLATES:
        paths = [p for p, _ in flatten(get_template(key).nodes)]
        assert len(paths) == len(set(paths)), f"duplicate path in template {key}"


def test_imrad_has_ablation_section():
    paths = [p for p, _ in flatten(get_template("imrad").nodes)]
    assert any("消融" in p for p in paths)


def test_engineering_template_has_performance_test():
    paths = [p for p, _ in flatten(get_template("engineering").nodes)]
    assert any("性能测试" in p for p in paths)


# ---------- 生成 ----------

BRIEFS = [
    PaperBrief(
        paper_id="p1",
        title="图神经网络综述",
        year=2023,
        summary=PaperSummary(one_line="综述 GNN 方法"),
    )
]

DIRECTION = DirectionInput(
    statement="在动态引文网络上引入对比学习预训练",
    gap="现有工作仅在静态图上验证",
    innovation="结合时序与对比学习",
    method_sketch="两阶段训练",
)


def points_payload(paths: list[str]) -> str:
    return json.dumps(
        {
            "sections": [
                {"path": p, "key_points": [f"{p} 的要点一", f"{p} 的要点二"], "est_words": 500}
                for p in paths
            ]
        },
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_generate_outline_fills_key_points():
    flat_paths = [p for p, _ in flatten(get_template("imrad").nodes)]
    client, provider = make_client(points_payload(flat_paths))

    tree, template = await generate_outline(
        "某论文题目", "imrad", direction=DIRECTION, briefs=BRIEFS, client=client
    )
    assert template.key == "imrad"
    flat = flatten_outline(tree)
    assert len(flat) == len(flat_paths)
    assert all(node.key_points for node in flat)
    assert all(node.est_words == 500 for node in flat)

    prompt = provider.calls[0].messages[0].content
    assert "在动态引文网络上引入对比学习预训练" in prompt
    assert "图神经网络综述" in prompt


@pytest.mark.asyncio
async def test_generate_outline_preserves_structure_on_llm_failure():
    client, _ = make_client("not json at all")
    tree, template = await generate_outline("题目", "imrad", client=client)
    flat = flatten_outline(tree)
    # 骨架完整，只是没有要点
    assert len(flat) == len(flatten(template.nodes))
    assert all(node.key_points == [] for node in flat)


@pytest.mark.asyncio
async def test_generate_outline_drops_hallucinated_paths():
    client, _ = make_client(
        json.dumps(
            {
                "sections": [
                    {"path": "引言", "key_points": ["真实路径"], "est_words": 300},
                    {"path": "模型凭空编造的章节", "key_points": ["假的"], "est_words": 300},
                ]
            },
            ensure_ascii=False,
        )
    )
    tree, _ = await generate_outline("题目", "imrad", client=client)
    flat = flatten_outline(tree)
    titles = [n.title for n in flat]
    assert "模型凭空编造的章节" not in titles
    intro = next(n for n in flat if n.path == "引言")
    assert intro.key_points == ["真实路径"]


@pytest.mark.asyncio
async def test_generate_outline_levels_and_order():
    client, _ = make_client("{}")
    tree, _ = await generate_outline("题目", "imrad", client=client)
    assert all(node.level == 1 for node in tree)
    assert [node.order for node in tree] == list(range(len(tree)))
    first_with_children = next(n for n in tree if n.children)
    assert all(child.level == 2 for child in first_with_children.children)


@pytest.mark.asyncio
async def test_generate_outline_unknown_template_uses_default():
    client, _ = make_client("{}")
    _tree, template = await generate_outline("题目", "bogus", client=client)
    assert template.key == "imrad"


@pytest.mark.asyncio
async def test_generate_outline_without_direction_or_papers():
    client, provider = make_client("{}")
    tree, _ = await generate_outline(None, "review", client=client)
    assert tree
    # 无题目时 prompt 应有占位而非崩溃
    assert "待定" in provider.calls[0].messages[0].content


@pytest.mark.asyncio
async def test_generate_outline_limits_papers_in_prompt():
    many = [
        PaperBrief(paper_id=f"p{i}", title=f"论文{i}", summary=PaperSummary(one_line="x"))
        for i in range(40)
    ]
    client, provider = make_client("{}")
    await generate_outline("题目", "imrad", briefs=many, client=client)
    prompt = provider.calls[0].messages[0].content
    assert "论文0" in prompt
    # 只取前 15 篇
    assert "论文39" not in prompt


def test_flatten_outline_is_depth_first():
    import asyncio

    client, _ = make_client("{}")
    tree, _ = asyncio.run(generate_outline("题目", "imrad", client=client))
    flat = flatten_outline(tree)
    intro_index = next(i for i, n in enumerate(flat) if n.path == "引言")
    child_index = next(
        i for i, n in enumerate(flat) if n.path == "引言 > 研究背景与意义"
    )
    related_index = next(i for i, n in enumerate(flat) if n.path == "相关工作")
    # 子节点紧跟父节点，且在下一个同级节点之前
    assert intro_index < child_index < related_index
