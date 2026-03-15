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


class PendingVenueAlias(BaseModel):
    """An alias that exists in the export archive but not in the importer's library.

    Returned by POST /api/projects/import (format_version=2) when new aliases
    are found.  The user confirms or rejects each one before they are written
    to the global VenueAlias table.
    """

    venue_id: int   # local DB ID of the matched/created venue
    venue_name: str  # preferred display name, for grouping in the UI
    alias: str       # the alias string awaiting the user's decision


class AliasDecision(BaseModel):
    """User decision for a single pending venue alias."""

    venue_id: int
    alias: str
    accepted: bool


class ResolveAliasesRequest(BaseModel):
    """Body for POST /api/projects/import/{project_id}/resolve-aliases."""

    decisions: list[AliasDecision]


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

    # Aliases present in the archive that don't yet exist in this instance's
    # global VenueAlias table.  Only populated for format_version=2 imports.
    pending_venue_aliases: list[PendingVenueAlias] = []

    # True when pending_venue_aliases is non-empty and the user must confirm
    # which aliases to write to the global library before import is complete.
    needs_alias_decision: bool = False


class ImportResolveRequest(BaseModel):
    """Body for POST /api/projects/import/{temp_id}/resolve."""

    action: Literal["merge", "rename"]

    # Required when action == "merge": the target project to merge into
    target_project_id: int | None = None

    # Required when action == "rename": the new name for the imported project
    new_name: str | None = None

    # Conflict-resolution decisions for the merge case
    merge_decisions: MergeDecisions = MergeDecisions()
