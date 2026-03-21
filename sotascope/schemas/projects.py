from datetime import datetime

from pydantic import BaseModel

from sotascope.schemas.works import WorkOut


# ---------------------------------------------------------------------------
# Topic list works
# ---------------------------------------------------------------------------

class TopicListWorkAdd(BaseModel):
    work_id: int


class TopicListWorkOut(BaseModel):
    id: int
    work_id: int
    added_at: datetime
    work: WorkOut

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Project ignored works
# ---------------------------------------------------------------------------

class ProjectIgnoredWorkAdd(BaseModel):
    work_id: int


class ProjectIgnoredWorkOut(BaseModel):
    id: int
    work_id: int
    work: WorkOut

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Topic lists
# ---------------------------------------------------------------------------

class TopicListCreate(BaseModel):
    name: str
    color: str  # hex, e.g. '#3b82f6'


class TopicListUpdate(BaseModel):
    name: str | None = None
    color: str | None = None


class TopicListOut(BaseModel):
    id: int
    project_id: int
    name: str
    color: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TopicListDetail(TopicListOut):
    works: list[TopicListWorkOut] = []


class TopicListMergeResult(BaseModel):
    merged_count: int
    skipped_duplicate_count: int


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

class ProjectCreate(BaseModel):
    name: str
    description: str | None = None
    owner: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    owner: str | None = None


class ProjectOut(BaseModel):
    id: int
    name: str
    description: str | None
    owner: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectDetail(ProjectOut):
    topic_lists: list[TopicListOut] = []
    ignored_works: list[ProjectIgnoredWorkOut] = []


# ---------------------------------------------------------------------------
# Per-project venue tiers
# ---------------------------------------------------------------------------

class ProjectVenueTierOut(BaseModel):
    venue_id: int
    venue_name: str          # preferred alias or canonical name
    all_names: list[str]     # all aliases + canonical name, for client-side search
    global_tier: int
    local_tier: int | None   # None = no override (inheriting global)
    effective_tier: int

    model_config = {"from_attributes": True}


class ProjectVenueTierUpdate(BaseModel):
    tier: int
