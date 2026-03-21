"""Tests for the PDF upload/serve/delete API endpoints."""

import io
from pathlib import Path

import pytest

from sotascope.api.works import _get_pdf_root


@pytest.fixture()
def pdf_storage_dir(tmp_path, monkeypatch):
    """Override _get_pdf_root to use a temp directory for PDF storage."""
    storage = tmp_path / "pdfs"
    storage.mkdir()

    original_fn = _get_pdf_root

    def _mock_get_pdf_root(db):
        return storage

    monkeypatch.setattr("sotascope.api.works._get_pdf_root", _mock_get_pdf_root)
    return storage


@pytest.fixture()
def work_id(client):
    """Create a work and return its ID."""
    resp = client.post("/api/works", json={"title": "Test Paper"})
    assert resp.status_code == 201
    return resp.json()["id"]


def _make_pdf_bytes(content: str = "fake-pdf-content") -> bytes:
    return content.encode("utf-8")


class TestUploadPDF:
    def test_upload_first_pdf_is_primary(self, client, work_id, pdf_storage_dir):
        resp = client.post(
            f"/api/works/{work_id}/pdfs",
            files={"file": ("paper.pdf", io.BytesIO(_make_pdf_bytes()), "application/pdf")},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["filename"] == "paper.pdf"
        assert data["is_primary"] is True
        assert data["work_id"] == work_id

        # File exists on disk
        assert (pdf_storage_dir / str(work_id) / "paper.pdf").is_file()

    def test_upload_second_pdf_not_primary(self, client, work_id, pdf_storage_dir):
        client.post(
            f"/api/works/{work_id}/pdfs",
            files={"file": ("first.pdf", io.BytesIO(_make_pdf_bytes()), "application/pdf")},
        )
        resp = client.post(
            f"/api/works/{work_id}/pdfs",
            files={"file": ("second.pdf", io.BytesIO(_make_pdf_bytes("second")), "application/pdf")},
        )
        assert resp.status_code == 201
        assert resp.json()["is_primary"] is False

    def test_upload_duplicate_filename_409(self, client, work_id, pdf_storage_dir):
        client.post(
            f"/api/works/{work_id}/pdfs",
            files={"file": ("paper.pdf", io.BytesIO(_make_pdf_bytes()), "application/pdf")},
        )
        resp = client.post(
            f"/api/works/{work_id}/pdfs",
            files={"file": ("paper.pdf", io.BytesIO(_make_pdf_bytes()), "application/pdf")},
        )
        assert resp.status_code == 409


class TestListPDFs:
    def test_list_pdfs(self, client, work_id, pdf_storage_dir):
        client.post(
            f"/api/works/{work_id}/pdfs",
            files={"file": ("a.pdf", io.BytesIO(_make_pdf_bytes()), "application/pdf")},
        )
        client.post(
            f"/api/works/{work_id}/pdfs",
            files={"file": ("b.pdf", io.BytesIO(_make_pdf_bytes("b")), "application/pdf")},
        )
        resp = client.get(f"/api/works/{work_id}/pdfs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["filename"] == "a.pdf"
        assert data[1]["filename"] == "b.pdf"


class TestServePDF:
    def test_serve_pdf(self, client, work_id, pdf_storage_dir):
        client.post(
            f"/api/works/{work_id}/pdfs",
            files={"file": ("paper.pdf", io.BytesIO(_make_pdf_bytes("serve-test")), "application/pdf")},
        )
        pdf_id = client.get(f"/api/works/{work_id}/pdfs").json()[0]["id"]

        resp = client.get(f"/api/works/{work_id}/pdfs/{pdf_id}/file")
        assert resp.status_code == 200
        assert resp.content == b"serve-test"

    def test_serve_missing_file_404(self, client, work_id, pdf_storage_dir):
        # Upload then delete the file from disk
        client.post(
            f"/api/works/{work_id}/pdfs",
            files={"file": ("gone.pdf", io.BytesIO(_make_pdf_bytes()), "application/pdf")},
        )
        pdf_id = client.get(f"/api/works/{work_id}/pdfs").json()[0]["id"]
        # Remove file from disk directly
        (pdf_storage_dir / str(work_id) / "gone.pdf").unlink()

        resp = client.get(f"/api/works/{work_id}/pdfs/{pdf_id}/file")
        assert resp.status_code == 404


class TestSetPrimary:
    def test_set_primary(self, client, work_id, pdf_storage_dir):
        client.post(
            f"/api/works/{work_id}/pdfs",
            files={"file": ("first.pdf", io.BytesIO(_make_pdf_bytes()), "application/pdf")},
        )
        client.post(
            f"/api/works/{work_id}/pdfs",
            files={"file": ("second.pdf", io.BytesIO(_make_pdf_bytes("2")), "application/pdf")},
        )
        pdfs = client.get(f"/api/works/{work_id}/pdfs").json()
        second_id = pdfs[1]["id"]

        resp = client.patch(f"/api/works/{work_id}/pdfs/{second_id}/set-primary")
        assert resp.status_code == 200
        assert resp.json()["is_primary"] is True

        # Verify old primary is no longer primary
        pdfs_after = client.get(f"/api/works/{work_id}/pdfs").json()
        assert pdfs_after[0]["is_primary"] is False
        assert pdfs_after[1]["is_primary"] is True


class TestDeletePDF:
    def test_delete_moves_to_orphaned(self, client, work_id, pdf_storage_dir):
        client.post(
            f"/api/works/{work_id}/pdfs",
            files={"file": ("paper.pdf", io.BytesIO(_make_pdf_bytes("orphan")), "application/pdf")},
        )
        pdf_id = client.get(f"/api/works/{work_id}/pdfs").json()[0]["id"]

        resp = client.delete(f"/api/works/{work_id}/pdfs/{pdf_id}")
        assert resp.status_code == 204

        # DB row gone
        assert len(client.get(f"/api/works/{work_id}/pdfs").json()) == 0

        # File moved to orphaned
        orphaned = pdf_storage_dir / "_orphaned" / str(work_id) / "paper.pdf"
        assert orphaned.is_file()
        assert orphaned.read_bytes() == b"orphan"

    def test_delete_primary_reassigns(self, client, work_id, pdf_storage_dir):
        client.post(
            f"/api/works/{work_id}/pdfs",
            files={"file": ("first.pdf", io.BytesIO(_make_pdf_bytes()), "application/pdf")},
        )
        client.post(
            f"/api/works/{work_id}/pdfs",
            files={"file": ("second.pdf", io.BytesIO(_make_pdf_bytes("2")), "application/pdf")},
        )
        pdfs = client.get(f"/api/works/{work_id}/pdfs").json()
        primary_id = pdfs[0]["id"]

        client.delete(f"/api/works/{work_id}/pdfs/{primary_id}")

        remaining = client.get(f"/api/works/{work_id}/pdfs").json()
        assert len(remaining) == 1
        assert remaining[0]["is_primary"] is True


class TestDeleteWorkOrphansPDFs:
    def test_delete_work_moves_pdfs_to_orphaned(self, client, work_id, pdf_storage_dir):
        client.post(
            f"/api/works/{work_id}/pdfs",
            files={"file": ("paper.pdf", io.BytesIO(_make_pdf_bytes("work-delete")), "application/pdf")},
        )
        resp = client.delete(f"/api/works/{work_id}")
        assert resp.status_code == 204

        # File moved to orphaned
        orphaned = pdf_storage_dir / "_orphaned" / str(work_id) / "paper.pdf"
        assert orphaned.is_file()
        assert orphaned.read_bytes() == b"work-delete"
