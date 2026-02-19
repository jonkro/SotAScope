"""Application-level key/value settings stored in the database."""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from litexplorer.models.base import Base


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
