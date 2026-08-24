"""大纲 API：模板列表、生成、增删改。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_project
from app.db import get_session
from app.models import ManuscriptSection, OutlineSection, Paper, Project
from app.models import ResearchDirection as DirectionModel
from app.schemas import (
    OutlineGenerateRequest,
    OutlineSectionCreate,
    OutlineSectionOut,
    OutlineSectionUpdate,
    TemplateOut,
)
from app.services.direction import PaperBrief
from app.services.outline import DirectionInput, flatten_outline, generate_outline
from app.services.summarize import PaperSummary
from app.services.templates import list_templates

router = APIRouter()


def _to_brief(paper: Paper) -> PaperBrief:
    summary = None
    if paper.summary:
        try:
            summary = PaperSummary.model_validate(paper.summary)
        except Exception:
            summary = None
    if summary is None and paper.abstract:
        summary = PaperSummary(one_line=paper.abstract[:300])
    return PaperBrief(
        paper_id=str(paper.id), title=paper.title, year=paper.year, summary=summary
    )


async def _serialize(
    session: AsyncSession, sections: list[OutlineSection]
) -> list[OutlineSectionOut]:
    """附加正文字数，避免前端为每节单独请求。"""
    if not sections:
        return []
    manuscripts = (
        (
            await session.execute(
                select(ManuscriptSection).where(
                    ManuscriptSection.outline_section_id.in_([s.id for s in sections])
                )
            )
        )
        .scalars()
        .all()
    )
    by_section = {m.outline_section_id: m for m in manuscripts}

    out: list[OutlineSectionOut] = []
    for section in sections:
        manuscript = by_section.get(section.id)
        out.append(
            OutlineSectionOut(
                id=section.id,
                project_id=section.project_id,
                parent_id=section.parent_id,
                title=section.title,
                path=section.path,
                type=section.type,
                level=section.level,
                order=section.order,
                key_points=list(section.key_points or []),
                est_words=section.est_words,
                hint=section.hint,
                template=section.template,
                word_count=manuscript.word_count if manuscript else 0,
                has_content=bool(manuscript and manuscript.content.strip()),
            )
        )
    return out


@router.get("/templates", response_model=list[TemplateOut])
async def get_templates():
    return list_templates()


@router.get("", response_model=list[OutlineSectionOut])
async def list_sections(
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    """按层级与顺序返回全部章节，前端自行组装成树。"""
    sections = (
        (
            await session.execute(
                select(OutlineSection)
                .where(OutlineSection.project_id == project.id)
                .order_by(OutlineSection.level, OutlineSection.order, OutlineSection.path)
            )
        )
        .scalars()
        .all()
    )
    return await _serialize(session, sections)


@router.post("/generate", response_model=list[OutlineSectionOut], status_code=201)
async def generate(
    data: OutlineGenerateRequest,
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    """生成大纲。同步执行（单次 LLM 调用，通常几秒）。"""
    direction_row = (
        await session.execute(
            select(DirectionModel).where(
                DirectionModel.project_id == project.id,
                DirectionModel.selected.is_(True),
            )
        )
    ).scalar_one_or_none()

    direction = (
        DirectionInput(
            statement=direction_row.statement,
            gap=direction_row.gap,
            innovation=direction_row.innovation,
            method_sketch=direction_row.method_sketch,
        )
        if direction_row
        else None
    )

    papers = (
        (
            await session.execute(
                select(Paper).where(
                    Paper.project_id == project.id,
                    Paper.status.in_(("ready", "metadata_only")),
                )
            )
        )
        .scalars()
        .all()
    )

    tree, template = await generate_outline(
        project.title,
        data.template,
        direction=direction,
        briefs=[_to_brief(p) for p in papers],
        discipline=project.discipline,
    )

    if data.replace:
        # 级联会一并删除对应正文，前端已提示风险
        await session.execute(
            delete(OutlineSection).where(OutlineSection.project_id == project.id)
        )
        await session.commit()

    # 按层级顺序落库，父节点先插入以取得 id
    id_by_path: dict[str, uuid.UUID] = {}
    for node in flatten_outline(tree):
        parent_path = node.path.rsplit(" > ", 1)[0] if " > " in node.path else None
        row = OutlineSection(
            project_id=project.id,
            parent_id=id_by_path.get(parent_path) if parent_path else None,
            title=node.title,
            path=node.path,
            type=node.type,
            level=node.level,
            order=node.order,
            key_points=node.key_points,
            est_words=node.est_words,
            hint=node.hint or None,
            template=template.key,
        )
        session.add(row)
        await session.flush()
        id_by_path[node.path] = row.id

    await session.commit()
    return await list_sections(project=project, session=session)


async def _get_section(
    session: AsyncSession, project_id: uuid.UUID, section_id: uuid.UUID
) -> OutlineSection:
    section = (
        await session.execute(
            select(OutlineSection).where(
                OutlineSection.id == section_id,
                OutlineSection.project_id == project_id,
            )
        )
    ).scalar_one_or_none()
    if section is None:
        raise HTTPException(status_code=404, detail="outline section not found")
    return section


@router.post("", response_model=OutlineSectionOut, status_code=201)
async def add_section(
    data: OutlineSectionCreate,
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    """手工新增章节。parent_id 为空时作为顶层章节。"""
    parent = None
    if data.parent_id:
        parent = await _get_section(session, project.id, data.parent_id)

    siblings = (
        (
            await session.execute(
                select(OutlineSection).where(
                    OutlineSection.project_id == project.id,
                    OutlineSection.parent_id == data.parent_id,
                )
            )
        )
        .scalars()
        .all()
    )

    path = f"{parent.path} > {data.title}" if parent else data.title
    section = OutlineSection(
        project_id=project.id,
        parent_id=data.parent_id,
        title=data.title,
        path=path,
        type=data.type,
        level=(parent.level + 1) if parent else 1,
        order=len(siblings),
        key_points=data.key_points,
        est_words=data.est_words,
        template=parent.template if parent else None,
    )
    session.add(section)
    await session.commit()
    await session.refresh(section)
    return (await _serialize(session, [section]))[0]


@router.patch("/{section_id}", response_model=OutlineSectionOut)
async def update_section(
    section_id: uuid.UUID,
    data: OutlineSectionUpdate,
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    section = await _get_section(session, project.id, section_id)
    fields = data.model_dump(exclude_unset=True)

    if "title" in fields and fields["title"] != section.title:
        old_prefix = section.path
        new_path = (
            f"{old_prefix.rsplit(' > ', 1)[0]} > {fields['title']}"
            if " > " in old_prefix
            else fields["title"]
        )
        # 同步更新所有后代的 path
        descendants = (
            (
                await session.execute(
                    select(OutlineSection).where(
                        OutlineSection.project_id == project.id,
                        OutlineSection.path.startswith(f"{old_prefix} > "),
                    )
                )
            )
            .scalars()
            .all()
        )
        for child in descendants:
            child.path = new_path + child.path[len(old_prefix) :]
        section.path = new_path

    for key, value in fields.items():
        setattr(section, key, value)
    await session.commit()
    await session.refresh(section)
    return (await _serialize(session, [section]))[0]


@router.delete("/{section_id}", status_code=204)
async def delete_section(
    section_id: uuid.UUID,
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    """删除章节及其子章节和对应正文。"""
    section = await _get_section(session, project.id, section_id)
    await session.delete(section)
    await session.commit()
