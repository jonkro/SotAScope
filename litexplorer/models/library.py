"""Library-layer models — shared across all projects."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from litexplorer.models.base import Base


# ---------------------------------------------------------------------------
# Works (papers)
# ---------------------------------------------------------------------------

class Work(Base):
    """A single scholarly work, uniquely keyed by DOI or arXiv ID."""

    __tablename__ = "works"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    doi: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    openalex_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    abstract: Mapped[str | None] = mapped_column(Text)
    publication_year: Mapped[int | None] = mapped_column(Integer)

    venue_id: Mapped[int | None] = mapped_column(ForeignKey("venues.id"))

    # Human-readable BibTeX key (AuthorYearKeyword convention).
    bibtex_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    # Raw BibTeX entry, kept in sync with structured fields.
    bibtex_entry: Mapped[str | None] = mapped_column(Text)

    citation_count: Mapped[int | None] = mapped_column(Integer, default=0)

    # None = DOI provided directly; True = DOI resolved via Crossref fuzzy match
    doi_auto_resolved: Mapped[bool | None] = mapped_column(default=None)

    # Soft user scope for future multi-user support.
    created_by: Mapped[str | None] = mapped_column(String(128))

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    venue: Mapped["Venue | None"] = relationship(back_populates="works")
    locations: Mapped[list["WorkLocation"]] = relationship(
        back_populates="work", cascade="all, delete-orphan"
    )
    authors: Mapped[list["WorkAuthor"]] = relationship(
        back_populates="work", cascade="all, delete-orphan", order_by="WorkAuthor.position"
    )
    pdfs: Mapped[list["WorkPDF"]] = relationship(
        back_populates="work", cascade="all, delete-orphan"
    )
    notes: Mapped[list["WorkNote"]] = relationship(
        back_populates="work", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Work id={self.id} doi={self.doi!r} title={self.title[:60]!r}>"


# ---------------------------------------------------------------------------
# Work PDFs
# ---------------------------------------------------------------------------

class WorkPDF(Base):
    """A PDF file attached to a work."""

    __tablename__ = "work_pdfs"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_id: Mapped[int] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    is_primary: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    work: Mapped["Work"] = relationship(back_populates="pdfs")


# ---------------------------------------------------------------------------
# Work Notes
# ---------------------------------------------------------------------------

class WorkNote(Base):
    """A note attached to a work, optionally scoped to a project."""

    __tablename__ = "work_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_id: Mapped[int] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"))
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    note_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_outdated: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    work: Mapped["Work"] = relationship(back_populates="notes")


# ---------------------------------------------------------------------------
# Work locations (venue URL, arXiv preprint URL, etc.)
# ---------------------------------------------------------------------------

class WorkLocation(Base):
    """A resolvable location for a work (venue page, preprint, etc.)."""

    __tablename__ = "work_locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_id: Mapped[int] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"))
    location_type: Mapped[str] = mapped_column(String(32))  # 'venue' | 'preprint'
    url: Mapped[str] = mapped_column(String(1024))
    is_primary: Mapped[bool] = mapped_column(default=True)

    work: Mapped["Work"] = relationship(back_populates="locations")


# ---------------------------------------------------------------------------
# Authors
# ---------------------------------------------------------------------------

class Author(Base):
    """A distinct author entity."""

    __tablename__ = "authors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    openalex_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)

    works: Mapped[list["WorkAuthor"]] = relationship(back_populates="author")

    def __repr__(self) -> str:
        return f"<Author id={self.id} name={self.name!r}>"


class WorkAuthor(Base):
    """Association between a work and an author, with position."""

    __tablename__ = "work_authors"
    __table_args__ = (UniqueConstraint("work_id", "author_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    work_id: Mapped[int] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"))
    author_id: Mapped[int] = mapped_column(ForeignKey("authors.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=0)

    work: Mapped["Work"] = relationship(back_populates="authors")
    author: Mapped["Author"] = relationship(back_populates="works")


# ---------------------------------------------------------------------------
# Venues and venue normalization
# ---------------------------------------------------------------------------

class Venue(Base):
    """A normalized publication venue (conference, journal)."""

    __tablename__ = "venues"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    dblp_id: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    openalex_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    issn: Mapped[str | None] = mapped_column(String(32), index=True)
    publisher: Mapped[str | None] = mapped_column(String(512))
    venue_type: Mapped[str | None] = mapped_column(String(32))  # 'conference' | 'journal'
    tier: Mapped[int] = mapped_column(Integer, default=2)  # 1 = top, 2 = regular, 3 = ignore

    works: Mapped[list["Work"]] = relationship(back_populates="venue")
    aliases: Mapped[list["VenueAlias"]] = relationship(
        back_populates="venue", cascade="all, delete-orphan",
        order_by="VenueAlias.sort_order",
    )
    fields: Mapped[list["VenueField"]] = relationship(
        back_populates="venue", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Venue id={self.id} name={self.name!r}>"


class VenueAlias(Base):
    """Alternative name for a venue (handles year-to-year name variation)."""

    __tablename__ = "venue_aliases"
    __table_args__ = (UniqueConstraint("venue_id", "alias"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    venue: Mapped["Venue"] = relationship(back_populates="aliases")


# ---------------------------------------------------------------------------
# Fields and venue tier list
# ---------------------------------------------------------------------------

class Field(Base):
    """A research field (e.g., 'computer_networks', 'ai_ml')."""

    __tablename__ = "fields"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)

    venues: Mapped[list["VenueField"]] = relationship(
        back_populates="field", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<Field id={self.id} name={self.name!r}>"


class VenueField(Base):
    """Association between a venue and a research field (many-to-many)."""

    __tablename__ = "venue_fields"
    __table_args__ = (UniqueConstraint("venue_id", "field_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id", ondelete="CASCADE"))
    field_id: Mapped[int] = mapped_column(ForeignKey("fields.id", ondelete="CASCADE"))

    venue: Mapped["Venue"] = relationship(back_populates="fields")
    field: Mapped["Field"] = relationship(back_populates="venues")


# ---------------------------------------------------------------------------
# Citation edges
# ---------------------------------------------------------------------------

class Citation(Base):
    """A directed citation edge: citing_work -> cited_work."""

    __tablename__ = "citations"
    __table_args__ = (
        UniqueConstraint("citing_work_id", "cited_work_id"),
        Index("ix_citations_cited", "cited_work_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    citing_work_id: Mapped[int] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"))
    cited_work_id: Mapped[int] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(32))  # 'openalex' | 'crossref' | 'semantic_scholar'

    citing_work: Mapped["Work"] = relationship(foreign_keys=[citing_work_id])
    cited_work: Mapped["Work"] = relationship(foreign_keys=[cited_work_id])
