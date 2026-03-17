"""Extraction schema and column models for structured literature table extraction."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from litexplorer.models.base import Base


class ExtractionSchema(Base):
    """A user-defined schema for structured extraction from papers.

    Each schema has a title (e.g. "Table 1"), an optional description that
    tells the LLM the purpose of the table, and an optional project scope.
    A null ``project_id`` means the schema is global (not project-specific).
    """

    __tablename__ = "extraction_schemas"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_promoted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    selected_work_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    columns: Mapped[list["ExtractionColumn"]] = relationship(
        back_populates="schema",
        cascade="all, delete-orphan",
        order_by="ExtractionColumn.sort_order",
    )

    def __repr__(self) -> str:
        return f"<ExtractionSchema id={self.id} title={self.title!r}>"


class ExtractionColumn(Base):
    """A column (question) within an extraction schema.

    Each column carries a ``prompt`` — the question asked of the LLM — and an
    optional list of ``allowed_values`` that constrain the LLM's answer.
    """

    __tablename__ = "extraction_columns"

    id: Mapped[int] = mapped_column(primary_key=True)
    schema_id: Mapped[int] = mapped_column(
        ForeignKey("extraction_schemas.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # e.g. ["supervised", "unsupervised", "other"]
    allowed_values: Mapped[list | None] = mapped_column(JSON, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    schema: Mapped["ExtractionSchema"] = relationship(back_populates="columns")

    def __repr__(self) -> str:
        return f"<ExtractionColumn id={self.id} name={self.name!r}>"
