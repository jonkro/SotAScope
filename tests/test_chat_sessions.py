"""Tests for the chat session persistence API and LLM chat auto-save.

Covers:
  1. Create auto-session; second request for same scope returns existing
  2. Add messages via chat endpoint; retrieve session with messages in order
  3. Save session — creates new non-auto copy, original unchanged
  4. List sessions — both auto and saved, ordered by updated_at desc
  5. Delete session — messages cascade-deleted
  6. New chat (clear messages) — session exists but empty
  7. Chat endpoint with session_id — messages auto-persisted
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from litexplorer.models.chat import ChatMessage, ChatSession
from litexplorer.models.library import Work, WorkPDF
from litexplorer.models.project import Project
from litexplorer.models.settings import Setting


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_work(db_session, title="Test Paper", doi_suffix="test"):
    w = Work(title=title, doi=f"10.9999/{doi_suffix}", publication_year=2024)
    db_session.add(w)
    db_session.commit()
    return w


def _seed_llm_settings(db_session, provider="anthropic", model_id="claude-test"):
    for key, value in [
        ("llm_provider", provider),
        ("llm_api_key", "test-key"),
        ("llm_model_id", model_id),
        ("llm_base_url", ""),
    ]:
        db_session.add(Setting(key=key, value=value, description="test"))
    db_session.commit()


def _make_pdf(db_session, work_id, filename="paper.pdf", extraction_status="ready"):
    pdf = WorkPDF(
        work_id=work_id,
        filename=filename,
        is_primary=True,
        extraction_status=extraction_status,
    )
    db_session.add(pdf)
    db_session.commit()
    return pdf


# ---------------------------------------------------------------------------
# 1. Create auto-session; uniqueness — second call returns existing
# ---------------------------------------------------------------------------


def test_auto_session_create_and_return_existing(client, db_session):
    """POST /auto creates a session. A second call for the same scope returns the same id."""
    work = _make_work(db_session)

    resp1 = client.post("/api/chat/sessions/auto", json={"work_id": work.id, "project_id": None})
    assert resp1.status_code == 200
    session1 = resp1.json()
    assert session1["is_auto"] is True
    assert session1["work_id"] == work.id
    assert session1["project_id"] is None
    assert session1["message_count"] == 0
    assert session1["messages"] == []

    # Second call — must return the same session
    resp2 = client.post("/api/chat/sessions/auto", json={"work_id": work.id, "project_id": None})
    assert resp2.status_code == 200
    session2 = resp2.json()
    assert session2["id"] == session1["id"], "Second auto-session call should return existing session"


def test_auto_session_project_scope_separate_from_library(client, db_session):
    """Auto-sessions for library scope (project_id=None) and project scope are distinct."""
    work = _make_work(db_session, doi_suffix="scope_test")
    project = Project(name="Test Project")
    db_session.add(project)
    db_session.commit()

    resp_lib = client.post("/api/chat/sessions/auto", json={"work_id": work.id, "project_id": None})
    assert resp_lib.status_code == 200

    resp_proj = client.post("/api/chat/sessions/auto", json={"work_id": work.id, "project_id": project.id})
    assert resp_proj.status_code == 200

    # Different scopes → different sessions
    assert resp_lib.json()["id"] != resp_proj.json()["id"]


# ---------------------------------------------------------------------------
# 2. Add messages to a session; retrieve with messages in order
# ---------------------------------------------------------------------------


def test_get_session_with_messages(client, db_session):
    """GET /sessions/{id} returns session with messages ordered by creation."""
    work = _make_work(db_session, doi_suffix="msgs")

    # Create auto-session
    resp = client.post("/api/chat/sessions/auto", json={"work_id": work.id, "project_id": None})
    session_id = resp.json()["id"]

    # Insert messages directly into DB to simulate persistence
    db_session.add(ChatMessage(session_id=session_id, role="user", content="Hello"))
    db_session.add(ChatMessage(session_id=session_id, role="assistant", content="Hi there"))
    db_session.add(ChatMessage(session_id=session_id, role="user", content="What is this paper about?"))
    db_session.commit()

    resp2 = client.get(f"/api/chat/sessions/{session_id}")
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["message_count"] == 3
    assert len(data["messages"]) == 3
    assert data["messages"][0]["role"] == "user"
    assert data["messages"][0]["content"] == "Hello"
    assert data["messages"][1]["role"] == "assistant"
    assert data["messages"][2]["content"] == "What is this paper about?"


def test_get_session_not_found(client, db_session):
    """GET /sessions/{id} returns 404 for a non-existent session."""
    resp = client.get("/api/chat/sessions/99999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3. Save session — creates new non-auto copy, original unchanged
# ---------------------------------------------------------------------------


def test_save_session(client, db_session):
    """POST /sessions/{id}/save creates a named non-auto copy with messages."""
    work = _make_work(db_session, doi_suffix="save_test")

    resp = client.post("/api/chat/sessions/auto", json={"work_id": work.id, "project_id": None})
    session_id = resp.json()["id"]

    # Add messages to the auto-session
    db_session.add(ChatMessage(session_id=session_id, role="user", content="Question"))
    db_session.add(ChatMessage(session_id=session_id, role="assistant", content="Answer"))
    db_session.commit()

    # Save the session
    save_resp = client.post(
        f"/api/chat/sessions/{session_id}/save",
        json={"title": "My saved discussion"},
    )
    assert save_resp.status_code == 200
    saved = save_resp.json()
    assert saved["is_auto"] is False
    assert saved["title"] == "My saved discussion"
    assert saved["work_id"] == work.id
    assert saved["message_count"] == 2
    # The saved session should have its own id
    assert saved["id"] != session_id

    # Original auto-session unchanged
    orig = client.get(f"/api/chat/sessions/{session_id}")
    assert orig.json()["id"] == session_id
    assert orig.json()["is_auto"] is True
    assert orig.json()["message_count"] == 2


def test_save_session_empty_title_returns_400(client, db_session):
    """POST /sessions/{id}/save returns 400 when title is empty."""
    work = _make_work(db_session, doi_suffix="empty_title")
    resp = client.post("/api/chat/sessions/auto", json={"work_id": work.id, "project_id": None})
    session_id = resp.json()["id"]

    save_resp = client.post(f"/api/chat/sessions/{session_id}/save", json={"title": "  "})
    assert save_resp.status_code == 400


# ---------------------------------------------------------------------------
# 4. List sessions — both auto and saved, ordered by updated_at desc
# ---------------------------------------------------------------------------


def test_list_sessions(client, db_session):
    """GET /sessions returns all sessions for the scope, most recently updated first."""
    work = _make_work(db_session, doi_suffix="list_test")

    # Create auto-session
    client.post("/api/chat/sessions/auto", json={"work_id": work.id, "project_id": None})

    # Get auto-session id
    list_resp = client.get(f"/api/chat/sessions?work_id={work.id}")
    assert list_resp.status_code == 200
    sessions = list_resp.json()
    assert len(sessions) == 1
    session_id = sessions[0]["id"]

    # Add a message so we can save
    db_session.add(ChatMessage(session_id=session_id, role="user", content="Hi"))
    db_session.add(ChatMessage(session_id=session_id, role="assistant", content="Hello"))
    db_session.commit()

    # Save the session
    client.post(f"/api/chat/sessions/{session_id}/save", json={"title": "Saved"})

    # Now list should show 2 sessions
    list_resp2 = client.get(f"/api/chat/sessions?work_id={work.id}")
    sessions2 = list_resp2.json()
    assert len(sessions2) == 2

    # Auto and saved present
    auto_sessions = [s for s in sessions2 if s["is_auto"]]
    saved_sessions = [s for s in sessions2 if not s["is_auto"]]
    assert len(auto_sessions) == 1
    assert len(saved_sessions) == 1
    assert saved_sessions[0]["title"] == "Saved"

    # message_count included in listing (without full messages)
    assert all("message_count" in s for s in sessions2)
    assert all("messages" not in s or s["messages"] == [] for s in sessions2)


# ---------------------------------------------------------------------------
# 5. Delete session — messages cascade-deleted
# ---------------------------------------------------------------------------


def test_delete_session(client, db_session):
    """DELETE /sessions/{id} removes session; messages are cascade-deleted."""
    work = _make_work(db_session, doi_suffix="delete_test")

    resp = client.post("/api/chat/sessions/auto", json={"work_id": work.id, "project_id": None})
    session_id = resp.json()["id"]

    db_session.add(ChatMessage(session_id=session_id, role="user", content="Bye"))
    db_session.commit()

    del_resp = client.delete(f"/api/chat/sessions/{session_id}")
    assert del_resp.status_code == 204

    # Session gone
    get_resp = client.get(f"/api/chat/sessions/{session_id}")
    assert get_resp.status_code == 404

    # Messages also gone
    remaining = db_session.query(ChatMessage).filter_by(session_id=session_id).all()
    assert remaining == []


def test_delete_session_not_found(client, db_session):
    """DELETE /sessions/{id} returns 404 for non-existent session."""
    resp = client.delete("/api/chat/sessions/99999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 6. New chat (clear messages) — session exists but empty
# ---------------------------------------------------------------------------


def test_clear_messages(client, db_session):
    """DELETE /sessions/{id}/messages clears messages; session itself survives."""
    work = _make_work(db_session, doi_suffix="clear_test")

    resp = client.post("/api/chat/sessions/auto", json={"work_id": work.id, "project_id": None})
    session_id = resp.json()["id"]

    db_session.add(ChatMessage(session_id=session_id, role="user", content="Q1"))
    db_session.add(ChatMessage(session_id=session_id, role="assistant", content="A1"))
    db_session.commit()

    clear_resp = client.delete(f"/api/chat/sessions/{session_id}/messages")
    assert clear_resp.status_code == 200
    assert clear_resp.json()["cleared"] is True

    # Session still exists
    get_resp = client.get(f"/api/chat/sessions/{session_id}")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["message_count"] == 0
    assert data["messages"] == []

    # Can create new auto-session for the same scope — should return the same id
    resp2 = client.post("/api/chat/sessions/auto", json={"work_id": work.id, "project_id": None})
    assert resp2.json()["id"] == session_id


# ---------------------------------------------------------------------------
# 7. Chat endpoint with session_id — messages auto-persisted
# ---------------------------------------------------------------------------


def test_chat_with_session_id_auto_persists(client, db_session):
    """POST /api/llm/chat with session_id saves user+assistant messages to the session."""
    _seed_llm_settings(db_session)
    work = _make_work(db_session, doi_suffix="chat_persist")
    _make_pdf(db_session, work.id)

    # Create auto-session
    resp = client.post("/api/chat/sessions/auto", json={"work_id": work.id, "project_id": None})
    session_id = resp.json()["id"]

    with patch("litexplorer.api.llm.make_llm_client") as mock_factory:
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "This paper is about X."
        mock_factory.return_value = mock_llm

        with patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", return_value="Extracted text"):
            resp = client.post("/api/llm/chat", json={
                "session_id": session_id,
                "papers": [{"work_id": work.id, "use_pdf": False}],
                "history": [],
                "message": "What is this paper about?",
            })

    assert resp.status_code == 200
    assert resp.json()["reply"] == "This paper is about X."

    # Both messages persisted in the session
    get_resp = client.get(f"/api/chat/sessions/{session_id}")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["message_count"] == 2
    msgs = data["messages"]
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "What is this paper about?"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "This paper is about X."


def test_auto_session_project_only_no_work(client, db_session):
    """POST /auto with work_id=None creates a project-scoped session."""
    project = Project(name="Test Project")
    db_session.add(project)
    db_session.commit()

    resp = client.post("/api/chat/sessions/auto", json={"work_id": None, "project_id": project.id})
    assert resp.status_code == 200
    session = resp.json()
    assert session["is_auto"] is True
    assert session["work_id"] is None
    assert session["project_id"] == project.id
    assert session["message_count"] == 0

    # Second call returns the same session
    resp2 = client.post("/api/chat/sessions/auto", json={"work_id": None, "project_id": project.id})
    assert resp2.status_code == 200
    assert resp2.json()["id"] == session["id"]


def test_list_sessions_project_only(client, db_session):
    """GET /sessions?project_id=N with no work_id lists project-scoped sessions."""
    project = Project(name="Proj")
    db_session.add(project)
    db_session.commit()

    client.post("/api/chat/sessions/auto", json={"work_id": None, "project_id": project.id})

    resp = client.get(f"/api/chat/sessions?project_id={project.id}")
    assert resp.status_code == 200
    sessions = resp.json()
    assert len(sessions) == 1
    assert sessions[0]["work_id"] is None
    assert sessions[0]["project_id"] == project.id


def test_save_session_project_only(client, db_session):
    """Saving a project-only session (work_id=None) creates a named copy."""
    project = Project(name="ProjSave")
    db_session.add(project)
    db_session.commit()

    resp = client.post("/api/chat/sessions/auto", json={"work_id": None, "project_id": project.id})
    session_id = resp.json()["id"]

    db_session.add(ChatMessage(session_id=session_id, role="user", content="Q"))
    db_session.add(ChatMessage(session_id=session_id, role="assistant", content="A"))
    db_session.commit()

    save_resp = client.post(f"/api/chat/sessions/{session_id}/save", json={"title": "Project discussion"})
    assert save_resp.status_code == 200
    saved = save_resp.json()
    assert saved["is_auto"] is False
    assert saved["title"] == "Project discussion"
    assert saved["work_id"] is None
    assert saved["project_id"] == project.id
    assert saved["message_count"] == 2


# ---------------------------------------------------------------------------
# context_type / context_id tests
# ---------------------------------------------------------------------------


def test_auto_session_extraction_schema_with_context_id(client, db_session):
    """context_type='extraction_schema' + context_id scopes the auto-session correctly."""
    resp = client.post("/api/chat/sessions/auto", json={
        "context_type": "extraction_schema",
        "context_id": 42,
    })
    assert resp.status_code == 200
    session = resp.json()
    assert session["context_type"] == "extraction_schema"
    assert session["context_id"] == 42
    assert session["work_id"] is None
    assert session["project_id"] is None
    assert session["is_auto"] is True

    # Second call with same scope returns existing session.
    resp2 = client.post("/api/chat/sessions/auto", json={
        "context_type": "extraction_schema",
        "context_id": 42,
    })
    assert resp2.status_code == 200
    assert resp2.json()["id"] == session["id"], "Same scope should return existing session"

    # Different context_id → different session.
    resp3 = client.post("/api/chat/sessions/auto", json={
        "context_type": "extraction_schema",
        "context_id": 99,
    })
    assert resp3.status_code == 200
    assert resp3.json()["id"] != session["id"], "Different context_id must yield a distinct session"


def test_auto_session_extraction_schema_no_context_id(client, db_session):
    """context_type='extraction_schema' with no context_id (new/unsaved schema) works."""
    resp = client.post("/api/chat/sessions/auto", json={
        "context_type": "extraction_schema",
    })
    assert resp.status_code == 200
    session = resp.json()
    assert session["context_type"] == "extraction_schema"
    assert session["context_id"] is None

    # Second call with same scope returns same session.
    resp2 = client.post("/api/chat/sessions/auto", json={
        "context_type": "extraction_schema",
    })
    assert resp2.status_code == 200
    assert resp2.json()["id"] == session["id"]


def test_existing_paper_sessions_default_context_fields(client, db_session):
    """Paper-based sessions created without context_type default to 'papers'/None."""
    work = _make_work(db_session, doi_suffix="ctx_default")
    resp = client.post("/api/chat/sessions/auto", json={"work_id": work.id})
    assert resp.status_code == 200
    session = resp.json()
    assert session["context_type"] == "papers"
    assert session["context_id"] is None


def test_chat_without_session_id_no_error(client, db_session):
    """POST /api/llm/chat without session_id still works (no persistence attempted)."""
    _seed_llm_settings(db_session)
    work = _make_work(db_session, doi_suffix="no_session")
    _make_pdf(db_session, work.id)

    with patch("litexplorer.api.llm.make_llm_client") as mock_factory:
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "Reply."
        mock_factory.return_value = mock_llm

        with patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", return_value="Text"):
            resp = client.post("/api/llm/chat", json={
                "papers": [{"work_id": work.id}],
                "message": "Explain.",
            })

    assert resp.status_code == 200
    assert resp.json()["reply"] == "Reply."
