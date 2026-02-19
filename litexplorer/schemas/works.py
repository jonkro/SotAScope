from datetime import datetime

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

class WorkLocationCreate(BaseModel):
    location_type: str  # 'venue' | 'preprint'
    url: str
    is_primary: bool = True


class WorkLocationOut(BaseModel):
    id: int
    location_type: str
    url: str
    is_primary: bool

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Authors (nested)
# ---------------------------------------------------------------------------

class AuthorBrief(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}


class WorkAuthorOut(BaseModel):
    author: AuthorBrief
    position: int

    model_config = {"from_attributes": True}


class WorkAuthorAdd(BaseModel):
    """Link an existing author to a work."""
    author_id: int
    position: int = 0


class AuthorCreate(BaseModel):
    name: str
    openalex_id: str | None = None


class AuthorOut(BaseModel):
    id: int
    name: str
    openalex_id: str | None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Citations (nested read-only views)
# ---------------------------------------------------------------------------

class CitationWorkBrief(BaseModel):
    """Minimal work info shown in citation lists."""
    id: int
    doi: str | None
    title: str
    publication_year: int | None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Works
# ---------------------------------------------------------------------------

class WorkCreate(BaseModel):
    title: str
    doi: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    abstract: str | None = None
    publication_year: int | None = None
    venue_id: int | None = None
    bibtex_key: str | None = None
    bibtex_entry: str | None = None
    pdf_path: str | None = None
    citation_count: int | None = 0
    created_by: str | None = None
    locations: list[WorkLocationCreate] = []
    authors: list[WorkAuthorAdd] = []


class WorkUpdate(BaseModel):
    title: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    abstract: str | None = None
    publication_year: int | None = None
    venue_id: int | None = None
    bibtex_key: str | None = None
    bibtex_entry: str | None = None
    pdf_path: str | None = None
    citation_count: int | None = None
    created_by: str | None = None
    doi_auto_resolved: bool | None = None


class WorkOut(BaseModel):
    id: int
    doi: str | None
    arxiv_id: str | None
    openalex_id: str | None
    title: str
    abstract: str | None
    publication_year: int | None
    venue_id: int | None
    bibtex_key: str | None
    bibtex_entry: str | None
    pdf_path: str | None
    citation_count: int | None
    doi_auto_resolved: bool | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WorkDetail(WorkOut):
    venue_name: str | None = None
    locations: list[WorkLocationOut] = []
    authors: list[WorkAuthorOut] = []


# ---------------------------------------------------------------------------
# BibTeX import
# ---------------------------------------------------------------------------

class BibtexImportRequest(BaseModel):
    bibtex: str  # Raw BibTeX text


class BibtexImportResult(BaseModel):
    imported: int
    skipped: int
    works: list[WorkOut]
    needs_doi_resolution: list[int] = []


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

class DuplicateGroup(BaseModel):
    reason: str
    works: list[WorkOut]
