"""Project import service — reads a .zip archive and recreates the project.

Import steps:
  a. Parse ZIP, validate manifest.json format_version.
  b. Import works: match by DOI → arXiv → OpenAlex → title+year+first-author.
     100% matches (exact title+year+author) → auto-match silently.
     title+year match without author agreement → flag as ambiguous, create new.
  c. Handle project name collision: create a temp "$name - incoming" project.
  d. Populate project content: topic lists, extraction schemas + results,
     venue tier overrides, chat sessions, project-scoped work notes.
  e. Return ImportResult + list of new seed work IDs for auto-enrichment.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import bibtexparser
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from litexplorer.models.chat import ChatMessage, ChatSession
from litexplorer.models.extraction import ExtractionColumn, ExtractionSchema
from litexplorer.models.library import Venue, VenueAlias, Work, WorkNote, WorkPDF
from litexplorer.models.project import (
    Project,
    ProjectVenueTier,
    TopicList,
    TopicListWork,
)
from litexplorer.schemas.project_import import (
    AmbiguousMatch,
    AmbiguousMatchWork,
    ImportResult,
    PendingVenueAlias,
)
from litexplorer.services.enrichment import (
    _extract_first_author_from_bibtex,
    normalize_venue_name,
)
from litexplorer.services.extraction import _truncate_note_type

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# BibTeX helpers
# ---------------------------------------------------------------------------


def _parse_bibtex_entries(bibtex_content: str) -> dict[str, dict]:
    """Parse a BibTeX string; return a dict keyed by entry ID (bibtex_key)."""
    bib_db = bibtexparser.loads(bibtex_content)
    result: dict[str, dict] = {}
    for entry in bib_db.entries:
        key = entry.get("ID", "")
        if key:
            result[key] = entry
    return result


def _entry_to_bibtex_str(entry: dict) -> str:
    """Reconstruct a BibTeX entry string from a parsed bibtexparser dict."""
    entry_type = entry.get("ENTRYTYPE", "article")
    key = entry.get("ID", "unknown")
    fields = {k: v for k, v in entry.items() if k not in ("ENTRYTYPE", "ID")}
    lines = [f"@{entry_type}{{{key},"]
    for k, v in fields.items():
        lines.append(f"  {k} = {{{v}}},")
    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Venue matching
# ---------------------------------------------------------------------------


def _find_or_create_venue(
    openalex_id: str | None,
    issn: str | None,
    venue_name: str | None,
    db: Session,
) -> Venue | None:
    """Find an existing venue by openalex_id, ISSN, or normalised name.

    Creates a new Venue row (tier=2 default) if no match is found and a name
    is available.  Returns None if venue_name is also absent.
    """
    # 1. OpenAlex ID
    if openalex_id:
        v = db.scalars(select(Venue).where(Venue.openalex_id == openalex_id)).one_or_none()
        if v:
            return v

    # 2. ISSN
    if issn:
        v = db.scalars(select(Venue).where(Venue.issn == issn)).one_or_none()
        if v:
            return v

    # 3. Normalised name match (check canonical name + all aliases)
    if venue_name:
        norm_import = normalize_venue_name(venue_name).lower()
        all_venues = db.scalars(select(Venue)).all()
        for v in all_venues:
            if normalize_venue_name(v.name).lower() == norm_import:
                return v
            for alias in v.aliases:
                if normalize_venue_name(alias.alias).lower() == norm_import:
                    return v

    # 4. Create new venue
    if venue_name:
        new_venue = Venue(
            name=venue_name,
            openalex_id=openalex_id,
            issn=issn,
            tier=2,
        )
        db.add(new_venue)
        db.flush()
        return new_venue

    return None


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------


@dataclass
class _ImportState:
    """Mutable state accumulated while processing manifest works."""

    # Maps manifest work_ref → local work ID
    work_ref_to_id: dict[str, int] = field(default_factory=dict)
    works_created: int = 0
    works_matched: int = 0
    ambiguous_matches: list[AmbiguousMatch] = field(default_factory=list)
    # IDs of works newly added to the library (candidates for auto-enrichment)
    new_work_ids: list[int] = field(default_factory=list)
    # Aliases from a v2 archive that don't exist in this instance's VenueAlias table
    pending_venue_aliases: list[PendingVenueAlias] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Work import step
# ---------------------------------------------------------------------------


def _import_works(
    manifest_works: list[dict],
    bibtex_entries: dict[str, dict],
    state: _ImportState,
    db: Session,
) -> None:
    """Resolve each manifest work to a local Work row (match or create)."""
    for mw in manifest_works:
        doi: str | None = mw.get("doi")
        arxiv_id: str | None = mw.get("arxiv_id")
        openalex_id: str | None = mw.get("openalex_id")
        title: str = mw.get("title") or ""
        year: int | None = mw.get("year")
        bibtex_key: str | None = mw.get("bibtex_key")

        # Derive a stable work_ref (same preference order as export)
        if doi:
            work_ref = f"doi:{doi}"
        elif arxiv_id:
            work_ref = f"arxiv:{arxiv_id}"
        elif openalex_id:
            work_ref = f"openalex:{openalex_id}"
        else:
            work_ref = f"title:{title}:{year}"

        # ---- Try to match existing work ----
        existing: Work | None = None

        if doi:
            existing = db.scalars(
                select(Work).where(func.lower(Work.doi) == doi.lower())
            ).one_or_none()

        if existing is None and arxiv_id:
            existing = db.scalars(
                select(Work).where(Work.arxiv_id == arxiv_id)
            ).one_or_none()

        if existing is None and openalex_id:
            existing = db.scalars(
                select(Work).where(Work.openalex_id == openalex_id)
            ).one_or_none()

        if existing is not None:
            state.work_ref_to_id[work_ref] = existing.id
            state.works_matched += 1
            continue

        # ---- Title + year dedup check ----
        title_year_matched = False
        if title and year is not None:
            ty_matches = list(
                db.scalars(
                    select(Work).where(
                        func.lower(Work.title) == title.lower(),
                        Work.publication_year == year,
                    )
                ).all()
            )
            if ty_matches:
                # Try to extract first author from BibTeX for auto-match check
                incoming_author: str | None = None
                if bibtex_key and bibtex_key in bibtex_entries:
                    raw = _entry_to_bibtex_str(bibtex_entries[bibtex_key])
                    incoming_author = _extract_first_author_from_bibtex(raw)

                best: Work | None = None
                if incoming_author:
                    for candidate in ty_matches:
                        if candidate.bibtex_entry:
                            cand_author = _extract_first_author_from_bibtex(
                                candidate.bibtex_entry
                            )
                            if (
                                cand_author
                                and incoming_author.lower() == cand_author.lower()
                            ):
                                best = candidate
                                break

                if best is not None:
                    # 100% match: auto-match silently, reuse existing
                    state.work_ref_to_id[work_ref] = best.id
                    state.works_matched += 1
                    title_year_matched = True
                else:
                    # Ambiguous: flag for user, but still create a new work below
                    state.ambiguous_matches.append(
                        AmbiguousMatch(
                            incoming=AmbiguousMatchWork(
                                title=title,
                                year=year,
                                doi=doi,
                                arxiv_id=arxiv_id,
                                bibtex_key=bibtex_key,
                            ),
                            candidates=[
                                AmbiguousMatchWork(
                                    title=c.title,
                                    year=c.publication_year,
                                    doi=c.doi,
                                    arxiv_id=c.arxiv_id,
                                    bibtex_key=c.bibtex_key,
                                )
                                for c in ty_matches[:5]
                            ],
                        )
                    )

        if title_year_matched:
            continue

        # ---- Create new work ----
        bib_entry = bibtex_entries.get(bibtex_key or "")

        if bib_entry is not None:
            entry_doi = bib_entry.get("doi") or doi
            entry_title = bib_entry.get("title") or title or "Untitled"
            raw_year = bib_entry.get("year")
            entry_year = int(raw_year) if raw_year and str(raw_year).isdigit() else year
            bibtex_str = _entry_to_bibtex_str(bib_entry)

            # Avoid DOI uniqueness violation: skip if another work owns this DOI
            if entry_doi:
                doi_conflict = db.scalars(
                    select(Work).where(func.lower(Work.doi) == entry_doi.lower())
                ).one_or_none()
                if doi_conflict:
                    state.work_ref_to_id[work_ref] = doi_conflict.id
                    state.works_matched += 1
                    continue

            # Avoid bibtex_key uniqueness violation: clear key if already used
            effective_bk: str | None = bibtex_key
            if effective_bk:
                bk_conflict = db.scalars(
                    select(Work).where(Work.bibtex_key == effective_bk)
                ).one_or_none()
                if bk_conflict:
                    effective_bk = None

            new_work = Work(
                doi=entry_doi,
                arxiv_id=arxiv_id,
                openalex_id=openalex_id,
                title=entry_title,
                publication_year=entry_year,
                abstract=bib_entry.get("abstract"),
                bibtex_key=effective_bk,
                bibtex_entry=bibtex_str,
            )
        else:
            # Fallback: minimal work from manifest metadata only
            if doi:
                doi_conflict = db.scalars(
                    select(Work).where(func.lower(Work.doi) == doi.lower())
                ).one_or_none()
                if doi_conflict:
                    state.work_ref_to_id[work_ref] = doi_conflict.id
                    state.works_matched += 1
                    continue

            effective_bk = bibtex_key
            if effective_bk:
                bk_conflict = db.scalars(
                    select(Work).where(Work.bibtex_key == effective_bk)
                ).one_or_none()
                if bk_conflict:
                    effective_bk = None

            new_work = Work(
                doi=doi,
                arxiv_id=arxiv_id,
                openalex_id=openalex_id,
                title=title or "Untitled",
                publication_year=year,
                bibtex_key=effective_bk,
            )

        db.add(new_work)
        db.flush()
        state.work_ref_to_id[work_ref] = new_work.id
        state.works_created += 1
        state.new_work_ids.append(new_work.id)


# ---------------------------------------------------------------------------
# v2: venue tier snapshot import
# ---------------------------------------------------------------------------


def _import_venue_tiers_v2(
    project_id: int,
    venue_tiers_data: list[dict],
    state: _ImportState,
    db: Session,
) -> None:
    """Process the ``venue_tiers`` section from a format_version=2 archive.

    For each entry:
    - Locate or create the Venue (via the existing _find_or_create_venue helper).
    - Create a ProjectVenueTier row with the exported effective tier, making
      the tier project-local so the importer's global tiers are not touched.
    - Collect aliases that don't yet exist in this instance's VenueAlias table
      as ``PendingVenueAlias`` entries in *state*, so the caller can present
      them to the user for confirmation.
    """
    for entry in venue_tiers_data:
        venue = _find_or_create_venue(
            openalex_id=entry.get("venue_openalex_id"),
            issn=entry.get("venue_issn"),
            venue_name=entry.get("venue_name"),
            db=db,
        )
        if venue is None:
            continue

        # Create project-local tier override if one doesn't exist yet
        existing_override = db.scalars(
            select(ProjectVenueTier).where(
                ProjectVenueTier.project_id == project_id,
                ProjectVenueTier.venue_id == venue.id,
            )
        ).one_or_none()
        if existing_override is None:
            db.add(
                ProjectVenueTier(
                    project_id=project_id,
                    venue_id=venue.id,
                    tier=entry.get("tier", 2),
                )
            )

        # Collect aliases that are new to this instance
        preferred_name = entry.get("venue_name", "")
        for alias_str in entry.get("aliases", []):
            # Skip if it already is the venue's canonical name on this instance
            if alias_str == venue.name:
                continue
            existing_alias = db.scalars(
                select(VenueAlias).where(
                    VenueAlias.venue_id == venue.id,
                    VenueAlias.alias == alias_str,
                )
            ).one_or_none()
            if existing_alias is None:
                state.pending_venue_aliases.append(
                    PendingVenueAlias(
                        venue_id=venue.id,
                        venue_name=preferred_name,
                        alias=alias_str,
                    )
                )

    db.flush()


# ---------------------------------------------------------------------------
# Project content creation
# ---------------------------------------------------------------------------


def _create_project_content(
    project_id: int,
    manifest: dict[str, Any],
    state: _ImportState,
    db: Session,
    format_version: int = 1,
) -> None:
    """Populate topic lists, schemas, venue tiers, chat sessions, and notes."""

    # ---- e. Topic lists ----
    for tl_data in manifest.get("topic_lists", []):
        tl = TopicList(
            project_id=project_id,
            name=tl_data["name"],
            color=tl_data.get("color", "#3b82f6"),
        )
        db.add(tl)
        db.flush()

        for work_ref in tl_data.get("works", []):
            work_id = state.work_ref_to_id.get(work_ref)
            if work_id is None:
                logger.warning(
                    "import: unresolved work ref %r in topic list %r",
                    work_ref,
                    tl_data["name"],
                )
                continue
            existing_assoc = db.scalars(
                select(TopicListWork).where(
                    TopicListWork.topic_list_id == tl.id,
                    TopicListWork.work_id == work_id,
                )
            ).one_or_none()
            if existing_assoc is None:
                db.add(TopicListWork(topic_list_id=tl.id, work_id=work_id))
        db.flush()

    # ---- f. Extraction schemas + columns  ----
    # ---- g. Extraction results as WorkNotes ----
    for schema_data in manifest.get("extraction_schemas", []):
        schema = ExtractionSchema(
            project_id=project_id,
            title=schema_data["title"],
            description=schema_data.get("description"),
            is_promoted=schema_data.get("is_promoted", False),
        )
        db.add(schema)
        db.flush()

        col_name_to_obj: dict[str, ExtractionColumn] = {}
        columns_data = sorted(
            schema_data.get("columns", []),
            key=lambda c: c.get("sort_order", 9999),
        )
        for i, col_data in enumerate(columns_data):
            col = ExtractionColumn(
                schema_id=schema.id,
                name=col_data["name"],
                prompt=col_data.get("prompt", ""),
                description=col_data.get("description"),
                allowed_values=col_data.get("allowed_values"),
                sort_order=i,  # re-index from 0
            )
            db.add(col)
            db.flush()
            col_name_to_obj[col_data["name"]] = col

        for result_data in schema_data.get("results", []):
            work_ref = result_data.get("work_ref", "")
            work_id = state.work_ref_to_id.get(work_ref)
            if work_id is None:
                continue
            col_name = result_data.get("column_name", "")
            answer = result_data.get("answer")
            reasoning = result_data.get("reasoning")
            provenance = result_data.get("provenance", "ai")

            if answer is not None:
                db.add(
                    WorkNote(
                        work_id=work_id,
                        project_id=project_id,
                        content=answer,
                        note_type=_truncate_note_type(
                            f"{schema_data['title']} / {col_name}"
                        ),
                        provenance=provenance,
                    )
                )
            if reasoning is not None:
                db.add(
                    WorkNote(
                        work_id=work_id,
                        project_id=project_id,
                        content=reasoning,
                        note_type=_truncate_note_type(
                            f"{schema_data['title']} / {col_name} / reasoning"
                        ),
                        provenance=provenance,
                    )
                )
        db.flush()

    # ---- h. Venue tiers ----
    if format_version >= 2:
        # v2: full effective-tier snapshot with alias collection
        _import_venue_tiers_v2(
            project_id,
            manifest.get("venue_tiers", []),
            state,
            db,
        )
    else:
        # v1: project-local overrides only (legacy format)
        for override_data in manifest.get("venue_tier_overrides", []):
            venue = _find_or_create_venue(
                openalex_id=override_data.get("venue_openalex_id"),
                issn=override_data.get("venue_issn"),
                venue_name=override_data.get("venue_name"),
                db=db,
            )
            if venue is None:
                continue
            existing_override = db.scalars(
                select(ProjectVenueTier).where(
                    ProjectVenueTier.project_id == project_id,
                    ProjectVenueTier.venue_id == venue.id,
                )
            ).one_or_none()
            if existing_override is None:
                db.add(
                    ProjectVenueTier(
                        project_id=project_id,
                        venue_id=venue.id,
                        tier=override_data.get("tier", 2),
                    )
                )
        db.flush()

    # ---- i. Chat sessions + messages ----
    # context_id is an original DB ID that is not portable across instances.
    # For extraction_schema sessions we reset to "papers" mode as a safe fallback;
    # for all other context types we preserve context_type but clear context_id.
    for session_data in manifest.get("chat_sessions", []):
        ctx_type: str = session_data.get("context_type", "papers")
        ctx_id: int | None = session_data.get("context_id")

        if ctx_type == "extraction_schema":
            ctx_type = "papers"
            ctx_id = None

        session = ChatSession(
            project_id=project_id,
            work_id=None,  # work_id is not portable across instances
            context_type=ctx_type,
            context_id=ctx_id,
            title=session_data.get("title"),
            is_auto=session_data.get("is_auto", False),
        )
        db.add(session)
        db.flush()

        for msg_data in session_data.get("messages", []):
            db.add(
                ChatMessage(
                    session_id=session.id,
                    role=msg_data.get("role", "user"),
                    content=msg_data.get("content", ""),
                )
            )
    db.flush()

    # ---- j. Project-scoped work notes ----
    for note_data in manifest.get("work_notes", []):
        work_ref = note_data.get("work_ref", "")
        work_id = state.work_ref_to_id.get(work_ref)
        if work_id is None:
            continue
        db.add(
            WorkNote(
                work_id=work_id,
                project_id=project_id,
                content=note_data.get("content", ""),
                note_type=note_data.get("note_type"),
                provenance=note_data.get("provenance", "user"),
            )
        )
    db.flush()


# ---------------------------------------------------------------------------
# PDF file import
# ---------------------------------------------------------------------------


def _import_pdf_files(
    zip_entries: dict[str, bytes],
    manifest_files: list[dict],
    state: _ImportState,
    db: Session,
) -> None:
    """Copy PDF and extracted text files from the archive to local storage.

    *zip_entries* is a ``{zip_path: bytes}`` mapping pre-read from the archive.

    For each entry in the manifest ``files`` list:
    - Resolve the work by DOI or arXiv ID using *state.work_ref_to_id*.
    - Copy each PDF to ``{pdf_root}/{local_work_id}/{filename}``.
    - Create a ``WorkPDF`` row if none exists for that work + filename.
    - If a companion ``.txt`` file is present, copy it and set
      ``extraction_status = "ready"`` on the corresponding WorkPDF row.

    Skips gracefully (with a warning) if the work cannot be resolved or if
    a file is absent from *zip_entries*.
    """
    if not manifest_files:
        return

    from litexplorer.api.settings import get_setting_value
    from litexplorer.config import settings as _settings

    custom_root = get_setting_value(db, "pdf_storage_path")
    pdf_root = Path(custom_root) if custom_root else _settings.pdf_dir

    for entry in manifest_files:
        doi: str | None = entry.get("doi")
        arxiv_id: str | None = entry.get("arxiv_id")
        archive_work_id: int | None = entry.get("work_id")
        filenames: list[str] = entry.get("filenames", [])

        if not filenames:
            continue

        # Resolve local work_id via stable reference
        if doi:
            work_ref = f"doi:{doi}"
        elif arxiv_id:
            work_ref = f"arxiv:{arxiv_id}"
        else:
            logger.warning("import files: entry has no doi or arxiv_id, skipping")
            continue

        local_work_id = state.work_ref_to_id.get(work_ref)
        if local_work_id is None:
            logger.warning(
                "import files: no work found for ref %r, skipping %d file(s)",
                work_ref,
                len(filenames),
            )
            continue

        if archive_work_id is None:
            logger.warning(
                "import files: entry for %r has no work_id, cannot locate files in archive",
                work_ref,
            )
            continue

        work_dir = pdf_root / str(local_work_id)
        work_dir.mkdir(parents=True, exist_ok=True)

        for filename in filenames:
            zip_path = f"files/{archive_work_id}/{filename}"
            file_data = zip_entries.get(zip_path)
            if file_data is None:
                logger.warning("import files: %r not found in archive, skipping", zip_path)
                continue

            dest_path = work_dir / filename
            dest_path.write_bytes(file_data)

            suffix = Path(filename).suffix.lower()

            if suffix == ".pdf":
                # Create WorkPDF row if it doesn't already exist
                existing_pdf = db.scalars(
                    select(WorkPDF).where(
                        WorkPDF.work_id == local_work_id,
                        WorkPDF.filename == filename,
                    )
                ).one_or_none()
                if existing_pdf is None:
                    has_any_pdf = db.scalars(
                        select(WorkPDF).where(WorkPDF.work_id == local_work_id)
                    ).first()
                    db.add(
                        WorkPDF(
                            work_id=local_work_id,
                            filename=filename,
                            is_primary=(has_any_pdf is None),
                            extraction_status="pending",
                        )
                    )
                    db.flush()

            elif suffix == ".txt":
                # Companion extracted text — mark the matching PDF as ready
                stem = Path(filename).stem
                pdf_row = db.scalars(
                    select(WorkPDF).where(
                        WorkPDF.work_id == local_work_id,
                        WorkPDF.filename == f"{stem}.pdf",
                    )
                ).one_or_none()
                if pdf_row is not None:
                    pdf_row.extraction_status = "ready"
                    db.flush()

    db.flush()


# ---------------------------------------------------------------------------
# Helper: seed IDs for a project
# ---------------------------------------------------------------------------


def _project_seed_ids(project_id: int, db: Session) -> list[int]:
    return list(
        db.scalars(
            select(TopicListWork.work_id)
            .join(TopicList, TopicListWork.topic_list_id == TopicList.id)
            .where(TopicList.project_id == project_id)
            .distinct()
        ).all()
    )


# ---------------------------------------------------------------------------
# Main import function
# ---------------------------------------------------------------------------


def import_project(
    zip_bytes: bytes,
    db: Session,
) -> tuple[ImportResult, list[int]]:
    """Parse a project ZIP archive and recreate the project in the database.

    Returns:
        ``(result, seed_ids)`` — if ``result.needs_project_decision`` is True
        a temp project was created but auto-enrichment should NOT be triggered
        until the collision is resolved.  ``seed_ids`` is empty in that case.

    Raises:
        ValueError: for invalid ZIP, missing manifest, unsupported version.
    """
    # ---- a. Parse ZIP ----
    # Read all content eagerly so the ZipFile can be closed before DB work begins.
    try:
        buf = io.BytesIO(zip_bytes)
        with zipfile.ZipFile(buf, "r") as zf_obj:
            try:
                manifest_bytes = zf_obj.read("manifest.json")
            except KeyError:
                raise ValueError("Archive is missing manifest.json")
            try:
                bibtex_content = zf_obj.read("seeds.bib").decode("utf-8", errors="replace")
            except KeyError:
                bibtex_content = ""
            # Pre-read all files/* entries so _import_pdf_files doesn't need the ZipFile open.
            zip_entries: dict[str, bytes] = {
                name: zf_obj.read(name)
                for name in zf_obj.namelist()
                if name.startswith("files/")
            }
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid ZIP file: {exc}") from exc

    manifest: dict[str, Any] = json.loads(manifest_bytes)

    fmt = manifest.get("format_version")
    if fmt is None:
        raise ValueError("manifest.json is missing 'format_version'")
    if not isinstance(fmt, int) or fmt > 2:
        raise ValueError(
            f"Archive format version {fmt!r} is not supported "
            "(max supported: 2). Please upgrade LitExplorer."
        )

    project_info: dict = manifest.get("project", {})
    project_name: str = project_info.get("name") or "Imported Project"
    project_description: str | None = project_info.get("description")

    # ---- b. Import works ----
    bibtex_entries = _parse_bibtex_entries(bibtex_content)
    state = _ImportState()
    _import_works(manifest.get("works", []), bibtex_entries, state, db)

    # ---- b2. Import PDF files (library-level; independent of project collision) ----
    _import_pdf_files(zip_entries, manifest.get("files", []), state, db)

    # ---- c/d. Handle project name collision ----
    existing_project = db.scalars(
        select(Project).where(Project.name == project_name)
    ).one_or_none()

    if existing_project is not None:
        # Create a temp staging project
        temp_name = f"{project_name} - incoming"
        suffix = 1
        candidate = temp_name
        while db.scalars(select(Project).where(Project.name == candidate)).one_or_none():
            suffix += 1
            candidate = f"{temp_name} ({suffix})"
        temp_name = candidate

        temp_project = Project(name=temp_name, description=project_description)
        db.add(temp_project)
        db.flush()
        _create_project_content(temp_project.id, manifest, state, db, fmt)
        db.commit()

        # Pre-compute merge preview so the frontend can show conflict controls
        from litexplorer.services.project_merge import merge_preview as _preview

        preview = _preview(existing_project.id, temp_project.id, db)

        result = ImportResult(
            project_id=None,
            temp_project_id=temp_project.id,
            project_name=project_name,
            works_created=state.works_created,
            works_matched=state.works_matched,
            ambiguous_matches=state.ambiguous_matches,
            needs_project_decision=True,
            existing_project_id=existing_project.id,
            merge_preview=preview,
            pending_venue_aliases=state.pending_venue_aliases,
            needs_alias_decision=len(state.pending_venue_aliases) > 0,
        )
        # No auto-enrichment until the user resolves the collision
        return result, []

    # ---- No collision: create project directly ----
    project = Project(name=project_name, description=project_description)
    db.add(project)
    db.flush()
    _create_project_content(project.id, manifest, state, db, fmt)
    db.commit()

    seed_ids = _project_seed_ids(project.id, db)

    result = ImportResult(
        project_id=project.id,
        temp_project_id=None,
        project_name=project_name,
        works_created=state.works_created,
        works_matched=state.works_matched,
        ambiguous_matches=state.ambiguous_matches,
        needs_project_decision=False,
        existing_project_id=None,
        merge_preview=None,
        pending_venue_aliases=state.pending_venue_aliases,
        needs_alias_decision=len(state.pending_venue_aliases) > 0,
    )
    return result, seed_ids


# ---------------------------------------------------------------------------
# Resolve import collision
# ---------------------------------------------------------------------------


def resolve_import(
    temp_project_id: int,
    action: str,
    target_project_id: int | None,
    new_name: str | None,
    merge_decisions: Any,  # MergeDecisions from schemas.project_merge
    db: Session,
) -> tuple[Project, list[int]]:
    """Resolve a pending import where a project name collision was detected.

    Returns:
        ``(final_project, seed_ids)`` where *final_project* is the project the
        user should be directed to, and *seed_ids* are work IDs to enrich.
    """
    temp_project = db.get(Project, temp_project_id)
    if temp_project is None:
        raise ValueError(f"Temp project {temp_project_id} not found")

    if action == "rename":
        if not new_name or not new_name.strip():
            raise ValueError("new_name is required for action='rename'")
        new_name = new_name.strip()
        conflict = db.scalars(
            select(Project).where(
                Project.name == new_name,
                Project.id != temp_project_id,
            )
        ).one_or_none()
        if conflict:
            raise ValueError(f"Project name '{new_name}' is already taken")
        temp_project.name = new_name
        db.commit()
        seed_ids = _project_seed_ids(temp_project_id, db)
        db.refresh(temp_project)
        return temp_project, seed_ids

    if action == "merge":
        if target_project_id is None:
            raise ValueError("target_project_id is required for action='merge'")
        target_project = db.get(Project, target_project_id)
        if target_project is None:
            raise ValueError(f"Target project {target_project_id} not found")

        from litexplorer.services.project_merge import execute_merge

        merged = execute_merge(target_project_id, temp_project_id, merge_decisions, db)
        seed_ids = _project_seed_ids(target_project_id, db)

        # Delete the temp staging project — it has served its purpose
        db.delete(temp_project)
        db.commit()
        return merged, seed_ids

    raise ValueError(f"Unknown action: {action!r}")
