"""正文写作服务：AI 动作（初稿/扩写/改写/润色/翻译/降重）+ 引用溯源。

所有动作共用一个 prompt 模板，靠 action 分支切换指令，保证行为一致。
引用校验复用综述模块的实现，越界引用一律剥离。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from loguru import logger

from app.llm import LLMClient, LLMRequest, Message, Role, TaskType, get_llm
from app.llm.prompts import render
from app.rag import Embedder, VectorStore, build_context, retrieve
from app.services.direction import PaperBrief
from app.services.review import validate_citations

CONTEXT_CHARS = 300


class WriteAction(str, Enum):
    DRAFT = "draft"
    EXPAND = "expand"
    REWRITE = "rewrite"
    POLISH = "polish"
    ACADEMIC = "academic"
    TRANSLATE = "translate"
    DEDUP = "dedup"


# 需要选中文本才有意义的动作
SELECTION_REQUIRED = {
    WriteAction.EXPAND,
    WriteAction.REWRITE,
    WriteAction.POLISH,
    WriteAction.ACADEMIC,
    WriteAction.TRANSLATE,
    WriteAction.DEDUP,
}

# 纯语言加工的动作不需要文献，避免模型硬塞引用
NEEDS_PAPERS = {WriteAction.DRAFT, WriteAction.EXPAND}

TASK_BY_ACTION = {
    WriteAction.DRAFT: TaskType.REVIEW_GEN,
    WriteAction.EXPAND: TaskType.REVIEW_GEN,
    WriteAction.REWRITE: TaskType.POLISH,
    WriteAction.POLISH: TaskType.POLISH,
    WriteAction.ACADEMIC: TaskType.POLISH,
    WriteAction.TRANSLATE: TaskType.TRANSLATE,
    WriteAction.DEDUP: TaskType.POLISH,
}

TEMPERATURE_BY_ACTION = {
    WriteAction.DRAFT: 0.6,
    WriteAction.EXPAND: 0.6,
    WriteAction.REWRITE: 0.4,
    WriteAction.POLISH: 0.2,
    WriteAction.ACADEMIC: 0.3,
    WriteAction.TRANSLATE: 0.2,
    WriteAction.DEDUP: 0.7,
}


class WriteError(ValueError):
    """写作请求参数不合法。"""


@dataclass
class WriteResult:
    content: str
    action: WriteAction
    paper_ids: list[str] = field(default_factory=list)
    """本次可引用的文献，顺序与 [n] 编号对应。"""
    invalid_citations: list[int] = field(default_factory=list)
    used_papers: bool = False


async def write_section(
    project_id: str,
    action: WriteAction,
    section_path: str,
    paper_title: str | None = None,
    key_points: list[str] | None = None,
    selection: str | None = None,
    context_before: str | None = None,
    context_after: str | None = None,
    briefs: list[PaperBrief] | None = None,
    target_words: int = 400,
    language: str = "中文",
    discipline: str | None = None,
    client: LLMClient | None = None,
    embedder: Embedder | None = None,
    store: VectorStore | None = None,
) -> WriteResult:
    """执行一次写作动作。

    选中文本类动作缺 selection 会直接报错，避免把空请求送给模型白花 token。
    """
    if action in SELECTION_REQUIRED and not (selection or "").strip():
        raise WriteError(f"action {action.value!r} requires non-empty selection")
    if action is WriteAction.DRAFT and not (key_points or selection):
        raise WriteError("draft requires key_points or selection")

    papers = list(briefs or []) if action in NEEDS_PAPERS else []

    # RAG 上下文：用要点或选中文本作为查询，限定在可引用文献内
    context = ""
    if papers and embedder is not None and store is not None:
        query = " ".join(key_points or []) or (selection or "") or section_path
        if query.strip():
            try:
                chunks = await retrieve(
                    project_id,
                    query[:500],
                    limit=6,
                    paper_ids=[b.paper_id for b in papers],
                    max_per_paper=2,
                    embedder=embedder,
                    store=store,
                )
                context = build_context(chunks, max_chars=3000)
            except Exception as exc:
                logger.warning("write: RAG context unavailable: {}", exc)

    prompt = render(
        "write",
        discipline=discipline,
        action=action.value,
        section_path=section_path,
        paper_title=paper_title,
        key_points=key_points or [],
        selection=selection,
        context_before=(context_before or "")[-CONTEXT_CHARS:] or None,
        context_after=(context_after or "")[:CONTEXT_CHARS] or None,
        papers=[
            {
                "title": b.title,
                "year": b.year,
                "one_line": b.summary.one_line if b.summary else None,
                "method": b.summary.method if b.summary else None,
                "conclusion": b.summary.conclusion if b.summary else None,
            }
            for b in papers
        ],
        context=context,
        target_words=target_words,
        language=language,
    )

    llm = client or get_llm()
    req = LLMRequest(
        messages=[Message(role=Role.USER, content=prompt)],
        task=TASK_BY_ACTION[action],
        temperature=TEMPERATURE_BY_ACTION[action],
    )
    resp = await llm.complete(req, use_cache=False)

    # 无可引用文献时任何 [n] 都是编造，max_index=0 全部剥离
    content, invalid = validate_citations(resp.content.strip(), len(papers))
    if invalid:
        logger.warning(
            "write({}): stripped {} invalid citations {}",
            action.value,
            len(invalid),
            invalid,
        )

    return WriteResult(
        content=content,
        action=action,
        paper_ids=[b.paper_id for b in papers],
        invalid_citations=invalid,
        used_papers=bool(papers),
    )
