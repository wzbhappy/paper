"""研究方向生成：文献摘要 + 主题聚类 + RAG 上下文 → 带证据的方向建议。

这是 Phase 1 的核心产出。关键约束：
- 每个方向必须有支撑文献（evidence_indices），越界引用会被丢弃，防止 LLM 编造。
- 评分做归一化与裁剪，保证落在 [0, 1]。
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger
from pydantic import BaseModel, Field, field_validator

from app.llm import LLMClient, LLMRequest, Message, Role, TaskType, get_llm
from app.llm.prompts import render
from app.rag import Embedder, RetrievedChunk, VectorStore, build_context, retrieve
from app.services.cluster import kmeans, label_clusters_by_keywords, suggest_k
from app.services.summarize import PaperSummary


@dataclass
class PaperBrief:
    """方向生成的输入单元：一篇已解析文献的关键信息。"""

    paper_id: str
    title: str | None = None
    year: int | None = None
    summary: PaperSummary | None = None

    def as_prompt_dict(self) -> dict:
        s = self.summary or PaperSummary()
        return {
            "title": self.title,
            "year": self.year,
            "one_line": s.one_line,
            "problem": s.problem,
            "method": s.method,
            "dataset": s.dataset,
            "limitations": s.limitations,
            "future_work": s.future_work,
        }

    def keywords(self) -> list[str]:
        s = self.summary or PaperSummary()
        return list(s.key_terms)

    def embed_text(self) -> str:
        """用于聚类的文本表示。"""
        s = self.summary or PaperSummary()
        parts = [
            self.title or "",
            s.one_line or "",
            s.problem or "",
            s.method or "",
            " ".join(s.key_terms),
        ]
        return " ".join(p for p in parts if p).strip()


class RawDirection(BaseModel):
    """LLM 原始输出。evidence_indices 为 prompt 中的文献编号（1-based）。"""

    statement: str
    gap: str | None = None
    innovation: str | None = None
    method_sketch: str | None = None
    feasibility: float = 0.5
    novelty: float = 0.5
    evidence_indices: list[int] = Field(default_factory=list)

    @field_validator("feasibility", "novelty")
    @classmethod
    def clamp_score(cls, v: float) -> float:
        # LLM 有时给 0-100 或 0-10，统一归一到 0-1
        if v > 1:
            v = v / 100 if v > 10 else v / 10
        return max(0.0, min(1.0, v))


class RawDirectionList(BaseModel):
    directions: list[RawDirection] = Field(default_factory=list)


class ResearchDirection(BaseModel):
    """最终方向建议，证据已解析为 paper_id。"""

    statement: str
    gap: str | None = None
    innovation: str | None = None
    method_sketch: str | None = None
    feasibility: float = 0.5
    novelty: float = 0.5
    evidence_paper_ids: list[str] = Field(default_factory=list)
    evidence_titles: list[str] = Field(default_factory=list)

    @property
    def score(self) -> float:
        """综合分：可行性与新颖性同等重要，用调和均值惩罚偏科。"""
        f, n = self.feasibility, self.novelty
        if f + n == 0:
            return 0.0
        return 2 * f * n / (f + n)


def _build_topic_summary(briefs: list[PaperBrief], vectors: list[list[float]]) -> str:
    """聚类后生成人类可读的主题分布描述。"""
    if len(briefs) < 3 or not vectors:
        return ""
    clusters = kmeans(vectors, k=suggest_k(len(briefs)))
    if not clusters:
        return ""
    label_clusters_by_keywords(clusters, [b.keywords() for b in briefs])

    lines: list[str] = []
    for cluster in clusters:
        titles = [
            briefs[i].title or briefs[i].paper_id
            for i in cluster.member_indices[:4]
        ]
        label = cluster.label or f"主题 {cluster.id + 1}"
        lines.append(
            f"- {label}（{len(cluster.member_indices)} 篇）：" + "；".join(titles)
        )
    return "\n".join(lines)


def _resolve_evidence(
    raw: RawDirection, briefs: list[PaperBrief]
) -> tuple[list[str], list[str]]:
    """把 1-based 编号映射为 paper_id；越界编号丢弃并告警。"""
    paper_ids: list[str] = []
    titles: list[str] = []
    for idx in raw.evidence_indices:
        if 1 <= idx <= len(briefs):
            brief = briefs[idx - 1]
            if brief.paper_id not in paper_ids:
                paper_ids.append(brief.paper_id)
                titles.append(brief.title or brief.paper_id)
        else:
            logger.warning(
                "direction cites out-of-range paper index {} (have {})",
                idx,
                len(briefs),
            )
    return paper_ids, titles


async def generate_directions(
    project_id: str,
    briefs: list[PaperBrief],
    n: int = 3,
    intent: str | None = None,
    discipline: str | None = None,
    language: str = "中文",
    require_evidence: bool = True,
    client: LLMClient | None = None,
    embedder: Embedder | None = None,
    store: VectorStore | None = None,
) -> list[ResearchDirection]:
    """生成研究方向建议，按综合分降序返回。

    require_evidence=True 时丢弃无支撑文献的方向，这是防幻觉的主要手段。
    """
    if not briefs:
        logger.warning("generate_directions: no papers, nothing to do")
        return []

    # 主题聚类（可选，embedder 不可用时跳过，不影响主流程）
    topic_summary = ""
    vectors: list[list[float]] = []
    if embedder is not None and len(briefs) >= 3:
        texts = [b.embed_text() for b in briefs]
        if all(texts):
            try:
                vectors = await embedder.embed(texts)
                topic_summary = _build_topic_summary(briefs, vectors)
            except Exception as exc:
                logger.warning("clustering skipped: {}", exc)

    # RAG 上下文：用意向或文献标题作为查询，取回原文片段
    context = ""
    if store is not None and embedder is not None:
        query = intent or " ".join(b.title or "" for b in briefs[:5])
        if query.strip():
            try:
                chunks: list[RetrievedChunk] = await retrieve(
                    project_id,
                    query,
                    limit=8,
                    max_per_paper=2,
                    embedder=embedder,
                    store=store,
                )
                context = build_context(chunks, max_chars=4000)
            except Exception as exc:
                logger.warning("RAG context skipped: {}", exc)

    prompt = render(
        "direction",
        discipline=discipline,
        n=n,
        intent=intent,
        language=language,
        papers=[b.as_prompt_dict() for b in briefs],
        topic_summary=topic_summary,
        context=context,
    )

    llm = client or get_llm()
    req = LLMRequest(
        messages=[Message(role=Role.USER, content=prompt)],
        task=TaskType.DIRECTION,
        temperature=0.6,
    )
    raw_list = await llm.complete_json(req, RawDirectionList, retries=1)

    directions: list[ResearchDirection] = []
    for raw in raw_list.directions:
        paper_ids, titles = _resolve_evidence(raw, briefs)
        if require_evidence and not paper_ids:
            logger.warning("drop direction without evidence: {}", raw.statement[:60])
            continue
        directions.append(
            ResearchDirection(
                statement=raw.statement,
                gap=raw.gap,
                innovation=raw.innovation,
                method_sketch=raw.method_sketch,
                feasibility=raw.feasibility,
                novelty=raw.novelty,
                evidence_paper_ids=paper_ids,
                evidence_titles=titles,
            )
        )

    directions.sort(key=lambda d: d.score, reverse=True)
    logger.info(
        "generated {}/{} directions for project {}",
        len(directions),
        len(raw_list.directions),
        project_id,
    )
    return directions
