"""Tests for the work notes API endpoints."""

import pytest

from litexplorer.models.library import Work, WorkNote
from litexplorer.models.project import Project, TopicList, TopicListWork


@pytest.fixture()
def work(db_session):
    """Create a test work."""
    w = Work(title="Test Paper", doi="10.1234/test")
    db_session.add(w)
    db_session.commit()
    return w


@pytest.fixture()
def project(db_session):
    """Create a test project."""
    p = Project(name="Test Project")
    db_session.add(p)
    db_session.commit()
    return p


def test_create_general_note(client, work):
    resp = client.post(f"/api/works/{work.id}/notes", json={
        "content": "This paper introduces a novel approach.",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["content"] == "This paper introduces a novel approach."
    assert data["project_id"] is None
    assert data["provenance"] == "user"
    assert data["model_id"] is None
    assert data["is_outdated"] is False
    assert data["note_type"] is None


def test_create_project_scoped_note(client, work, project):
    resp = client.post(f"/api/works/{work.id}/notes", json={
        "content": "Relevant to our ML pipeline.",
        "note_type": "key insight",
        "project_id": project.id,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["project_id"] == project.id
    assert data["note_type"] == "key insight"
    assert data["provenance"] == "user"


def test_fetch_notes_without_project_id_returns_general_only(client, work, project):
    # Create a general note and a project-scoped note
    client.post(f"/api/works/{work.id}/notes", json={"content": "General note"})
    client.post(f"/api/works/{work.id}/notes", json={
        "content": "Project note",
        "project_id": project.id,
    })

    resp = client.get(f"/api/works/{work.id}/notes")
    assert resp.status_code == 200
    notes = resp.json()
    assert len(notes) == 1
    assert notes[0]["content"] == "General note"
    assert notes[0]["project_id"] is None


def test_fetch_notes_with_project_id_returns_both(client, work, project):
    # Create a general note and a project-scoped note
    client.post(f"/api/works/{work.id}/notes", json={"content": "General note"})
    client.post(f"/api/works/{work.id}/notes", json={
        "content": "Project note",
        "project_id": project.id,
    })

    resp = client.get(f"/api/works/{work.id}/notes", params={"project_id": project.id})
    assert resp.status_code == 200
    notes = resp.json()
    assert len(notes) == 2
    contents = {n["content"] for n in notes}
    assert contents == {"General note", "Project note"}


def test_update_user_note(client, work):
    create_resp = client.post(f"/api/works/{work.id}/notes", json={"content": "Original"})
    note_id = create_resp.json()["id"]

    update_resp = client.patch(f"/api/works/{work.id}/notes/{note_id}", json={
        "content": "Updated content",
        "note_type": "method",
    })
    assert update_resp.status_code == 200
    data = update_resp.json()
    assert data["content"] == "Updated content"
    assert data["note_type"] == "method"
    assert data["provenance"] == "user"


def test_update_ai_note_becomes_ai_reviewed(client, work, db_session):
    # Directly create an AI note in the DB (no endpoint sets provenance=ai in phase 1)
    note = WorkNote(
        work_id=work.id,
        content="AI generated insight",
        provenance="ai",
        model_id="claude-sonnet-4-5",
    )
    db_session.add(note)
    db_session.commit()

    update_resp = client.patch(f"/api/works/{work.id}/notes/{note.id}", json={
        "content": "Edited AI insight",
    })
    assert update_resp.status_code == 200
    data = update_resp.json()
    assert data["provenance"] == "ai_reviewed"
    assert data["content"] == "Edited AI insight"
    assert data["model_id"] == "claude-sonnet-4-5"


def test_delete_note(client, work):
    create_resp = client.post(f"/api/works/{work.id}/notes", json={"content": "To delete"})
    note_id = create_resp.json()["id"]

    del_resp = client.delete(f"/api/works/{work.id}/notes/{note_id}")
    assert del_resp.status_code == 204

    # Verify it's gone
    list_resp = client.get(f"/api/works/{work.id}/notes")
    assert len(list_resp.json()) == 0


def test_delete_work_cascades_notes(client, work, db_session):
    client.post(f"/api/works/{work.id}/notes", json={"content": "Will be cascade deleted"})

    # Delete the work
    del_resp = client.delete(f"/api/works/{work.id}")
    assert del_resp.status_code == 204

    # Verify notes are gone
    remaining = db_session.query(WorkNote).filter_by(work_id=work.id).all()
    assert len(remaining) == 0


def test_mark_note_outdated(client, work):
    create_resp = client.post(f"/api/works/{work.id}/notes", json={"content": "Some note"})
    note_id = create_resp.json()["id"]

    update_resp = client.patch(f"/api/works/{work.id}/notes/{note_id}", json={
        "is_outdated": True,
    })
    assert update_resp.status_code == 200
    assert update_resp.json()["is_outdated"] is True

    # Flip back
    update_resp2 = client.patch(f"/api/works/{work.id}/notes/{note_id}", json={
        "is_outdated": False,
    })
    assert update_resp2.json()["is_outdated"] is False


def test_note_on_nonexistent_work(client):
    resp = client.post("/api/works/99999/notes", json={"content": "No work"})
    assert resp.status_code == 404


def test_update_nonexistent_note(client, work):
    resp = client.patch(f"/api/works/{work.id}/notes/99999", json={"content": "x"})
    assert resp.status_code == 404


def test_delete_nonexistent_note(client, work):
    resp = client.delete(f"/api/works/{work.id}/notes/99999")
    assert resp.status_code == 404


# ---- Project-level notes endpoint ----


def test_project_notes_returns_scoped_and_general(client, db_session, work, project):
    """Project notes endpoint returns project-scoped + general notes for seed works."""
    # Add work to a topic list in the project
    tl = TopicList(project_id=project.id, name="TL1", color="#ff0000")
    db_session.add(tl)
    db_session.commit()
    tlw = TopicListWork(topic_list_id=tl.id, work_id=work.id)
    db_session.add(tlw)
    db_session.commit()

    # Create a general note on the seed work
    client.post(f"/api/works/{work.id}/notes", json={"content": "General seed note"})
    # Create a project-scoped note
    client.post(f"/api/works/{work.id}/notes", json={
        "content": "Project note",
        "project_id": project.id,
    })

    resp = client.get(f"/api/projects/{project.id}/notes")
    assert resp.status_code == 200
    notes = resp.json()
    assert len(notes) == 2
    contents = {n["content"] for n in notes}
    assert contents == {"General seed note", "Project note"}
    # Each note should have work metadata
    for n in notes:
        assert n["work_title"] == "Test Paper"


def test_project_notes_excludes_general_notes_for_non_seed_works(client, db_session, project):
    """General notes on works NOT in the project's topic lists are excluded."""
    other_work = Work(title="Other Paper", doi="10.1234/other")
    db_session.add(other_work)
    db_session.commit()

    # Create a general note on a work NOT in the project
    client.post(f"/api/works/{other_work.id}/notes", json={"content": "Unrelated note"})

    resp = client.get(f"/api/projects/{project.id}/notes")
    assert resp.status_code == 200
    assert len(resp.json()) == 0


def test_project_notes_nonexistent_project(client):
    resp = client.get("/api/projects/99999/notes")
    assert resp.status_code == 404
