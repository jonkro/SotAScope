"""Pydantic schemas for work notes."""

from datetime import datetime

from pydantic import BaseModel


class WorkNoteCreate(BaseModel):
    content: str
    note_type: str | None = None
    project_id: int | None = None


class WorkNoteUpdate(BaseModel):
    content: str | None = None
    note_type: str | None = None
    is_outdated: bool | None = None
    provenance: str | None = None  # explicit override (e.g. "ai_reviewed")


class WorkNoteOut(BaseModel):
    id: int
    work_id: int
    project_id: int | None
    content: str
    note_type: str | None
    provenance: str
    model_id: str | None
    is_outdated: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectNoteOut(WorkNoteOut):
    """WorkNoteOut with extra work metadata for project-level listing."""
    work_title: str
    work_publication_year: int | None = None
