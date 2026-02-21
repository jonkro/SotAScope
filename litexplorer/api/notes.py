"""Project-level notes API router."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from litexplorer.api.deps import get_db
from litexplorer.models.library import Work, WorkNote
from litexplorer.models.project import Project, TopicList, TopicListWork
from litexplorer.schemas.notes import ProjectNoteOut, WorkNoteOut

project_notes_router = APIRouter(prefix="/api/projects/{project_id}/notes", tags=["notes"])


@project_notes_router.get("", response_model=list[ProjectNoteOut])
def list_project_notes(
    project_id: int,
    db: Session = Depends(get_db),
):
    """List all notes relevant to a project.

    Returns:
    - All notes with project_id == this project
    - All general notes (project_id IS NULL) for works in this project's topic lists
    """
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Get work IDs from all topic lists in this project
    seed_work_ids = db.scalars(
        select(TopicListWork.work_id)
        .join(TopicList, TopicListWork.topic_list_id == TopicList.id)
        .where(TopicList.project_id == project_id)
    ).all()
    seed_work_id_set = set(seed_work_ids)

    # Query: project-scoped notes for this project, OR general notes for seed works
    conditions = [WorkNote.project_id == project_id]
    if seed_work_id_set:
        conditions.append(
            (WorkNote.project_id == None) & (WorkNote.work_id.in_(seed_work_id_set))  # noqa: E711
        )

    stmt = (
        select(WorkNote)
        .where(or_(*conditions))
        .order_by(WorkNote.created_at)
    )
    notes = db.scalars(stmt).all()

    # Fetch work titles for all referenced works
    work_ids = {n.work_id for n in notes}
    works = {w.id: w for w in db.scalars(select(Work).where(Work.id.in_(work_ids))).all()} if work_ids else {}

    result = []
    for n in notes:
        w = works.get(n.work_id)
        result.append(ProjectNoteOut(
            **{k: getattr(n, k) for k in WorkNoteOut.model_fields},
            work_title=w.title if w else "(unknown)",
            work_publication_year=w.publication_year if w else None,
        ))

    return result
