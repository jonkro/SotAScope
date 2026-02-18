"""API response cache for OpenAlex, Crossref, and Semantic Scholar."""

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from litexplorer.models.base import Base


class ApiCache(Base):
    """Cached API responses.

    cache_type semantics:
      - 'permanent': backward citations, paper metadata — never auto-expires.
      - 'timestamped': forward citations — fetched_at is shown in the UI
        and the user can trigger a manual refresh.
    """

    __tablename__ = "api_cache"
    __table_args__ = (
        Index("ix_api_cache_lookup", "source", "query_key", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # 'openalex' | 'crossref' | 'semantic_scholar'
    query_key: Mapped[str] = mapped_column(String(512), nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
    cache_type: Mapped[str] = mapped_column(String(16), nullable=False)  # 'permanent' | 'timestamped'
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
