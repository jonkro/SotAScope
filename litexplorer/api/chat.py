"""Chat session persistence API.

Endpoints:
  POST /api/chat/sessions/auto            — get or create the auto-session for a scope
  GET  /api/chat/sessions                  — list sessions for a work+project scope
  GET  /api/chat/sessions/{id}             — load session with messages
  POST /api/chat/sessions/{id}/save        — snapshot as a named saved session
  DELETE /api/chat/sessions/{id}           — delete a session
  DELETE /api/chat/sessions/{id}/messages  — clear messages (reset auto-session)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from litexplorer.api.deps import get_db
from litexplorer.models.chat import ChatMessage, ChatSession

router = APIRouter(prefix="/api/chat", tags=["chat"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ChatSessionMessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ChatSessionOut(BaseModel):
    id: int
    work_id: int
    project_id: Optional[int]
    title: Optional[str]
    is_auto: bool
    message_count: int
    created_at: datetime
    updated_at: datetime
    messages: list[ChatSessionMessageOut] = []

    model_config = ConfigDict(from_attributes=True)


class AutoSessionRequest(BaseModel):
    work_id: int
    project_id: Optional[int] = None


class SaveSessionRequest(BaseModel):
    title: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session_out(session: ChatSession, include_messages: bool = True) -> ChatSessionOut:
    msgs = list(session.messages) if include_messages else []
    return ChatSessionOut(
        id=session.id,
        work_id=session.work_id,
        project_id=session.project_id,
        title=session.title,
        is_auto=session.is_auto,
        message_count=len(session.messages),
        created_at=session.created_at,
        updated_at=session.updated_at,
        messages=[
            ChatSessionMessageOut(
                id=m.id,
                role=m.role,
                content=m.content,
                created_at=m.created_at,
            )
            for m in msgs
        ],
    )


def _find_auto_session(
    db: Session, work_id: int, project_id: int | None
) -> ChatSession | None:
    """Return the auto-session for the given scope, or None if it doesn't exist."""
    stmt = (
        select(ChatSession)
        .where(
            ChatSession.work_id == work_id,
            ChatSession.is_auto == True,  # noqa: E712
        )
    )
    if project_id is None:
        stmt = stmt.where(ChatSession.project_id.is_(None))
    else:
        stmt = stmt.where(ChatSession.project_id == project_id)
    return db.scalars(stmt).one_or_none()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/sessions/auto", response_model=ChatSessionOut)
def get_or_create_auto_session(
    body: AutoSessionRequest, db: Session = Depends(get_db)
) -> ChatSessionOut:
    """Return the auto-session for this work+project scope (creating one if needed).

    There is at most one auto-session per scope.  If one already exists it is
    returned with all its messages.  If not, a new empty session is created.
    """
    existing = _find_auto_session(db, body.work_id, body.project_id)
    if existing is not None:
        return _session_out(existing)

    session = ChatSession(
        work_id=body.work_id,
        project_id=body.project_id,
        is_auto=True,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return _session_out(session)


@router.get("/sessions", response_model=list[ChatSessionOut])
def list_sessions(
    work_id: int = Query(...),
    project_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
) -> list[ChatSessionOut]:
    """List all sessions (auto and saved) for a work+project scope, newest first."""
    stmt = select(ChatSession).where(ChatSession.work_id == work_id)
    if project_id is None:
        stmt = stmt.where(ChatSession.project_id.is_(None))
    else:
        stmt = stmt.where(ChatSession.project_id == project_id)
    stmt = stmt.order_by(ChatSession.updated_at.desc())
    sessions = db.scalars(stmt).all()
    return [_session_out(s, include_messages=False) for s in sessions]


@router.get("/sessions/{session_id}", response_model=ChatSessionOut)
def get_session(session_id: int, db: Session = Depends(get_db)) -> ChatSessionOut:
    """Load a session with all its messages, ordered by creation time."""
    session = db.get(ChatSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return _session_out(session)


@router.post("/sessions/{session_id}/save", response_model=ChatSessionOut)
def save_session(
    session_id: int, body: SaveSessionRequest, db: Session = Depends(get_db)
) -> ChatSessionOut:
    """Create a named snapshot of a session's messages.

    A NEW session is created (``is_auto=False``) with all messages copied from
    the source session.  The source session is not modified.
    """
    source = db.get(ChatSession, session_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title must not be empty")

    saved = ChatSession(
        work_id=source.work_id,
        project_id=source.project_id,
        title=title,
        is_auto=False,
    )
    db.add(saved)
    db.flush()  # get saved.id before inserting messages

    for m in source.messages:
        db.add(
            ChatMessage(
                session_id=saved.id,
                role=m.role,
                content=m.content,
            )
        )

    db.commit()
    db.refresh(saved)
    return _session_out(saved)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: int, db: Session = Depends(get_db)) -> None:
    """Delete a session and all its messages."""
    session = db.get(ChatSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    db.delete(session)
    db.commit()


@router.delete("/sessions/{session_id}/messages", status_code=200)
def clear_messages(
    session_id: int, db: Session = Depends(get_db)
) -> dict:
    """Clear all messages from a session, keeping the session itself."""
    session = db.get(ChatSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    for m in list(session.messages):
        db.delete(m)

    session.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"cleared": True}
