"""Pydantic schemas for project import."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from litexplorer.schemas.project_merge import MergeDecisions, MergePreview


class AmbiguousMatchWork(BaseModel):
    """Brief representation of a work for display in ambiguous-match UI."""

    title: str
    year: int | None
    doi: str | None
    arxiv_id: str | None
    bibtex_key: str | None


class AmbiguousMatch(BaseModel):
    """A work from the archive that matches existing library entries by title+year
    but cannot be confidently auto-merged (e.g. first-author mismatch)."""

    incoming: AmbiguousMatchWork
    candidates: list[AmbiguousMatchWork]


class ImportResult(BaseModel):
    """Result returned by POST /api/projects/import."""

    # The newly created project (set when no name collision occurred)
    project_id: int | None = None

    # A temporary "$name - incoming" staging project (set when name collision detected)
    temp_project_id: int | None = None

    # Project name as found in the manifest
    project_name: str

    # How many works were newly created vs. reused from the existing library
    works_created: int
    works_matched: int

    # Works that matched by title+year but could not be auto-merged
    ambiguous_matches: list[AmbiguousMatch]

    # True when a project with the same name already exists
    needs_project_decision: bool

    # ID of the existing project that caused the collision (if any)
    existing_project_id: int | None = None

    # Pre-computed merge preview (available immediately if needs_project_decision)
    merge_preview: MergePreview | None = None


class ImportResolveRequest(BaseModel):
    """Body for POST /api/projects/import/{temp_id}/resolve."""

    action: Literal["merge", "rename"]

    # Required when action == "merge": the target project to merge into
    target_project_id: int | None = None

    # Required when action == "rename": the new name for the imported project
    new_name: str | None = None

    # Conflict-resolution decisions for the merge case
    merge_decisions: MergeDecisions = MergeDecisions()
