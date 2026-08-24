"""正文 API：读写章节正文、AI 写作动作、质量检查、导出。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_project
from app.db import get_session
from app.models import ManuscriptSection, OutlineSection, Paper, Project
from app.rag import get_embedder, get_vector_store
from app.schemas import (
    CitationIssueOut,
    ManuscriptSectionOut,
    ManuscriptSectionSave,
    QualityReportOut,
    WriteActionRequest,
    WriteActionResponse,
)
from app.services.direction import PaperBrief
from app.services.export import (
    Manuscript,
    ManuscriptPart,
    Reference,
    check_citations,
    to_bibtex,
    to_docx_bytes,
    to_latex,
    to_markdown,
)
from app.services.summarize import PaperSummary
from app.services.write import WriteAction, WriteError, write_section

router = APIRouter()

AVAILABLE_STATUSES = ("ready", "metadata_only")


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


async def _ordered_sections(
    session: AsyncSession, project_id: uuid.UUID
) -> list[OutlineSection]:
    """按阅读顺序（path 字典序近似深度优先）返回章节。"""
    sections = (
        (
            await session.execute(
                select(OutlineSection).where(OutlineSection.project_id == project_id)
            )
        )
        .scalars()
        .all()
    )
    by_parent: dict[uuid.UUID | None, list[OutlineSection]] = {}
    for section in sections:
        by_parent.setdefault(section.parent_id, []).append(section)
    for group in by_parent.values():
        group.sort(key=lambda s: (s.order, s.title))

    ordered: list[OutlineSection] = []

    def walk(parent_id: uuid.UUID | None) -> None:
        for section in by_parent.get(parent_id, []):
            ordered.append(section)
            walk(section.id)

    walk(None)
    return ordered


async def _get_outline_section(
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


async def _get_or_create_manuscript(
    session: AsyncSession, project_id: uuid.UUID, section: OutlineSection
) -> ManuscriptSection:
    manuscript = (
        await session.execute(
            select(ManuscriptSection).where(
                ManuscriptSection.outline_section_id == section.id
            )
        )
    ).scalar_one_or_none()
    if manuscript is None:
        manuscript = ManuscriptSection(
            project_id=project_id, outline_section_id=section.id, content=""
        )
        session.add(manuscript)
        await session.commit()
        await session.refresh(manuscript)
    return manuscript


def _to_out(
    manuscript: ManuscriptSection, section: OutlineSection
) -> ManuscriptSectionOut:
    return ManuscriptSectionOut(
        id=manuscript.id,
        outline_section_id=section.id,
        title=section.title,
        path=section.path,
        level=section.level,
        content=manuscript.content,
        word_count=manuscript.word_count,
        status=manuscript.status,
        ai_generated=manuscript.ai_generated,
        source_paper_ids=list(manuscript.source_paper_ids or []),
    )


@router.get("", response_model=list[ManuscriptSectionOut])
async def list_manuscript(
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    """返回全部章节的正文（未写的返回空内容），按阅读顺序。"""
    sections = await _ordered_sections(session, project.id)
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

    out: list[ManuscriptSectionOut] = []
    for section in sections:
        manuscript = by_section.get(section.id)
        if manuscript is None:
            out.append(
                ManuscriptSectionOut(
                    id=section.id,  # 占位：尚未创建正文记录
                    outline_section_id=section.id,
                    title=section.title,
                    path=section.path,
                    level=section.level,
                )
            )
        else:
            out.append(_to_out(manuscript, section))
    return out


@router.put("/{section_id}", response_model=ManuscriptSectionOut)
async def save_section(
    section_id: uuid.UUID,
    data: ManuscriptSectionSave,
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    """保存用户编辑的正文。手工保存会清除 ai_generated 标记。"""
    section = await _get_outline_section(session, project.id, section_id)
    manuscript = await _get_or_create_manuscript(session, project.id, section)

    manuscript.content = data.content
    manuscript.word_count = len(data.content)
    if data.status:
        manuscript.status = data.status
    # 人工改过就不再算纯 AI 生成
    manuscript.ai_generated = False
    await session.commit()
    await session.refresh(manuscript)
    return _to_out(manuscript, section)


@router.post("/{section_id}/ai", response_model=WriteActionResponse)
async def ai_write(
    section_id: uuid.UUID,
    data: WriteActionRequest,
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    """执行一次 AI 写作动作。apply=true 时直接写入该节正文。"""
    section = await _get_outline_section(session, project.id, section_id)

    try:
        action = WriteAction(data.action)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"unknown action: {data.action}")

    stmt = select(Paper).where(
        Paper.project_id == project.id, Paper.status.in_(AVAILABLE_STATUSES)
    )
    if data.paper_ids:
        try:
            wanted = [uuid.UUID(p) for p in data.paper_ids]
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid paper_ids")
        stmt = stmt.where(Paper.id.in_(wanted))
    papers = (await session.execute(stmt)).scalars().all()

    # 相邻章节正文作为上下文，帮助衔接
    ordered = await _ordered_sections(session, project.id)
    index = next((i for i, s in enumerate(ordered) if s.id == section.id), None)
    context_before = context_after = None
    if index is not None:
        neighbor_ids = [
            s.id for s in ordered[max(0, index - 1) : index + 2] if s.id != section.id
        ]
        if neighbor_ids:
            neighbors = (
                (
                    await session.execute(
                        select(ManuscriptSection).where(
                            ManuscriptSection.outline_section_id.in_(neighbor_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
            by_section = {m.outline_section_id: m.content for m in neighbors}
            if index > 0:
                context_before = by_section.get(ordered[index - 1].id)
            if index + 1 < len(ordered):
                context_after = by_section.get(ordered[index + 1].id)

    try:
        embedder = get_embedder()
        store = get_vector_store()
    except Exception:
        embedder, store = None, None

    try:
        result = await write_section(
            str(project.id),
            action,
            section.path,
            paper_title=project.title,
            key_points=list(section.key_points or []),
            selection=data.selection,
            context_before=context_before,
            context_after=context_after,
            briefs=[_to_brief(p) for p in papers],
            target_words=data.target_words or section.est_words,
            language=data.language,
            discipline=project.discipline,
            embedder=embedder,
            store=store,
        )
    except WriteError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    applied = False
    if data.apply:
        manuscript = await _get_or_create_manuscript(session, project.id, section)
        manuscript.content = result.content
        manuscript.word_count = len(result.content)
        manuscript.ai_generated = True
        manuscript.source_paper_ids = result.paper_ids
        await session.commit()
        applied = True

    return WriteActionResponse(
        content=result.content,
        action=action.value,
        paper_ids=result.paper_ids,
        invalid_citations=result.invalid_citations,
        applied=applied,
    )


async def _assemble(session: AsyncSession, project: Project) -> Manuscript:
    """把大纲 + 正文 + 文献组装成可导出的稿件。"""
    sections = await _ordered_sections(session, project.id)
    manuscripts = (
        (
            await session.execute(
                select(ManuscriptSection).where(
                    ManuscriptSection.project_id == project.id
                )
            )
        )
        .scalars()
        .all()
    )
    by_section = {m.outline_section_id: m for m in manuscripts}

    papers = (
        (await session.execute(select(Paper).where(Paper.project_id == project.id)))
        .scalars()
        .all()
    )
    paper_by_id = {str(p.id): p for p in papers}

    parts: list[ManuscriptPart] = []
    cited_order: list[str] = []
    for section in sections:
        manuscript = by_section.get(section.id)
        paper_ids = list(manuscript.source_paper_ids or []) if manuscript else []
        parts.append(
            ManuscriptPart(
                title=section.title,
                level=section.level,
                content=manuscript.content if manuscript else "",
                paper_ids=paper_ids,
                ai_generated=bool(manuscript and manuscript.ai_generated),
            )
        )
        for pid in paper_ids:
            if pid not in cited_order and pid in paper_by_id:
                cited_order.append(pid)

    references = [
        Reference(
            paper_id=pid,
            title=paper_by_id[pid].title,
            authors=paper_by_id[pid].authors,
            year=paper_by_id[pid].year,
            venue=paper_by_id[pid].venue,
            doi=paper_by_id[pid].doi,
        )
        for pid in cited_order
    ]
    return Manuscript(title=project.title, parts=parts, references=references)


@router.get("/quality", response_model=QualityReportOut)
async def quality_check(
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
):
    """引用一致性与完整性检查。"""
    manuscript = await _assemble(session, project)
    issues = check_citations(manuscript)
    return QualityReportOut(
        issues=[
            CitationIssueOut(section=i.section, kind=i.kind, detail=i.detail)
            for i in issues
        ],
        word_count=manuscript.word_count,
        section_count=len(manuscript.parts),
        reference_count=len(manuscript.references),
        ai_generated_sections=sum(1 for p in manuscript.parts if p.ai_generated),
    )


def _content_disposition(filename: str, ext: str) -> str:
    """构造 Content-Disposition。

    HTTP 头只能是 latin-1，中文标题必须走 RFC 5987 的 filename* 形式，
    同时保留一个 ASCII 兜底 filename 供老客户端使用。
    """
    from urllib.parse import quote

    stem = "".join(
        ch for ch in (filename or "") if ch.isalnum() or ch in " -_"
    ).strip() or "manuscript"
    ascii_stem = stem.encode("ascii", "ignore").decode("ascii").strip() or "manuscript"
    encoded = quote(f"{stem}.{ext}", safe="")
    return (
        f'attachment; filename="{ascii_stem}.{ext}"; '
        f"filename*=UTF-8''{encoded}"
    )


@router.get("/export")
async def export(
    project: Project = Depends(get_project),
    session: AsyncSession = Depends(get_session),
    format: str = Query(default="markdown", pattern="^(markdown|latex|bibtex|docx)$"),
    disclosure: bool = Query(default=True, description="是否附 AI 使用声明"),
):
    """导出稿件。Markdown 优先，其次 LaTeX，Word 需要 python-docx。"""
    manuscript = await _assemble(session, project)

    if format == "markdown":
        body = to_markdown(manuscript, include_disclosure=disclosure)
        media, ext = "text/markdown; charset=utf-8", "md"
    elif format == "latex":
        body = to_latex(manuscript, include_disclosure=disclosure)
        media, ext = "application/x-tex; charset=utf-8", "tex"
    elif format == "bibtex":
        body = to_bibtex(manuscript)
        media, ext = "application/x-bibtex; charset=utf-8", "bib"
    else:
        try:
            data = to_docx_bytes(manuscript, include_disclosure=disclosure)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        return Response(
            content=data,
            media_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            headers={
                "Content-Disposition": _content_disposition(project.title, "docx")
            },
        )

    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": _content_disposition(project.title, ext)},
    )
