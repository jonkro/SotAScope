"""Tests for POST /api/llm/chat endpoint.

All LLMClient.chat() calls and file I/O are mocked — no real API calls or files.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sotascope.models.library import Work, WorkPDF
from sotascope.models.settings import Setting


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_llm_settings(db_session, provider="anthropic", model_id="claude-test"):
    """Insert the four LLM settings rows into the DB."""
    for key, value in [
        ("llm_provider", provider),
        ("llm_api_key", "test-key"),
        ("llm_model_id", model_id),
        ("llm_base_url", ""),
    ]:
        db_session.add(Setting(key=key, value=value, description="test"))
    db_session.commit()


def _make_work(db_session, title="Test Paper", year=2023):
    w = Work(title=title, doi=f"10.1234/{title.replace(' ', '_')}", publication_year=year)
    db_session.add(w)
    db_session.commit()
    return w


def _make_pdf(db_session, work_id, filename="paper.pdf", is_primary=True, extraction_status="ready"):
    pdf = WorkPDF(
        work_id=work_id,
        filename=filename,
        is_primary=is_primary,
        extraction_status=extraction_status,
    )
    db_session.add(pdf)
    db_session.commit()
    return pdf


# ---------------------------------------------------------------------------
# 1. Happy path — text context, history passed through, reply returned
# ---------------------------------------------------------------------------


def test_chat_happy_path_text_context(client, db_session):
    """Text context assembled from .txt file and passed to LLMClient; reply returned."""
    _seed_llm_settings(db_session)
    work = _make_work(db_session)
    _make_pdf(db_session, work.id, filename="paper.pdf", extraction_status="ready")

    with patch("sotascope.api.llm.make_llm_client") as mock_factory:
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "This paper is about X."
        mock_factory.return_value = mock_llm

        with patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", return_value="Extracted paper text"):
            resp = client.post("/api/llm/chat", json={
                "papers": [{"work_id": work.id, "use_pdf": False}],
                "history": [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi there"},
                ],
                "message": "What is this paper about?",
            })

    assert resp.status_code == 200
    assert resp.json() == {"reply": "This paper is about X."}

    # Verify history + current message were passed
    call_kwargs = mock_llm.chat.call_args.kwargs
    messages = call_kwargs["messages"]
    assert messages[-1] == {"role": "user", "content": "What is this paper about?"}
    assert messages[0] == {"role": "user", "content": "Hello"}
    assert messages[1] == {"role": "assistant", "content": "Hi there"}

    # Verify context document was assembled
    docs = call_kwargs["context_documents"]
    assert len(docs) == 1
    assert docs[0].work_id == work.id
    assert docs[0].text == "Extracted paper text"
    assert docs[0].pdf_bytes is None


# ---------------------------------------------------------------------------
# 2. PDF context on Anthropic provider
# ---------------------------------------------------------------------------


def test_chat_pdf_context_anthropic(client, db_session):
    """PDF bytes read from disk and passed to LLMClient.chat() for Anthropic provider."""
    _seed_llm_settings(db_session, provider="anthropic")
    work = _make_work(db_session)
    _make_pdf(db_session, work.id, filename="paper.pdf", extraction_status="ready")

    fake_pdf_bytes = b"%PDF-1.4 fake content"

    with patch("sotascope.api.llm.make_llm_client") as mock_factory:
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "PDF summary."
        mock_factory.return_value = mock_llm

        with patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_bytes", return_value=fake_pdf_bytes):
            resp = client.post("/api/llm/chat", json={
                "papers": [{"work_id": work.id, "use_pdf": True}],
                "message": "Summarize this paper.",
            })

    assert resp.status_code == 200
    assert resp.json() == {"reply": "PDF summary."}

    docs = mock_llm.chat.call_args.kwargs["context_documents"]
    assert len(docs) == 1
    assert docs[0].pdf_bytes == fake_pdf_bytes
    assert docs[0].text is None


# ---------------------------------------------------------------------------
# 3. PDF context on non-Anthropic provider → HTTP 400
# ---------------------------------------------------------------------------


def test_chat_pdf_context_non_anthropic_returns_400(client, db_session):
    """use_pdf=True with a non-Anthropic provider returns HTTP 400."""
    _seed_llm_settings(db_session, provider="openai", model_id="gpt-4o")
    work = _make_work(db_session)

    resp = client.post("/api/llm/chat", json={
        "papers": [{"work_id": work.id, "use_pdf": True}],
        "message": "Discuss.",
    })

    assert resp.status_code == 400
    assert "PDF vision requires Anthropic provider" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 4. llm_provider not configured → HTTP 400
# ---------------------------------------------------------------------------


def test_chat_provider_not_configured(client, db_session):
    """Returns HTTP 400 when llm_provider is not set in the DB."""
    # No settings rows at all
    resp = client.post("/api/llm/chat", json={"message": "Hello."})
    assert resp.status_code == 400
    assert "provider" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 5. llm_model_id not configured → HTTP 400
# ---------------------------------------------------------------------------


def test_chat_model_not_configured(client, db_session):
    """Returns HTTP 400 when llm_model_id is empty."""
    db_session.add(Setting(key="llm_provider", value="anthropic", description="test"))
    db_session.add(Setting(key="llm_api_key", value="key", description="test"))
    db_session.add(Setting(key="llm_model_id", value="", description="test"))
    db_session.commit()

    resp = client.post("/api/llm/chat", json={"message": "Hello."})
    assert resp.status_code == 400
    assert "model" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 6. Paper with no extracted text (extraction_status != "ready") — not an error
# ---------------------------------------------------------------------------


def test_chat_paper_no_extracted_text(client, db_session):
    """Work with pending/failed extraction gets text=None; frontend prevents sending such papers
    but backend still handles it gracefully (LLMClient reports 'No content available')."""
    _seed_llm_settings(db_session)
    work = _make_work(db_session)
    _make_pdf(db_session, work.id, filename="paper.pdf", extraction_status="pending")

    with patch("sotascope.api.llm.make_llm_client") as mock_factory:
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "Some reply."
        mock_factory.return_value = mock_llm

        resp = client.post("/api/llm/chat", json={
            "papers": [{"work_id": work.id, "use_pdf": False}],
            "message": "Tell me about this.",
        })

    assert resp.status_code == 200
    assert resp.json() == {"reply": "Some reply."}

    docs = mock_llm.chat.call_args.kwargs["context_documents"]
    assert len(docs) == 1
    assert docs[0].text is None
    assert docs[0].pdf_bytes is None


# ---------------------------------------------------------------------------
# 7. use_pdf=True but no PDF file on disk — falls back to no content
# ---------------------------------------------------------------------------


def test_chat_pdf_file_missing_on_disk(client, db_session):
    """use_pdf=True with no PDF file on disk: pdf_bytes=None, not an error."""
    _seed_llm_settings(db_session, provider="anthropic")
    work = _make_work(db_session)
    _make_pdf(db_session, work.id, filename="missing.pdf", extraction_status="ready")

    with patch("sotascope.api.llm.make_llm_client") as mock_factory:
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "Reply without PDF."
        mock_factory.return_value = mock_llm

        # is_file() returns False → file not found on disk
        with patch("pathlib.Path.is_file", return_value=False):
            resp = client.post("/api/llm/chat", json={
                "papers": [{"work_id": work.id, "use_pdf": True}],
                "message": "Discuss.",
            })

    assert resp.status_code == 200
    assert resp.json() == {"reply": "Reply without PDF."}

    docs = mock_llm.chat.call_args.kwargs["context_documents"]
    assert docs[0].pdf_bytes is None
    assert docs[0].text is None


# ---------------------------------------------------------------------------
# 8. Unknown work_id → HTTP 404
# ---------------------------------------------------------------------------


def test_chat_unknown_work_id(client, db_session):
    """Returns HTTP 404 when a work_id does not exist."""
    _seed_llm_settings(db_session)

    resp = client.post("/api/llm/chat", json={
        "papers": [{"work_id": 99999, "use_pdf": False}],
        "message": "Hello.",
    })

    assert resp.status_code == 404
    assert "99999" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 9. SDK error from LLMClient.chat() → HTTP 502
# ---------------------------------------------------------------------------


def test_chat_sdk_error_returns_502(client, db_session):
    """Returns HTTP 502 when LLMClient.chat() raises an exception."""
    _seed_llm_settings(db_session)
    work = _make_work(db_session)

    with patch("sotascope.api.llm.make_llm_client") as mock_factory:
        mock_llm = MagicMock()
        mock_llm.chat.side_effect = Exception("Rate limit exceeded")
        mock_factory.return_value = mock_llm

        resp = client.post("/api/llm/chat", json={
            "papers": [{"work_id": work.id}],
            "message": "Hello.",
        })

    assert resp.status_code == 502
    assert "Rate limit exceeded" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 10. Library mode (project_id=None, single paper)
# ---------------------------------------------------------------------------


def test_chat_library_mode(client, db_session):
    """project_id=None (library mode) works correctly — paper context assembled."""
    _seed_llm_settings(db_session)
    work = _make_work(db_session, title="Library Paper", year=2021)
    _make_pdf(db_session, work.id, filename="lib.pdf", extraction_status="ready")

    with patch("sotascope.api.llm.make_llm_client") as mock_factory:
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "Library reply."
        mock_factory.return_value = mock_llm

        with patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", return_value="Library paper text"):
            resp = client.post("/api/llm/chat", json={
                "project_id": None,
                "papers": [{"work_id": work.id}],
                "message": "Explain.",
            })

    assert resp.status_code == 200
    assert resp.json() == {"reply": "Library reply."}

    docs = mock_llm.chat.call_args.kwargs["context_documents"]
    assert docs[0].title == "Library Paper"
    assert docs[0].year == 2021
    assert docs[0].text == "Library paper text"


# ---------------------------------------------------------------------------
# 11. Project mode (project_id set, multiple papers)
# ---------------------------------------------------------------------------


def test_chat_project_mode_multiple_papers(client, db_session):
    """project_id set, multiple papers: all documents assembled and passed to chat()."""
    _seed_llm_settings(db_session)
    work1 = _make_work(db_session, title="Paper One")
    work2 = _make_work(db_session, title="Paper Two")
    _make_pdf(db_session, work1.id, filename="one.pdf", extraction_status="ready")
    _make_pdf(db_session, work2.id, filename="two.pdf", extraction_status="failed")

    with patch("sotascope.api.llm.make_llm_client") as mock_factory:
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "Combined reply."
        mock_factory.return_value = mock_llm

        with patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", return_value="Text for paper one"):
            resp = client.post("/api/llm/chat", json={
                "project_id": 1,
                "papers": [
                    {"work_id": work1.id, "use_pdf": False, "remark": "Focus on method"},
                    {"work_id": work2.id, "use_pdf": False},
                ],
                "message": "Compare these papers.",
            })

    assert resp.status_code == 200
    assert resp.json() == {"reply": "Combined reply."}

    docs = mock_llm.chat.call_args.kwargs["context_documents"]
    assert len(docs) == 2

    # work1 has ready PDF → text populated
    doc1 = next(d for d in docs if d.work_id == work1.id)
    assert doc1.text == "Text for paper one"
    assert doc1.remark == "Focus on method"

    # work2 has failed extraction → text=None
    doc2 = next(d for d in docs if d.work_id == work2.id)
    assert doc2.text is None
    assert doc2.remark is None
