"""Project-layer models — per-project topic lists and paper selections."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from litexplorer.models.base import Base


class Project(Base):
    """A research project containing topic lists."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(String(128))

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    topic_lists: Mapped[list["TopicList"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    ignored_work_associations: Mapped[list["ProjectIgnoredWork"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    venue_tier_overrides: Mapped[list["ProjectVenueTier"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Project id={self.id} name={self.name!r}>"


class TopicList(Base):
    """A named, color-coded collection of works within a project."""

    __tablename__ = "topic_lists"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    color: Mapped[str] = mapped_column(String(7), nullable=False)  # hex color, e.g. '#3b82f6'

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    project: Mapped["Project"] = relationship(back_populates="topic_lists")
    work_associations: Mapped[list["TopicListWork"]] = relationship(
        back_populates="topic_list", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<TopicList id={self.id} name={self.name!r} color={self.color}>"


class TopicListWork(Base):
    """Association between a topic list and a work (seed paper selection)."""

    __tablename__ = "topic_list_works"
    __table_args__ = (UniqueConstraint("topic_list_id", "work_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    topic_list_id: Mapped[int] = mapped_column(
        ForeignKey("topic_lists.id", ondelete="CASCADE")
    )
    work_id: Mapped[int] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"))

    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    topic_list: Mapped["TopicList"] = relationship(back_populates="work_associations")
    work: Mapped["Work"] = relationship()


class ProjectIgnoredWork(Base):
    """Works marked as uninteresting for a project (excluded from timeline)."""

    __tablename__ = "project_ignored_works"
    __table_args__ = (UniqueConstraint("project_id", "work_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    work_id: Mapped[int] = mapped_column(ForeignKey("works.id", ondelete="CASCADE"))

    project: Mapped["Project"] = relationship(back_populates="ignored_work_associations")
    work: Mapped["Work"] = relationship()


class ProjectVenueTier(Base):
    """Per-project venue tier override.  Absence = inherit global Venue.tier."""

    __tablename__ = "project_venue_tiers"
    __table_args__ = (UniqueConstraint("project_id", "venue_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id", ondelete="CASCADE"))
    tier: Mapped[int] = mapped_column(Integer, nullable=False)

    project: Mapped["Project"] = relationship(back_populates="venue_tier_overrides")
    venue: Mapped["Venue"] = relationship()
