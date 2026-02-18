"""Shared FastAPI dependencies."""

from collections.abc import Generator

from sqlalchemy.orm import Session

from litexplorer.database import SessionLocal


def get_db() -> Generator[Session, None, None]:
    """Yield a DB session, closing it when the request is done."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
