"""Tests for the settings API and ssl_verify seeding."""

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sotascope.api.deps import get_db
from sotascope.models.base import Base
from sotascope.models.cache import ApiCache
from sotascope.models.library import Work
from sotascope.models.settings import Setting


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session):
    from sotascope.app import app

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Seeding tests
# ---------------------------------------------------------------------------


def test_seed_default_settings_creates_ssl_verify():
    """_seed_default_settings() should create a ssl_verify row with value 'true'."""
    from sotascope.app import _seed_default_settings

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with patch("sotascope.app.SessionLocal", Session):
        _seed_default_settings()

    session = Session()
    try:
        row = session.execute(
            select(Setting).where(Setting.key == "ssl_verify")
        ).scalar_one_or_none()
        assert row is not None, "ssl_verify setting should be seeded"
        assert row.value == "true", f"Expected 'true', got '{row.value}'"
        assert row.description is not None and "proxy" in row.description.lower()
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_seed_default_settings_idempotent():
    """Calling _seed_default_settings() twice should not create duplicate rows."""
    from sotascope.app import _seed_default_settings

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    with patch("sotascope.app.SessionLocal", Session):
        _seed_default_settings()
        _seed_default_settings()  # second call should be a no-op

    session = Session()
    try:
        rows = session.execute(
            select(Setting).where(Setting.key == "ssl_verify")
        ).scalars().all()
        assert len(rows) == 1, "Should have exactly one ssl_verify row"
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


# ---------------------------------------------------------------------------
# API read tests
# ---------------------------------------------------------------------------


def test_get_settings_includes_ssl_verify(db_session, client):
    """GET /api/settings should return ssl_verify when it is seeded."""
    db_session.add(
        Setting(
            key="ssl_verify",
            value="true",
            description="Verify SSL certificates when calling external APIs",
        )
    )
    db_session.commit()

    resp = client.get("/api/settings")
    assert resp.status_code == 200

    settings = resp.json()
    ssl_setting = next((s for s in settings if s["key"] == "ssl_verify"), None)
    assert ssl_setting is not None, "ssl_verify should appear in settings list"
    assert ssl_setting["value"] == "true"
    assert ssl_setting["description"] is not None


def test_get_ssl_verify_default_is_true(db_session, client):
    """The seeded default value for ssl_verify should be 'true' (verification enabled)."""
    db_session.add(Setting(key="ssl_verify", value="true", description="test"))
    db_session.commit()

    resp = client.get("/api/settings")
    assert resp.status_code == 200

    settings = resp.json()
    ssl_setting = next(s for s in settings if s["key"] == "ssl_verify")
    assert ssl_setting["value"] == "true"


# ---------------------------------------------------------------------------
# API update tests
# ---------------------------------------------------------------------------


def test_update_ssl_verify_to_false(db_session, client):
    """PATCH /api/settings/ssl_verify should accept 'false' to disable SSL verification."""
    db_session.add(Setting(key="ssl_verify", value="true", description="test"))
    db_session.commit()

    resp = client.patch("/api/settings/ssl_verify", json={"value": "false"})
    assert resp.status_code == 200

    data = resp.json()
    assert data["key"] == "ssl_verify"
    assert data["value"] == "false"


def test_update_ssl_verify_back_to_true(db_session, client):
    """PATCH /api/settings/ssl_verify should accept 'true' to re-enable SSL verification."""
    db_session.add(Setting(key="ssl_verify", value="false", description="test"))
    db_session.commit()

    resp = client.patch("/api/settings/ssl_verify", json={"value": "true"})
    assert resp.status_code == 200

    data = resp.json()
    assert data["value"] == "true"


def test_update_ssl_verify_persists(db_session, client):
    """After PATCH, GET /api/settings should reflect the updated value."""
    db_session.add(Setting(key="ssl_verify", value="true", description="test"))
    db_session.commit()

    client.patch("/api/settings/ssl_verify", json={"value": "false"})

    resp = client.get("/api/settings")
    settings = resp.json()
    ssl_setting = next(s for s in settings if s["key"] == "ssl_verify")
    assert ssl_setting["value"] == "false"


def test_update_nonexistent_setting_returns_404(db_session, client):
    """PATCH for an unknown setting key should return 404."""
    resp = client.patch("/api/settings/nonexistent_key", json={"value": "x"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# _get_ssl_verify helper tests
# ---------------------------------------------------------------------------


def test_get_ssl_verify_reads_true(db_session):
    """_get_ssl_verify returns True when setting value is 'true'."""
    from sotascope.api.enrichment import _get_ssl_verify

    db_session.add(Setting(key="ssl_verify", value="true", description="test"))
    db_session.commit()

    assert _get_ssl_verify(db_session) is True


def test_get_ssl_verify_reads_false(db_session):
    """_get_ssl_verify returns False when setting value is 'false'."""
    from sotascope.api.enrichment import _get_ssl_verify

    db_session.add(Setting(key="ssl_verify", value="false", description="test"))
    db_session.commit()

    assert _get_ssl_verify(db_session) is False


def test_get_ssl_verify_defaults_true_when_missing(db_session):
    """_get_ssl_verify returns True when the setting row does not exist."""
    from sotascope.api.enrichment import _get_ssl_verify

    assert _get_ssl_verify(db_session) is True


def test_get_ssl_verify_defaults_true_when_empty(db_session):
    """_get_ssl_verify returns True when the setting value is an empty string."""
    from sotascope.api.enrichment import _get_ssl_verify

    db_session.add(Setting(key="ssl_verify", value="", description="test"))
    db_session.commit()

    assert _get_ssl_verify(db_session) is True


# ---------------------------------------------------------------------------
# Backfill venues endpoint tests
# ---------------------------------------------------------------------------


def test_backfill_venues_sets_venue_id_from_cache(db_session, client):
    """POST /api/settings/backfill-venues should set venue_id on a work that has
    a cached OpenAlex response containing primary_location.source venue data."""
    # Work with an OpenAlex ID but no venue assigned.
    work = Work(
        title="Test Paper",
        openalex_id="W9999999999",
        venue_id=None,
    )
    db_session.add(work)
    db_session.flush()

    # Cached OpenAlex response that includes primary_location.source.
    oa_raw = {
        "id": "https://openalex.org/W9999999999",
        "doi": None,
        "title": "Test Paper",
        "primary_location": {
            "source": {
                "id": "https://openalex.org/S123456789",
                "display_name": "Nature",
                "type": "journal",
            }
        },
        "authorships": [],
        "locations": [],
        "abstract_inverted_index": None,
        "cited_by_count": 0,
        "counts_by_year": [],
        "publication_year": 2023,
        "referenced_works": [],
    }
    cache_entry = ApiCache(
        source="openalex",
        query_key="work:openalex:W9999999999",
        response_json=json.dumps(oa_raw),
        cache_type="permanent",
    )
    db_session.add(cache_entry)
    db_session.commit()

    resp = client.post("/api/settings/backfill-venues")
    assert resp.status_code == 200

    data = resp.json()
    assert data["updated"] == 1
    assert "1 work" in data["message"]

    db_session.refresh(work)
    assert work.venue_id is not None


def test_backfill_venues_nothing_to_do(db_session, client):
    """POST /api/settings/backfill-venues returns a no-op message when all works
    already have venues."""
    from sotascope.models.library import Venue

    venue = Venue(name="ICML")
    db_session.add(venue)
    db_session.flush()

    work = Work(title="Already assigned", openalex_id="W1111111111", venue_id=venue.id)
    db_session.add(work)
    db_session.commit()

    resp = client.post("/api/settings/backfill-venues")
    assert resp.status_code == 200

    data = resp.json()
    assert data["updated"] == 0
    assert "nothing to do" in data["message"].lower()


def test_backfill_venues_idempotent(db_session, client):
    """Calling the endpoint twice should not double-count updates."""
    work = Work(title="Idempotent Test", openalex_id="W2222222222", venue_id=None)
    db_session.add(work)
    db_session.flush()

    oa_raw = {
        "id": "https://openalex.org/W2222222222",
        "doi": None,
        "title": "Idempotent Test",
        "primary_location": {
            "source": {
                "id": "https://openalex.org/S987654321",
                "display_name": "Science",
                "type": "journal",
            }
        },
        "authorships": [],
        "locations": [],
        "abstract_inverted_index": None,
        "cited_by_count": 0,
        "counts_by_year": [],
        "publication_year": 2022,
        "referenced_works": [],
    }
    db_session.add(ApiCache(
        source="openalex",
        query_key="work:openalex:W2222222222",
        response_json=json.dumps(oa_raw),
        cache_type="permanent",
    ))
    db_session.commit()

    resp1 = client.post("/api/settings/backfill-venues")
    assert resp1.status_code == 200
    assert resp1.json()["updated"] == 1

    # Second call — work already has a venue now, so nothing should be updated.
    resp2 = client.post("/api/settings/backfill-venues")
    assert resp2.status_code == 200
    assert resp2.json()["updated"] == 0
