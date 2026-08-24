from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field


# ---------- Project ----------
class ProjectBase(BaseModel):
    title: str
    discipline: str | None = None
    stage: str = "discovery"


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    title: str | None = None
    discipline: str | None = None
    stage: str | None = None


class ProjectOut(ProjectBase):
    id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------- Paper ----------
class PaperOut(BaseModel):
    id: UUID
    project_id: UUID
    title: str | None = None
    authors: str | None = None
    abstract: str | None = None
    year: int | None = None
    doi: str | None = None
    venue: str | None = None
    citation_count: int | None = None
    url: str | None = None
    pdf_url: str | None = None
    source: str
    status: str
    error: str | None = None
    chunk_count: int = 0
    summary: dict[str, Any] | None = None
    bibtex: str | None = None
    tags: list[str] | None = None
    created_at: datetime
    pdf_path: str | None = Field(default=None, exclude=True)
    """内部字段，不下发给前端（可能含服务器路径）。"""

    model_config = ConfigDict(from_attributes=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_pdf(self) -> bool:
        """前端据此决定是否显示「重新解析」。"""
        return bool(self.pdf_path)


class PaperUpdate(BaseModel):
    title: str | None = None
    authors: str | None = None
    year: int | None = None
    doi: str | None = None
    tags: list[str] | None = None
    quality_score: float | None = None


# ---------- Research direction ----------
class DirectionOut(BaseModel):
    id: UUID
    project_id: UUID
    statement: str
    gap: str | None = None
    innovation: str | None = None
    method_sketch: str | None = None
    feasibility: float
    novelty: float
    evidence_paper_ids: list[str] | None = None
    evidence_titles: list[str] | None = None
    selected: bool
    feedback: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DirectionGenerateRequest(BaseModel):
    n: int = Field(default=3, ge=1, le=10)
    intent: str | None = None
    replace: bool = True
    """True 时清空该项目已有方向再生成。"""


class DirectionUpdate(BaseModel):
    selected: bool | None = None
    feedback: str | None = None


# ---------- Job ----------
class JobOut(BaseModel):
    id: UUID
    project_id: UUID
    type: str
    status: str
    progress: float
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------- Search ----------
class SearchRequest(BaseModel):
    query: str
    sources: list[str] | None = None
    """留空则使用全部可用源。"""
    limit: int = Field(default=20, ge=1, le=100)
    year_from: int | None = None
    year_to: int | None = None
    expand: bool = True
    """是否用 LLM 扩展关键词。"""


class SearchResultItem(BaseModel):
    title: str
    source: str
    source_id: str | None = None
    authors: list[str] = Field(default_factory=list)
    abstract: str | None = None
    year: int | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    venue: str | None = None
    citation_count: int | None = None
    url: str | None = None
    pdf_url: str | None = None
    references: list[str] = Field(default_factory=list)
    """被引文献 DOI/标题。导入时用于构建引用图谱，前端无需展示。"""
    already_in_library: bool = False


class SearchResponse(BaseModel):
    query: str
    expanded_queries: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    results: list[SearchResultItem] = Field(default_factory=list)


class ImportRequest(BaseModel):
    """把检索结果导入文献库。"""

    items: list[SearchResultItem] = Field(min_length=1)


class ImportResponse(BaseModel):
    imported: int
    skipped: int
    paper_ids: list[str] = Field(default_factory=list)


# ---------- Review ----------
class ReviewGenerateRequest(BaseModel):
    organization: str = Field(default="topic", pattern="^(topic|timeline|method)$")
    words_per_section: int = Field(default=400, ge=100, le=2000)


class ReviewSectionOut(BaseModel):
    title: str
    content: str
    paper_ids: list[str] = Field(default_factory=list)
    invalid_citations: list[int] = Field(default_factory=list)


class ReviewOut(BaseModel):
    id: UUID
    project_id: UUID
    organization: str
    sections: list[ReviewSectionOut] = Field(default_factory=list)
    markdown: str | None = None
    bibtex: str | None = None
    word_count: int = 0
    invalid_citation_count: int = 0
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReviewUpdate(BaseModel):
    markdown: str


# ---------- Citation graph ----------
class GraphStatsOut(BaseModel):
    node_count: int = 0
    edge_count: int = 0
    most_cited: list[dict[str, Any]] = Field(default_factory=list)
    available: bool = True
    error: str | None = None
