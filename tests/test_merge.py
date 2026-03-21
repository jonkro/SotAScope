"""Tests for work merge logic: WorkPDF/WorkNote repointing, physical file move,
and the /api/works/integrity endpoint."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sotascope.models.library import Work, WorkNote, WorkPDF
from sotascope.models.settings import Setting


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_work(db, title="Test Paper", doi=None, year=2023):
    w = Work(title=title, doi=doi, publication_year=year)
    db.add(w)
    db.commit()
    return w


def _make_pdf(db, work_id, filename="paper.pdf", is_primary=True, extraction_status="ready"):
    pdf = WorkPDF(
        work_id=work_id,
        filename=filename,
        is_primary=is_primary,
        extraction_status=extraction_status,
    )
    db.add(pdf)
    db.commit()
    return pdf


def _make_note(db, work_id, content="A note", note_type=None, provenance="user"):
    note = WorkNote(
        work_id=work_id,
        content=content,
        note_type=note_type,
        provenance=provenance,
    )
    db.add(note)
    db.commit()
    return note


def _seed_pdf_setting(db, path_str):
    db.add(Setting(key="pdf_storage_path", value=path_str, description="test"))
    db.commit()


# ---------------------------------------------------------------------------
# 1. Merge repoints WorkPDF rows to target work
# ---------------------------------------------------------------------------


def test_merge_repoints_work_pdf_rows(client, db_session, tmp_path):
    """After merge, WorkPDF rows that belonged to source now point to target."""
    _seed_pdf_setting(db_session, str(tmp_path))

    source = _make_work(db_session, title="Source Paper", doi="10.1/source")
    target = _make_work(db_session, title="Target Paper", doi="10.1/target")

    # Give source a PDF (no physical file needed for DB-row test)
    pdf = _make_pdf(db_session, source.id, filename="source.pdf")

    resp = client.post(f"/api/works/{target.id}/merge/{source.id}")
    assert resp.status_code == 200, resp.text

    # Source should be gone
    assert client.get(f"/api/works/{source.id}").status_code == 404

    # The PDF row should now point to target
    db_session.expire_all()
    from sqlalchemy import select
    remaining_pdf = db_session.scalars(
        select(WorkPDF).where(WorkPDF.id == pdf.id)
    ).one_or_none()
    assert remaining_pdf is not None, "PDF row was deleted — should have been repointed"
    assert remaining_pdf.work_id == target.id


# ---------------------------------------------------------------------------
# 2. Merge repoints WorkNote rows to target work
# ---------------------------------------------------------------------------


def test_merge_repoints_work_note_rows(client, db_session, tmp_path):
    """After merge, WorkNote rows that belonged to source now point to target."""
    _seed_pdf_setting(db_session, str(tmp_path))

    source = _make_work(db_session, title="Source Paper", doi="10.1/src")
    target = _make_work(db_session, title="Target Paper", doi="10.1/tgt")

    note = _make_note(db_session, source.id, content="Important observation")

    resp = client.post(f"/api/works/{target.id}/merge/{source.id}")
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    from sqlalchemy import select
    remaining_note = db_session.scalars(
        select(WorkNote).where(WorkNote.id == note.id)
    ).one_or_none()
    assert remaining_note is not None, "Note row was deleted — should have been repointed"
    assert remaining_note.work_id == target.id


# ---------------------------------------------------------------------------
# 3. Merge moves physical PDF + .txt files to target directory
# ---------------------------------------------------------------------------


def test_merge_moves_physical_pdf_and_txt(client, db_session, tmp_path):
    """After merge, physical .pdf and .txt files are moved to target work's directory."""
    _seed_pdf_setting(db_session, str(tmp_path))

    source = _make_work(db_session, title="Source Paper", doi="10.1/src2")
    target = _make_work(db_session, title="Target Paper", doi="10.1/tgt2")

    # Create physical files under source's directory
    source_dir = tmp_path / str(source.id)
    source_dir.mkdir()
    pdf_file = source_dir / "paper.pdf"
    txt_file = source_dir / "paper.txt"
    pdf_file.write_bytes(b"%PDF-1.4 fake")
    txt_file.write_text("Extracted text content", encoding="utf-8")

    _make_pdf(db_session, source.id, filename="paper.pdf", extraction_status="ready")

    resp = client.post(f"/api/works/{target.id}/merge/{source.id}")
    assert resp.status_code == 200, resp.text

    # Files should exist at target location
    target_pdf = tmp_path / str(target.id) / "paper.pdf"
    target_txt = tmp_path / str(target.id) / "paper.txt"
    assert target_pdf.is_file(), "PDF not moved to target directory"
    assert target_txt.is_file(), "txt companion not moved to target directory"

    # Source files should be gone
    assert not pdf_file.exists(), "PDF still at source directory"
    assert not txt_file.exists(), "txt still at source directory"


# ---------------------------------------------------------------------------
# 4. Merge handles filename collision in target directory
# ---------------------------------------------------------------------------


def test_merge_renames_on_filename_collision(client, db_session, tmp_path):
    """If target already has a file with the same name, source file is renamed."""
    _seed_pdf_setting(db_session, str(tmp_path))

    source = _make_work(db_session, title="Source", doi="10.1/src3")
    target = _make_work(db_session, title="Target", doi="10.1/tgt3")

    # Both source and target have a PDF named "paper.pdf"
    source_dir = tmp_path / str(source.id)
    source_dir.mkdir()
    (source_dir / "paper.pdf").write_bytes(b"%PDF source")

    target_dir = tmp_path / str(target.id)
    target_dir.mkdir()
    (target_dir / "paper.pdf").write_bytes(b"%PDF target")

    _make_pdf(db_session, source.id, filename="paper.pdf")
    _make_pdf(db_session, target.id, filename="paper.pdf")

    resp = client.post(f"/api/works/{target.id}/merge/{source.id}")
    assert resp.status_code == 200, resp.text

    # Original target PDF intact
    assert (target_dir / "paper.pdf").is_file()
    assert (target_dir / "paper.pdf").read_bytes() == b"%PDF target"

    # Source PDF renamed to paper_1.pdf
    renamed = target_dir / "paper_1.pdf"
    assert renamed.is_file(), f"Renamed file not found; target_dir contents: {list(target_dir.iterdir())}"

    # DB row should point to target with new filename
    db_session.expire_all()
    from sqlalchemy import select
    pdfs = db_session.scalars(
        select(WorkPDF).where(WorkPDF.work_id == target.id).order_by(WorkPDF.id)
    ).all()
    filenames = {p.filename for p in pdfs}
    assert "paper_1.pdf" in filenames


# ---------------------------------------------------------------------------
# 5. Merge: source's primary PDF becomes target's primary if target has none
# ---------------------------------------------------------------------------


def test_merge_primary_pdf_transferred_when_target_has_none(client, db_session, tmp_path):
    """Source's primary PDF becomes primary on target when target has no PDFs."""
    _seed_pdf_setting(db_session, str(tmp_path))

    source = _make_work(db_session, title="Source", doi="10.1/s4")
    target = _make_work(db_session, title="Target", doi="10.1/t4")

    source_dir = tmp_path / str(source.id)
    source_dir.mkdir()
    (source_dir / "paper.pdf").write_bytes(b"%PDF")

    _make_pdf(db_session, source.id, filename="paper.pdf", is_primary=True)

    resp = client.post(f"/api/works/{target.id}/merge/{source.id}")
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    from sqlalchemy import select
    transferred = db_session.scalars(
        select(WorkPDF).where(WorkPDF.work_id == target.id)
    ).one()
    assert transferred.is_primary is True


# ---------------------------------------------------------------------------
# 6. Merge: source's primary PDF is NOT primary when target already has one
# ---------------------------------------------------------------------------


def test_merge_primary_not_transferred_when_target_already_has_one(client, db_session, tmp_path):
    """Source's primary PDF is made non-primary when target already has a primary."""
    _seed_pdf_setting(db_session, str(tmp_path))

    source = _make_work(db_session, title="Source", doi="10.1/s5")
    target = _make_work(db_session, title="Target", doi="10.1/t5")

    source_dir = tmp_path / str(source.id)
    source_dir.mkdir()
    (source_dir / "a.pdf").write_bytes(b"%PDF a")

    target_dir = tmp_path / str(target.id)
    target_dir.mkdir()
    (target_dir / "b.pdf").write_bytes(b"%PDF b")

    _make_pdf(db_session, source.id, filename="a.pdf", is_primary=True)
    _make_pdf(db_session, target.id, filename="b.pdf", is_primary=True)

    resp = client.post(f"/api/works/{target.id}/merge/{source.id}")
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    from sqlalchemy import select
    pdfs = db_session.scalars(
        select(WorkPDF).where(WorkPDF.work_id == target.id).order_by(WorkPDF.id)
    ).all()
    primary_count = sum(1 for p in pdfs if p.is_primary)
    assert primary_count == 1
    # The original target PDF (b.pdf) stays primary
    assert any(p.filename == "b.pdf" and p.is_primary for p in pdfs)


# ---------------------------------------------------------------------------
# 7. Multiple notes from source all repointed to target
# ---------------------------------------------------------------------------


def test_merge_all_notes_repointed(client, db_session, tmp_path):
    """All notes (user, AI, AI-reviewed) from source are repointed to target."""
    _seed_pdf_setting(db_session, str(tmp_path))

    source = _make_work(db_session, title="Source", doi="10.1/s6")
    target = _make_work(db_session, title="Target", doi="10.1/t6")

    n1 = _make_note(db_session, source.id, content="Note 1", provenance="user")
    n2 = _make_note(db_session, source.id, content="Note 2", provenance="ai")
    n3 = _make_note(db_session, source.id, content="Note 3", provenance="ai_reviewed")

    resp = client.post(f"/api/works/{target.id}/merge/{source.id}")
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    from sqlalchemy import select
    for note_id in [n1.id, n2.id, n3.id]:
        note = db_session.scalars(select(WorkNote).where(WorkNote.id == note_id)).one_or_none()
        assert note is not None, f"Note {note_id} was deleted"
        assert note.work_id == target.id


# ---------------------------------------------------------------------------
# 8. Chat endpoint sends correct text for the surviving work after merge
# ---------------------------------------------------------------------------


def test_chat_sends_correct_text_after_merge(client, db_session, tmp_path):
    """After merging source into target, chatting with target sends the
    (moved) source PDF text — not the missing-file fallback (text=None)."""
    _seed_pdf_setting(db_session, str(tmp_path))

    for key, value in [
        ("llm_provider", "anthropic"),
        ("llm_api_key", "test-key"),
        ("llm_model_id", "claude-test"),
        ("llm_base_url", ""),
    ]:
        db_session.add(Setting(key=key, value=value, description="test"))
    db_session.commit()

    source = _make_work(db_session, title="Source Paper", doi="10.1/chat_src")
    target = _make_work(db_session, title="Target Paper", doi="10.1/chat_tgt")

    # Physical files only exist under source's directory
    source_dir = tmp_path / str(source.id)
    source_dir.mkdir()
    pdf_file = source_dir / "paper.pdf"
    txt_file = source_dir / "paper.txt"
    pdf_file.write_bytes(b"%PDF-1.4")
    txt_file.write_text("The paper discusses X.", encoding="utf-8")

    _make_pdf(db_session, source.id, filename="paper.pdf", extraction_status="ready")

    # Merge source → target
    resp = client.post(f"/api/works/{target.id}/merge/{source.id}")
    assert resp.status_code == 200, resp.text

    # Now chat with target — the .txt should be found at target's directory
    with patch("sotascope.api.llm.make_llm_client") as mock_factory:
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "Reply."
        mock_factory.return_value = mock_llm

        chat_resp = client.post("/api/llm/chat", json={
            "papers": [{"work_id": target.id, "use_pdf": False}],
            "message": "What is this paper about?",
        })

    assert chat_resp.status_code == 200, chat_resp.text

    docs = mock_llm.chat.call_args.kwargs["context_documents"]
    assert len(docs) == 1
    assert docs[0].work_id == target.id
    # The text was found (not None) — merge moved the file correctly
    assert docs[0].text == "The paper discusses X.", (
        "Expected moved .txt content; got None — merge likely didn't move the file"
    )


# ---------------------------------------------------------------------------
# 9. Integrity check: reports WorkPDF row with missing physical file
# ---------------------------------------------------------------------------


def test_integrity_check_detects_missing_pdf_file(client, db_session, tmp_path):
    """pdf_rows_missing_file contains rows whose .pdf is not on disk."""
    _seed_pdf_setting(db_session, str(tmp_path))

    work = _make_work(db_session, title="No File Work")
    # DB row exists but no physical file
    _make_pdf(db_session, work.id, filename="ghost.pdf")

    resp = client.get("/api/works/integrity")
    assert resp.status_code == 200
    data = resp.json()

    missing = data["pdf_rows_missing_file"]
    assert any(r["work_id"] == work.id and r["filename"] == "ghost.pdf" for r in missing)


# ---------------------------------------------------------------------------
# 10. Integrity check: reports work directory on disk with no DB rows
# ---------------------------------------------------------------------------


def test_integrity_check_detects_work_dir_without_db_rows(client, db_session, tmp_path):
    """work_dirs_without_rows catches directories whose work no longer has PDF records."""
    _seed_pdf_setting(db_session, str(tmp_path))

    # Simulate a directory left over after a bad merge: work_id=9999 doesn't exist
    orphan_dir = tmp_path / "9999"
    orphan_dir.mkdir()
    (orphan_dir / "leftover.pdf").write_bytes(b"%PDF")

    resp = client.get("/api/works/integrity")
    assert resp.status_code == 200
    data = resp.json()

    dirs = data["work_dirs_without_rows"]
    assert any(d["work_id"] == 9999 for d in dirs)


# ---------------------------------------------------------------------------
# 11. Integrity check: reports WorkPDF with extraction_status=ready but no .txt
# ---------------------------------------------------------------------------


def test_integrity_check_detects_missing_txt(client, db_session, tmp_path):
    """pdf_rows_missing_txt catches 'ready' PDFs whose companion .txt is gone."""
    _seed_pdf_setting(db_session, str(tmp_path))

    work = _make_work(db_session, title="Missing Txt Work")
    work_dir = tmp_path / str(work.id)
    work_dir.mkdir()
    (work_dir / "paper.pdf").write_bytes(b"%PDF")
    # No paper.txt created

    _make_pdf(db_session, work.id, filename="paper.pdf", extraction_status="ready")

    resp = client.get("/api/works/integrity")
    assert resp.status_code == 200
    data = resp.json()

    missing_txt = data["pdf_rows_missing_txt"]
    assert any(r["work_id"] == work.id for r in missing_txt)


# ---------------------------------------------------------------------------
# 12. Integrity check: reports orphaned .txt without matching .pdf
# ---------------------------------------------------------------------------


def test_integrity_check_detects_orphaned_txt(client, db_session, tmp_path):
    """orphaned_txt_files catches .txt files with no matching .pdf in the same dir."""
    _seed_pdf_setting(db_session, str(tmp_path))

    work = _make_work(db_session, title="Orphan Txt Work")
    work_dir = tmp_path / str(work.id)
    work_dir.mkdir()
    # Only .txt, no .pdf
    (work_dir / "paper.txt").write_text("Orphaned text", encoding="utf-8")

    resp = client.get("/api/works/integrity")
    assert resp.status_code == 200
    data = resp.json()

    orphaned = data["orphaned_txt_files"]
    assert any("paper.txt" in p for p in orphaned)
