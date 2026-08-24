import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Postgres 用 JSONB / ARRAY；其他方言（如测试用 SQLite）回退到 JSON。
JsonType = JSON().with_variant(JSONB, "postgresql")
StrArrayType = JSON().with_variant(ARRAY(String), "postgresql")
UUID = Uuid(as_uuid=True)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(Text)
    discipline: Mapped[str | None] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(String(32), default="discovery")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    papers: Mapped[list["Paper"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    directions: Mapped[list["ResearchDirection"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    jobs: Mapped[list["Job"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    reviews: Mapped[list["ReviewDraft"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    outline_sections: Mapped[list["OutlineSection"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Paper(Base):
    __tablename__ = "papers"
    __table_args__ = (
        UniqueConstraint("project_id", "doi", name="uq_paper_project_doi"),
        UniqueConstraint(
            "project_id", "source", "source_id", name="uq_paper_project_source"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    source: Mapped[str] = mapped_column(String(32), default="manual")
    source_id: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    authors: Mapped[str | None] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None]
    doi: Mapped[str | None] = mapped_column(Text)
    arxiv_id: Mapped[str | None] = mapped_column(Text)
    venue: Mapped[str | None] = mapped_column(Text)
    citation_count: Mapped[int | None]
    url: Mapped[str | None] = mapped_column(Text)
    pdf_url: Mapped[str | None] = mapped_column(Text)
    references: Mapped[list[str] | None] = mapped_column(StrArrayType)
    """被引文献的 DOI / 标题，用于构建引用图谱。"""
    pdf_path: Mapped[str | None] = mapped_column(Text)
    parsed_md: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict | None] = mapped_column(JsonType)
    bibtex: Mapped[str | None] = mapped_column(Text)
    quality_score: Mapped[float | None]
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(default=0)
    tags: Mapped[list[str] | None] = mapped_column(StrArrayType)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    project: Mapped["Project"] = relationship(back_populates="papers")


class ResearchDirection(Base):
    """研究方向建议。evidence_paper_ids 存 UUID 字符串列表，便于跨方言。"""

    __tablename__ = "research_directions"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    statement: Mapped[str] = mapped_column(Text)
    gap: Mapped[str | None] = mapped_column(Text)
    innovation: Mapped[str | None] = mapped_column(Text)
    method_sketch: Mapped[str | None] = mapped_column(Text)
    feasibility: Mapped[float] = mapped_column(Float, default=0.5)
    novelty: Mapped[float] = mapped_column(Float, default=0.5)
    evidence_paper_ids: Mapped[list[str] | None] = mapped_column(StrArrayType)
    evidence_titles: Mapped[list[str] | None] = mapped_column(StrArrayType)
    selected: Mapped[bool] = mapped_column(default=False)
    feedback: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    project: Mapped["Project"] = relationship(back_populates="directions")


class Job(Base):
    """异步任务记录。长任务（解析、方向生成）通过它暴露进度。"""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    result: Mapped[dict | None] = mapped_column(JsonType)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    project: Mapped["Project"] = relationship(back_populates="jobs")


class ReviewDraft(Base):
    """综述草稿。sections 存结构化小节，markdown 存渲染结果便于直接导出。"""

    __tablename__ = "review_drafts"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    organization: Mapped[str] = mapped_column(String(16), default="topic")
    sections: Mapped[list | None] = mapped_column(JsonType)
    references: Mapped[list | None] = mapped_column(JsonType)
    markdown: Mapped[str | None] = mapped_column(Text)
    bibtex: Mapped[str | None] = mapped_column(Text)
    word_count: Mapped[int] = mapped_column(default=0)
    invalid_citation_count: Mapped[int] = mapped_column(default=0)
    """被剥离的编造引用数量，用于提示用户核查质量。"""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    project: Mapped["Project"] = relationship(back_populates="reviews")


class OutlineSection(Base):
    """大纲章节。用 parent_id 表达层级，path 冗余存储便于查询与展示。"""

    __tablename__ = "outline_sections"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("outline_sections.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(16), default="section")
    level: Mapped[int] = mapped_column(default=1)
    order: Mapped[int] = mapped_column(default=0)
    key_points: Mapped[list | None] = mapped_column(JsonType)
    est_words: Mapped[int] = mapped_column(default=400)
    hint: Mapped[str | None] = mapped_column(Text)
    template: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    project: Mapped["Project"] = relationship(back_populates="outline_sections")
    children: Mapped[list["OutlineSection"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )
    parent: Mapped["OutlineSection | None"] = relationship(
        back_populates="children", remote_side="OutlineSection.id"
    )
    manuscript: Mapped["ManuscriptSection | None"] = relationship(
        back_populates="outline_section", cascade="all, delete-orphan", uselist=False
    )


class ManuscriptSection(Base):
    """正文草稿，与大纲章节一对一绑定。"""

    __tablename__ = "manuscript_sections"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    outline_section_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("outline_sections.id", ondelete="CASCADE"), unique=True
    )
    content: Mapped[str] = mapped_column(Text, default="")
    """Markdown 正文。"""
    word_count: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    ai_generated: Mapped[bool] = mapped_column(default=False)
    """是否由 AI 生成初稿，用于导出时的 AI 使用声明。"""
    source_paper_ids: Mapped[list | None] = mapped_column(JsonType)
    """生成时引用的文献，供溯源。"""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    outline_section: Mapped["OutlineSection"] = relationship(
        back_populates="manuscript"
    )
