from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from sotascope.config import settings

engine = create_engine(
    settings.db_url,
    echo=False,
    connect_args={"check_same_thread": False},  # required for SQLite + threads
)


# Enable WAL mode for better concurrent read/write performance.
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine)


def get_db():
    """FastAPI dependency that yields a DB session and closes it after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables. Call once at startup."""
    from sotascope.models import Base  # noqa: F811 — imports all models so create_all sees them

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.pdf_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
