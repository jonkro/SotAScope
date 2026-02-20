"""Tests for filesystem browsing and PDF migration endpoints."""

import os
from pathlib import Path

import pytest

from litexplorer.models.settings import Setting


@pytest.fixture()
def tmp_dirs(tmp_path):
    """Create a temp directory structure for browsing tests."""
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "gamma").mkdir()
    return tmp_path


# ---- Browse endpoint ----


def test_browse_specific_dir(client, tmp_dirs):
    resp = client.get("/api/filesystem/browse", params={"path": str(tmp_dirs)})
    assert resp.status_code == 200
    data = resp.json()
    assert data["current_path"] == str(tmp_dirs.resolve())
    assert data["parent_path"] is not None
    assert "alpha" in data["directories"]
    assert "beta" in data["directories"]
    assert "gamma" in data["directories"]


def test_browse_hidden_dirs_excluded(client, tmp_dirs):
    resp = client.get("/api/filesystem/browse", params={"path": str(tmp_dirs)})
    assert resp.status_code == 200
    assert ".hidden" not in resp.json()["directories"]


def test_browse_nonexistent_path(client):
    resp = client.get("/api/filesystem/browse", params={"path": "/nonexistent/path/abc123"})
    assert resp.status_code == 400


def test_browse_default_path(client, db_session):
    """Browse with no path should default to the config PDF dir."""
    resp = client.get("/api/filesystem/browse")
    # May succeed or fail depending on whether the default dir exists,
    # but should not return a server error
    assert resp.status_code in (200, 400)


def test_browse_returns_sorted_directories(client, tmp_dirs):
    resp = client.get("/api/filesystem/browse", params={"path": str(tmp_dirs)})
    assert resp.status_code == 200
    dirs = resp.json()["directories"]
    assert dirs == sorted(dirs)


# ---- Mkdir endpoint ----


def test_mkdir_creates_directory(client, tmp_path):
    new_dir = tmp_path / "new_folder"
    resp = client.post("/api/filesystem/mkdir", json={"path": str(new_dir)})
    assert resp.status_code == 200
    assert new_dir.exists() and new_dir.is_dir()
    assert resp.json()["path"] == str(new_dir.resolve())


def test_mkdir_creates_nested_parents(client, tmp_path):
    nested = tmp_path / "a" / "b" / "c"
    resp = client.post("/api/filesystem/mkdir", json={"path": str(nested)})
    assert resp.status_code == 200
    assert nested.exists() and nested.is_dir()


# ---- Migration endpoint ----


def test_migrate_moves_files_and_updates_setting(client, db_session, tmp_path):
    """Migration should move files and directories, then update the setting."""
    old_dir = tmp_path / "old_pdfs"
    new_dir = tmp_path / "new_pdfs"
    old_dir.mkdir()

    # Create test content
    (old_dir / "paper.pdf").write_text("content")
    (old_dir / "subdir").mkdir()
    (old_dir / "subdir" / "nested.pdf").write_text("nested")

    # Seed the setting to point to old_dir
    db_session.add(Setting(key="pdf_storage_path", value=str(old_dir), description="test"))
    db_session.commit()

    resp = client.post(
        "/api/settings/pdf_storage_path/migrate",
        json={"new_path": str(new_dir)},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["files_moved"] == 1
    assert data["directories_moved"] == 1
    assert data["errors"] == []
    assert data["old_path"] == str(old_dir.resolve())
    assert data["new_path"] == str(new_dir.resolve())

    # Verify files moved
    assert (new_dir / "paper.pdf").exists()
    assert (new_dir / "subdir" / "nested.pdf").exists()
    assert not (old_dir / "paper.pdf").exists()

    # Verify setting updated
    db_session.expire_all()
    row = db_session.query(Setting).filter_by(key="pdf_storage_path").one()
    assert row.value == str(new_dir.resolve())


def test_migrate_same_path_noop(client, db_session, tmp_path):
    """Migration to the same path should be a no-op."""
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    (pdf_dir / "file.pdf").write_text("test")

    db_session.add(Setting(key="pdf_storage_path", value=str(pdf_dir), description="test"))
    db_session.commit()

    resp = client.post(
        "/api/settings/pdf_storage_path/migrate",
        json={"new_path": str(pdf_dir)},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["files_moved"] == 0
    assert data["directories_moved"] == 0
    # File should still be in original location
    assert (pdf_dir / "file.pdf").exists()


def test_migrate_empty_source_succeeds(client, db_session, tmp_path):
    """Migration from an empty (or nonexistent) source should succeed and update setting."""
    old_dir = tmp_path / "empty_old"
    new_dir = tmp_path / "new_pdfs"
    old_dir.mkdir()

    db_session.add(Setting(key="pdf_storage_path", value=str(old_dir), description="test"))
    db_session.commit()

    resp = client.post(
        "/api/settings/pdf_storage_path/migrate",
        json={"new_path": str(new_dir)},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["files_moved"] == 0
    assert data["directories_moved"] == 0
    assert data["errors"] == []
    assert new_dir.exists()


def test_migrate_handles_existing_subdirectory(client, db_session, tmp_path):
    """When destination already has a subdir with the same name, files merge."""
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()

    # Both old and new have 'papers/' subdir
    (old_dir / "papers").mkdir()
    (old_dir / "papers" / "a.pdf").write_text("a")
    (new_dir / "papers").mkdir()
    (new_dir / "papers" / "b.pdf").write_text("b")

    db_session.add(Setting(key="pdf_storage_path", value=str(old_dir), description="test"))
    db_session.commit()

    resp = client.post(
        "/api/settings/pdf_storage_path/migrate",
        json={"new_path": str(new_dir)},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["errors"] == []
    # Both files should be in new/papers/
    assert (new_dir / "papers" / "a.pdf").exists()
    assert (new_dir / "papers" / "b.pdf").exists()
