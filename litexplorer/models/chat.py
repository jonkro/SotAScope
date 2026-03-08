"""Chat session and message persistence models."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from litexplorer.models.base import Base


class ChatSession(Base):
    """A persistent chat session for a paper discussion.

    At most one *auto* session exists per (work_id, project_id) scope.
    This invariant is enforced in application code (see ``/api/chat``).
    """

    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    work_id: Mapped[int | None] = mapped_column(
        ForeignKey("works.id", ondelete="CASCADE"), nullable=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    # Discussion mode — "papers" (default) or "extraction_schema"
    context_type: Mapped[str] = mapped_column(String(32), nullable=False, default="papers")
    # Auxiliary context ID — for "extraction_schema" mode this holds the schema PK.
    context_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # User-set title for saved snapshots; None for auto-sessions.
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # True = the auto-saved "last conversation". False = explicitly saved by user.
    is_auto: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.id",
    )

    def __repr__(self) -> str:
        return (
            f"<ChatSession id={self.id} work_id={self.work_id} "
            f"is_auto={self.is_auto} title={self.title!r}>"
        )


class ChatMessage(Base):
    """A single turn in a chat session."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # 'user' | 'assistant'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    session: Mapped["ChatSession"] = relationship(back_populates="messages")

    def __repr__(self) -> str:
        return f"<ChatMessage id={self.id} session_id={self.session_id} role={self.role!r}>"
