"""大纲生成：模板骨架 + LLM 填充要点。

结构由模板固定，LLM 只填 key_points 与篇幅估计，避免章节漂移。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger
from pydantic import BaseModel, Field

from app.llm import LLMClient, LLMRequest, Message, Role, TaskType, get_llm
from app.llm.prompts import render
from app.services.direction import PaperBrief
from app.services.templates import (
    OutlineTemplate,
    TemplateNode,
    flatten,
    get_template,
)


class SectionPoints(BaseModel):
    path: str
    key_points: list[str] = Field(default_factory=list)
    est_words: int = 400


class OutlinePointsResponse(BaseModel):
    sections: list[SectionPoints] = Field(default_factory=list)


@dataclass
class OutlineNode:
    """生成后的大纲节点，带层级与要点。"""

    title: str
    path: str
    type: str
    order: int
    level: int
    key_points: list[str] = field(default_factory=list)
    est_words: int = 400
    hint: str = ""
    children: list["OutlineNode"] = field(default_factory=list)


@dataclass
class DirectionInput:
    """选定的研究方向，作为大纲生成的依据。"""

    statement: str
    gap: str | None = None
    innovation: str | None = None
    method_sketch: str | None = None


def _build_tree(
    nodes: list[TemplateNode],
    points_by_path: dict[str, SectionPoints],
    parent_path: str = "",
    level: int = 1,
) -> list[OutlineNode]:
    out: list[OutlineNode] = []
    for index, node in enumerate(nodes):
        path = f"{parent_path} > {node.title}" if parent_path else node.title
        points = points_by_path.get(path)
        out.append(
            OutlineNode(
                title=node.title,
                path=path,
                type=node.type,
                order=index,
                level=level,
                key_points=list(points.key_points) if points else [],
                est_words=points.est_words if points else 400,
                hint=node.hint,
                children=_build_tree(node.children, points_by_path, path, level + 1),
            )
        )
    return out


def flatten_outline(nodes: list[OutlineNode]) -> list[OutlineNode]:
    """深度优先展开，顺序即阅读顺序。"""
    out: list[OutlineNode] = []
    for node in nodes:
        out.append(node)
        out.extend(flatten_outline(node.children))
    return out


async def generate_outline(
    title: str | None,
    template_key: str | None = None,
    direction: DirectionInput | None = None,
    briefs: list[PaperBrief] | None = None,
    language: str = "中文",
    discipline: str | None = None,
    client: LLMClient | None = None,
) -> tuple[list[OutlineNode], OutlineTemplate]:
    """生成大纲。LLM 失败时返回仅有骨架、要点为空的大纲，不阻塞流程。"""
    template = get_template(template_key)
    flat = flatten(template.nodes)

    sections_payload = [
        {"path": path, "type": node.type, "hint": node.hint} for path, node in flat
    ]
    papers_payload = [
        {
            "title": b.title,
            "year": b.year,
            "one_line": b.summary.one_line if b.summary else None,
        }
        # 文献过多会挤占 prompt，取前 15 篇代表
        for b in (briefs or [])[:15]
    ]

    prompt = render(
        "outline",
        discipline=discipline,
        title=title,
        direction=(
            {
                "statement": direction.statement,
                "gap": direction.gap,
                "innovation": direction.innovation,
                "method_sketch": direction.method_sketch,
            }
            if direction
            else None
        ),
        sections=sections_payload,
        papers=papers_payload,
        language=language,
    )

    llm = client or get_llm()
    req = LLMRequest(
        messages=[Message(role=Role.USER, content=prompt)],
        task=TaskType.OUTLINE,
        temperature=0.4,
    )

    points_by_path: dict[str, SectionPoints] = {}
    try:
        response = await llm.complete_json(req, OutlinePointsResponse, retries=1)
    except Exception as exc:
        logger.warning("outline point generation failed, skeleton only: {}", exc)
    else:
        valid_paths = {path for path, _ in flat}
        for section in response.sections:
            if section.path in valid_paths:
                points_by_path[section.path] = section
            else:
                logger.warning("outline: dropping unknown path {!r}", section.path)

    tree = _build_tree(template.nodes, points_by_path)
    logger.info(
        "outline generated: template={} nodes={} with_points={}",
        template.key,
        len(flat),
        len(points_by_path),
    )
    return tree, template
