"""综述生成：引用图谱分簇 → LLM 命名小节 → 分节生成正文 → 引用校验。

核心防幻觉机制：
- 每节只暴露该簇的文献，编号从 1 开始局部编号
- 生成后校验所有 [n] 引用是否越界，越界的标记为 invalid 并从正文剥离
- 最终输出全局重编号的引用表 + BibTeX
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from loguru import logger
from pydantic import BaseModel, Field

from app.graph import CitationGraph, Community, detect_communities
from app.llm import LLMClient, LLMRequest, Message, Role, TaskType, get_llm
from app.llm.prompts import render
from app.rag import Embedder, VectorStore, build_context, retrieve
from app.services.direction import PaperBrief

CITATION_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")

ORGANIZATION_LABEL = {
    "timeline": "时间线（研究脉络的演进）",
    "method": "方法学（技术路线的分类与对比）",
    "topic": "主题（子问题的划分）",
}


class OutlineSection(BaseModel):
    cluster_id: int
    title: str
    order: int = 1


class OutlineResponse(BaseModel):
    sections: list[OutlineSection] = Field(default_factory=list)


@dataclass
class ReviewSection:
    """综述的一个小节。"""

    title: str
    content: str
    paper_ids: list[str] = field(default_factory=list)
    """本节引用的文献 paper_id，顺序与节内 [n] 编号对应。"""
    invalid_citations: list[int] = field(default_factory=list)
    """LLM 编造的越界引用编号，已从正文剥离。"""


@dataclass
class ReviewDraft:
    sections: list[ReviewSection] = field(default_factory=list)
    references: list[PaperBrief] = field(default_factory=list)
    """全局引用表，顺序即最终编号（1-based）。"""

    @property
    def word_count(self) -> int:
        return sum(len(s.content) for s in self.sections)

    def to_markdown(self) -> str:
        """渲染为 Markdown，含全局重编号的引用与参考文献列表。"""
        # 建立 paper_id → 全局编号
        global_index = {
            brief.paper_id: i for i, brief in enumerate(self.references, start=1)
        }

        parts: list[str] = ["# 文献综述\n"]
        for section in self.sections:
            parts.append(f"## {section.title}\n")
            local_map = {
                local: global_index.get(pid, 0)
                for local, pid in enumerate(section.paper_ids, start=1)
            }
            parts.append(_renumber(section.content, local_map) + "\n")

        if self.references:
            parts.append("## 参考文献\n")
            for i, brief in enumerate(self.references, start=1):
                year = f"（{brief.year}）" if brief.year else ""
                parts.append(f"[{i}] {brief.title or brief.paper_id}{year}")

        return "\n".join(parts)

    def to_bibtex(self) -> str:
        entries: list[str] = []
        for brief in self.references:
            key = re.sub(r"\W+", "", (brief.title or brief.paper_id)[:20]).lower()
            fields = [f"  title = {{{brief.title or 'untitled'}}}"]
            if brief.year:
                fields.append(f"  year = {{{brief.year}}}")
            entries.append("@article{" + f"{key}{brief.year or ''}" + ",\n" + ",\n".join(fields) + "\n}")
        return "\n\n".join(entries)


def _renumber(content: str, mapping: dict[int, int]) -> str:
    """把节内局部引用编号替换为全局编号。"""

    def replace(match: re.Match[str]) -> str:
        numbers = [int(n.strip()) for n in match.group(1).split(",")]
        mapped = [str(mapping[n]) for n in numbers if mapping.get(n)]
        return f"[{','.join(mapped)}]" if mapped else ""

    return CITATION_RE.sub(replace, content)


def extract_citations(content: str) -> list[int]:
    """提取正文中出现的所有引用编号。"""
    found: list[int] = []
    for match in CITATION_RE.finditer(content):
        for part in match.group(1).split(","):
            number = int(part.strip())
            if number not in found:
                found.append(number)
    return sorted(found)


def validate_citations(content: str, max_index: int) -> tuple[str, list[int]]:
    """剥离越界引用，返回清理后的正文与被剥离的编号。

    这是防幻觉的最后一道防线：LLM 引用了不存在的文献编号时，
    宁可丢掉该引用标记，也不能让读者以为有文献支撑。
    """
    invalid: list[int] = []

    def replace(match: re.Match[str]) -> str:
        numbers = [int(n.strip()) for n in match.group(1).split(",")]
        valid = [n for n in numbers if 1 <= n <= max_index]
        for n in numbers:
            if n not in valid and n not in invalid:
                invalid.append(n)
        return f"[{','.join(str(n) for n in valid)}]" if valid else ""

    cleaned = CITATION_RE.sub(replace, content)
    # 剥离引用可能留下多余空格
    cleaned = re.sub(r" +([,.;。，；])", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip(), sorted(invalid)


def _paper_key(brief: PaperBrief) -> str:
    """图谱节点 key：优先 DOI（跨源一致），回退 paper_id。"""
    return brief.paper_id


async def build_clusters(
    project_id: str,
    briefs: list[PaperBrief],
    graph: CitationGraph | None = None,
) -> list[Community]:
    """从引用图谱分簇。图谱不可用或边太少时按单簇处理。"""
    keys = [_paper_key(b) for b in briefs]
    if len(briefs) <= 2:
        return [Community(id=0, members=keys)]

    edges: list[tuple[str, str]] = []
    if graph is not None:
        try:
            known = set(keys)
            all_edges = await graph.all_edges(project_id)
            # 只保留库内文献之间的边，外部引用不参与分簇
            edges = [
                (e.source_key, e.target_key)
                for e in all_edges
                if e.source_key in known and e.target_key in known
            ]
        except Exception as exc:
            logger.warning("citation graph unavailable, falling back: {}", exc)

    if not edges:
        logger.info("no internal citation edges; treating all papers as one cluster")
        return [Community(id=0, members=keys)]

    communities = detect_communities(edges, nodes=keys)
    return communities or [Community(id=0, members=keys)]


async def name_sections(
    clusters: list[Community],
    briefs_by_key: dict[str, PaperBrief],
    language: str = "中文",
    discipline: str | None = None,
    client: LLMClient | None = None,
) -> list[OutlineSection]:
    """让 LLM 为每个簇命名。失败时回退到通用标题。"""
    fallback = [
        OutlineSection(cluster_id=c.id, title=f"研究主题 {c.id + 1}", order=c.id + 1)
        for c in clusters
    ]
    if not clusters:
        return []

    payload = [
        {
            "id": c.id,
            "papers": [
                {
                    "title": briefs_by_key[k].title,
                    "year": briefs_by_key[k].year,
                    "one_line": (briefs_by_key[k].summary.one_line if briefs_by_key[k].summary else None),
                }
                for k in c.members
                if k in briefs_by_key
            ],
        }
        for c in clusters
    ]

    prompt = render(
        "review_outline", discipline=discipline, clusters=payload, language=language
    )
    llm = client or get_llm()
    req = LLMRequest(
        messages=[Message(role=Role.USER, content=prompt)],
        task=TaskType.REVIEW_GEN,
        temperature=0.3,
    )
    try:
        response = await llm.complete_json(req, OutlineResponse, retries=1)
    except Exception as exc:
        logger.warning("section naming failed, using fallback titles: {}", exc)
        return fallback

    valid_ids = {c.id for c in clusters}
    named = {s.cluster_id: s for s in response.sections if s.cluster_id in valid_ids}
    # 缺失的簇补上兜底标题
    result = [named.get(c.id) or fallback[i] for i, c in enumerate(clusters)]
    result.sort(key=lambda s: s.order)
    return result


async def generate_section(
    project_id: str,
    title: str,
    briefs: list[PaperBrief],
    target_words: int = 400,
    organization: str = "topic",
    language: str = "中文",
    discipline: str | None = None,
    client: LLMClient | None = None,
    embedder: Embedder | None = None,
    store: VectorStore | None = None,
) -> ReviewSection:
    """生成一个小节。节内文献从 1 开始局部编号。"""
    if not briefs:
        return ReviewSection(title=title, content="", paper_ids=[])

    # RAG 补充原文片段，限定在本节文献范围内
    context = ""
    if embedder is not None and store is not None:
        try:
            chunks = await retrieve(
                project_id,
                title,
                limit=6,
                paper_ids=[b.paper_id for b in briefs],
                max_per_paper=2,
                embedder=embedder,
                store=store,
            )
            context = build_context(chunks, max_chars=3000)
        except Exception as exc:
            logger.warning("RAG context unavailable for section {!r}: {}", title, exc)

    prompt = render(
        "review_gen",
        discipline=discipline,
        section_title=title,
        papers=[
            {
                **b.as_prompt_dict(),
                "authors": None,
                "conclusion": b.summary.conclusion if b.summary else None,
            }
            for b in briefs
        ],
        target_words=target_words,
        organization=ORGANIZATION_LABEL.get(organization, ORGANIZATION_LABEL["topic"]),
        language=language,
        context=context,
    )

    llm = client or get_llm()
    req = LLMRequest(
        messages=[Message(role=Role.USER, content=prompt)],
        task=TaskType.REVIEW_GEN,
        temperature=0.5,
    )
    resp = await llm.complete(req)

    content, invalid = validate_citations(resp.content.strip(), len(briefs))
    if invalid:
        logger.warning(
            "section {!r}: stripped {} hallucinated citations {}",
            title,
            len(invalid),
            invalid,
        )

    return ReviewSection(
        title=title,
        content=content,
        paper_ids=[b.paper_id for b in briefs],
        invalid_citations=invalid,
    )


async def generate_review(
    project_id: str,
    briefs: list[PaperBrief],
    organization: str = "topic",
    words_per_section: int = 400,
    language: str = "中文",
    discipline: str | None = None,
    client: LLMClient | None = None,
    embedder: Embedder | None = None,
    store: VectorStore | None = None,
    graph: CitationGraph | None = None,
) -> ReviewDraft:
    """生成完整综述草稿。"""
    if not briefs:
        logger.warning("generate_review: no papers")
        return ReviewDraft()

    briefs_by_key = {_paper_key(b): b for b in briefs}
    clusters = await build_clusters(project_id, briefs, graph=graph)
    outline = await name_sections(
        clusters, briefs_by_key, language=language, discipline=discipline, client=client
    )

    cluster_by_id = {c.id: c for c in clusters}
    sections: list[ReviewSection] = []

    for item in outline:
        cluster = cluster_by_id.get(item.cluster_id)
        if cluster is None:
            continue
        section_briefs = [briefs_by_key[k] for k in cluster.members if k in briefs_by_key]
        if not section_briefs:
            continue
        section = await generate_section(
            project_id,
            item.title,
            section_briefs,
            target_words=words_per_section,
            organization=organization,
            language=language,
            discipline=discipline,
            client=client,
            embedder=embedder,
            store=store,
        )
        sections.append(section)

    # 全局引用表：按小节出现顺序去重
    references: list[PaperBrief] = []
    seen: set[str] = set()
    for section in sections:
        for pid in section.paper_ids:
            if pid not in seen and pid in briefs_by_key:
                seen.add(pid)
                references.append(briefs_by_key[pid])

    logger.info(
        "review generated: {} sections, {} references, {} chars",
        len(sections),
        len(references),
        sum(len(s.content) for s in sections),
    )
    return ReviewDraft(sections=sections, references=references)
