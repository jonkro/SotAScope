"""Tests for the settings API and ssl_verify seeding."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sotascope.api.deps import get_db
from sotascope.models.base import Base
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
