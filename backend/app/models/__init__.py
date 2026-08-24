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
