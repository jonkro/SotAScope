"""CRUD routes for projects, topic lists, and topic-list work membership."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from litexplorer.api.deps import get_db
from litexplorer.models.library import Work
from litexplorer.models.project import Project, ProjectIgnoredWork, TopicList, TopicListWork
from litexplorer.schemas.projects import (
    ProjectCreate,
    ProjectDetail,
    ProjectIgnoredWorkAdd,
    ProjectIgnoredWorkOut,
    ProjectOut,
    ProjectUpdate,
    TopicListCreate,
    TopicListDetail,
    TopicListOut,
    TopicListUpdate,
    TopicListWorkAdd,
    TopicListWorkOut,
)
from litexplorer.schemas.works import WorkOut

router = APIRouter(prefix="/api/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _get_topic_list(db: Session, project_id: int, topic_list_id: int) -> TopicList:
    tl = db.scalars(
        select(TopicList).where(
            TopicList.id == topic_list_id,
            TopicList.project_id == project_id,
        )
    ).one_or_none()
    if not tl:
        raise HTTPException(status_code=404, detail="Topic list not found")
    return tl


def _topic_list_detail(tl: TopicList) -> TopicListDetail:
    return TopicListDetail(
        **{c.key: getattr(tl, c.key) for c in TopicList.__table__.columns},
        works=[
            TopicListWorkOut(
                id=assoc.id,
                work_id=assoc.work_id,
                added_at=assoc.added_at,
                work=WorkOut.model_validate(assoc.work),
            )
            for assoc in tl.work_associations
        ],
    )


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------

@router.get("", response_model=list[ProjectOut])
def list_projects(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    q: str | None = Query(None, description="Search project name"),
    db: Session = Depends(get_db),
):
    stmt = select(Project).order_by(Project.id)
    if q:
        stmt = stmt.where(Project.name.ilike(f"%{q}%"))
    return db.scalars(stmt.offset(offset).limit(limit)).all()


def _project_detail(project: Project) -> ProjectDetail:
    """Build a ProjectDetail response from a Project ORM instance."""
    return ProjectDetail(
        **{c.key: getattr(project, c.key) for c in Project.__table__.columns},
        topic_lists=[TopicListOut.model_validate(tl) for tl in project.topic_lists],
        ignored_works=[
            ProjectIgnoredWorkOut(
                id=assoc.id,
                work_id=assoc.work_id,
                work=WorkOut.model_validate(assoc.work),
            )
            for assoc in project.ignored_work_associations
        ],
    )


@router.post("", response_model=ProjectDetail, status_code=201)
def create_project(body: ProjectCreate, db: Session = Depends(get_db)):
    project = Project(**body.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return _project_detail(project)


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(project_id: int, db: Session = Depends(get_db)):
    project = db.scalars(
        select(Project)
        .where(Project.id == project_id)
        .options(
            joinedload(Project.topic_lists),
            joinedload(Project.ignored_work_associations).joinedload(ProjectIgnoredWork.work),
        )
    ).unique().one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return _project_detail(project)


@router.patch("/{project_id}", response_model=ProjectDetail)
def update_project(project_id: int, body: ProjectUpdate, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(project, key, value)
    db.commit()
    db.refresh(project)
    return _project_detail(project)


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = _get_project(db, project_id)
    db.delete(project)
    db.commit()


# ---------------------------------------------------------------------------
# Topic lists (nested under project)
# ---------------------------------------------------------------------------

@router.get("/{project_id}/topic-lists", response_model=list[TopicListOut])
def list_topic_lists(project_id: int, db: Session = Depends(get_db)):
    _get_project(db, project_id)
    return db.scalars(
        select(TopicList)
        .where(TopicList.project_id == project_id)
        .order_by(TopicList.id)
    ).all()


@router.post("/{project_id}/topic-lists", response_model=TopicListDetail, status_code=201)
def create_topic_list(
    project_id: int, body: TopicListCreate, db: Session = Depends(get_db)
):
    _get_project(db, project_id)
    tl = TopicList(project_id=project_id, **body.model_dump())
    db.add(tl)
    db.commit()
    db.refresh(tl)
    return _topic_list_detail(tl)


@router.get("/{project_id}/topic-lists/{topic_list_id}", response_model=TopicListDetail)
def get_topic_list(project_id: int, topic_list_id: int, db: Session = Depends(get_db)):
    tl = db.scalars(
        select(TopicList)
        .where(TopicList.id == topic_list_id, TopicList.project_id == project_id)
        .options(
            joinedload(TopicList.work_associations).joinedload(TopicListWork.work)
        )
    ).unique().one_or_none()
    if not tl:
        raise HTTPException(status_code=404, detail="Topic list not found")
    return _topic_list_detail(tl)


@router.patch("/{project_id}/topic-lists/{topic_list_id}", response_model=TopicListDetail)
def update_topic_list(
    project_id: int,
    topic_list_id: int,
    body: TopicListUpdate,
    db: Session = Depends(get_db),
):
    tl = _get_topic_list(db, project_id, topic_list_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(tl, key, value)
    db.commit()
    db.refresh(tl)
    return _topic_list_detail(tl)


@router.delete("/{project_id}/topic-lists/{topic_list_id}", status_code=204)
def delete_topic_list(
    project_id: int, topic_list_id: int, db: Session = Depends(get_db)
):
    tl = _get_topic_list(db, project_id, topic_list_id)
    db.delete(tl)
    db.commit()


# ---------------------------------------------------------------------------
# Topic list works (add / remove papers from a topic list)
# ---------------------------------------------------------------------------

@router.post(
    "/{project_id}/topic-lists/{topic_list_id}/works",
    response_model=TopicListWorkOut,
    status_code=201,
)
def add_work_to_topic_list(
    project_id: int,
    topic_list_id: int,
    body: TopicListWorkAdd,
    db: Session = Depends(get_db),
):
    tl = _get_topic_list(db, project_id, topic_list_id)
    work = db.get(Work, body.work_id)
    if not work:
        raise HTTPException(status_code=422, detail="Work not found")

    existing = db.scalars(
        select(TopicListWork).where(
            TopicListWork.topic_list_id == tl.id,
            TopicListWork.work_id == work.id,
        )
    ).one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Work already in this topic list")

    assoc = TopicListWork(topic_list_id=tl.id, work_id=work.id)
    db.add(assoc)
    db.commit()
    db.refresh(assoc)
    return TopicListWorkOut(
        id=assoc.id,
        work_id=assoc.work_id,
        added_at=assoc.added_at,
        work=WorkOut.model_validate(work),
    )


@router.delete(
    "/{project_id}/topic-lists/{topic_list_id}/works/{work_id}",
    status_code=204,
)
def remove_work_from_topic_list(
    project_id: int, topic_list_id: int, work_id: int, db: Session = Depends(get_db)
):
    tl = _get_topic_list(db, project_id, topic_list_id)
    assoc = db.scalars(
        select(TopicListWork).where(
            TopicListWork.topic_list_id == tl.id,
            TopicListWork.work_id == work_id,
        )
    ).one_or_none()
    if not assoc:
        raise HTTPException(status_code=404, detail="Work not in this topic list")
    db.delete(assoc)
    db.commit()


# ---------------------------------------------------------------------------
# Ignored works (mark papers as uninteresting per project)
# ---------------------------------------------------------------------------

@router.post(
    "/{project_id}/ignored-works",
    response_model=ProjectIgnoredWorkOut,
    status_code=201,
)
def add_ignored_work(
    project_id: int,
    body: ProjectIgnoredWorkAdd,
    db: Session = Depends(get_db),
):
    _get_project(db, project_id)
    work = db.get(Work, body.work_id)
    if not work:
        raise HTTPException(status_code=422, detail="Work not found")

    existing = db.scalars(
        select(ProjectIgnoredWork).where(
            ProjectIgnoredWork.project_id == project_id,
            ProjectIgnoredWork.work_id == work.id,
        )
    ).one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Work already ignored in this project")

    assoc = ProjectIgnoredWork(project_id=project_id, work_id=work.id)
    db.add(assoc)
    db.commit()
    db.refresh(assoc)
    return ProjectIgnoredWorkOut(
        id=assoc.id,
        work_id=assoc.work_id,
        work=WorkOut.model_validate(work),
    )


@router.delete(
    "/{project_id}/ignored-works/{work_id}",
    status_code=204,
)
def remove_ignored_work(
    project_id: int, work_id: int, db: Session = Depends(get_db)
):
    _get_project(db, project_id)
    assoc = db.scalars(
        select(ProjectIgnoredWork).where(
            ProjectIgnoredWork.project_id == project_id,
            ProjectIgnoredWork.work_id == work_id,
        )
    ).one_or_none()
    if not assoc:
        raise HTTPException(status_code=404, detail="Work not in ignored list")
    db.delete(assoc)
    db.commit()
