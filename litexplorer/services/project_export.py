"""Project export service — produces a self-contained .zip archive.

The archive contains:
  manifest.json  — structured JSON with all project data (works, topic lists,
                   extraction schemas + results, venue overrides, chat sessions,
                   project-scoped work notes, citation edges between seeds).
  seeds.bib      — BibTeX entries for all seed works.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from litexplorer.models.chat import ChatSession
from litexplorer.models.extraction import ExtractionSchema
from litexplorer.models.library import (
    Author,
    Citation,
    Venue,
    VenueAlias,
    Work,
    WorkAuthor,
    WorkLocation,
    WorkNote,
)
from litexplorer.models.project import (
    Project,
    ProjectVenueTier,
    TopicList,
    TopicListWork,
)
from litexplorer.services.bibtex_export import _bibtex_key, works_to_bibtex
from litexplorer.services.extraction import _truncate_note_type


# ---------------------------------------------------------------------------
# Stable work reference helpers
# ---------------------------------------------------------------------------


def _work_ref(work: Work) -> str:
    """Return a portable stable reference string for *work*.

    Preference order: DOI → arXiv ID → OpenAlex ID → internal DB id (last
    resort, not portable across instances).
    """
    if work.doi:
        return f"doi:{work.doi}"
    if work.arxiv_id:
        return f"arxiv:{work.arxiv_id}"
    if work.openalex_id:
        return f"openalex:{work.openalex_id}"
    return f"id:{work.id}"


def _venue_preferred_name(venue: Venue) -> str:
    """Return the preferred display name for *venue*."""
    if venue.aliases:
        return venue.aliases[0].alias
    return venue.name


# ---------------------------------------------------------------------------
# Main export function
# ---------------------------------------------------------------------------


def export_project(project_id: int, db: Session) -> io.BytesIO:
    """Build a .zip export archive for the given project.

    Args:
        project_id: DB primary key of the project to export.
        db: SQLAlchemy session.  All relationships are loaded eagerly inside
            this function — the caller does not need to pre-load anything.

    Returns:
        A :class:`io.BytesIO` buffer positioned at offset 0, containing the
        ZIP archive.

    Raises:
        ValueError: If the project does not exist.
    """
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"Project {project_id} not found")

    # ------------------------------------------------------------------
    # 1. Collect seed works (unique across all topic lists)
    # ------------------------------------------------------------------
    seed_ids_stmt = (
        select(TopicListWork.work_id)
        .join(TopicList, TopicList.id == TopicListWork.topic_list_id)
        .where(TopicList.project_id == project_id)
        .distinct()
    )
    seed_ids: list[int] = list(db.scalars(seed_ids_stmt).all())

    if seed_ids:
        works_stmt = (
            select(Work)
            .where(Work.id.in_(seed_ids))
            .options(
                selectinload(Work.authors).selectinload(WorkAuthor.author),
                selectinload(Work.venue).selectinload(Venue.aliases),
                selectinload(Work.locations),
            )
            .order_by(Work.publication_year, Work.title)
        )
        seed_works: list[Work] = list(db.scalars(works_stmt).all())
    else:
        seed_works = []

    seed_work_map: dict[int, Work] = {w.id: w for w in seed_works}

    # ------------------------------------------------------------------
    # 2. Works manifest entries
    # ------------------------------------------------------------------
    works_manifest: list[dict[str, Any]] = [
        {
            "doi": w.doi,
            "arxiv_id": w.arxiv_id,
            "openalex_id": w.openalex_id,
            "title": w.title,
            "year": w.publication_year,
            "bibtex_key": _bibtex_key(w),
        }
        for w in seed_works
    ]

    # ------------------------------------------------------------------
    # 3. Topic lists
    # ------------------------------------------------------------------
    topic_lists_stmt = (
        select(TopicList)
        .where(TopicList.project_id == project_id)
        .options(selectinload(TopicList.work_associations))
    )
    topic_lists: list[TopicList] = list(db.scalars(topic_lists_stmt).all())

    topic_lists_manifest: list[dict[str, Any]] = []
    for tl in topic_lists:
        work_refs = []
        for assoc in tl.work_associations:
            w = seed_work_map.get(assoc.work_id)
            if w:
                work_refs.append(_work_ref(w))
        topic_lists_manifest.append(
            {"name": tl.name, "color": tl.color, "works": work_refs}
        )

    # ------------------------------------------------------------------
    # 4. Extraction schemas + results
    # ------------------------------------------------------------------
    schemas_stmt = (
        select(ExtractionSchema)
        .where(ExtractionSchema.project_id == project_id)
        .options(selectinload(ExtractionSchema.columns))
    )
    schemas: list[ExtractionSchema] = list(db.scalars(schemas_stmt).all())

    schemas_manifest: list[dict[str, Any]] = []
    for schema in schemas:
        columns = sorted(schema.columns, key=lambda c: c.sort_order)

        # Build note-type maps for answer + reasoning
        answer_type_to_col: dict[str, Any] = {}
        reasoning_type_to_col: dict[str, Any] = {}
        for col in columns:
            at = _truncate_note_type(f"{schema.title} / {col.name}")
            rt = _truncate_note_type(f"{schema.title} / {col.name} / reasoning")
            answer_type_to_col[at] = col
            reasoning_type_to_col[rt] = col

        all_note_types = list(answer_type_to_col.keys()) + list(reasoning_type_to_col.keys())

        # Fetch all non-proposal notes for this schema + seed works
        if seed_ids and all_note_types:
            notes_stmt = select(WorkNote).where(
                WorkNote.work_id.in_(seed_ids),
                WorkNote.note_type.in_(all_note_types),
                WorkNote.project_id == project_id,
                WorkNote.provenance != "ai_proposal",
            )
            notes: list[WorkNote] = list(db.scalars(notes_stmt).all())
        else:
            notes = []

        # Group notes: (work_id, col_id) → {answer: note, reasoning: note}
        cell_map: dict[tuple[int, int], dict[str, WorkNote]] = {}
        for note in notes:
            if note.note_type in answer_type_to_col:
                col = answer_type_to_col[note.note_type]
                cell_map.setdefault((note.work_id, col.id), {})["answer"] = note
            elif note.note_type in reasoning_type_to_col:
                col = reasoning_type_to_col[note.note_type]
                cell_map.setdefault((note.work_id, col.id), {})["reasoning"] = note

        results: list[dict[str, Any]] = []
        for (work_id, col_id), data in cell_map.items():
            if "answer" not in data:
                continue
            w = seed_work_map.get(work_id)
            if w is None:
                continue
            col = next((c for c in columns if c.id == col_id), None)
            if col is None:
                continue
            answer_note = data["answer"]
            reasoning_note = data.get("reasoning")
            results.append(
                {
                    "work_ref": _work_ref(w),
                    "column_name": col.name,
                    "answer": answer_note.content,
                    "reasoning": reasoning_note.content if reasoning_note else None,
                    "provenance": answer_note.provenance,
                }
            )

        schemas_manifest.append(
            {
                "title": schema.title,
                "description": schema.description,
                "columns": [
                    {
                        "name": c.name,
                        "prompt": c.prompt,
                        "description": c.description,
                        "allowed_values": c.allowed_values,
                        "sort_order": c.sort_order,
                    }
                    for c in columns
                ],
                "results": results,
            }
        )

    # ------------------------------------------------------------------
    # 5. Venue tier overrides
    # ------------------------------------------------------------------
    overrides_stmt = (
        select(ProjectVenueTier)
        .where(ProjectVenueTier.project_id == project_id)
        .options(
            selectinload(ProjectVenueTier.venue).selectinload(Venue.aliases)
        )
    )
    overrides: list[ProjectVenueTier] = list(db.scalars(overrides_stmt).all())

    venue_tiers_manifest: list[dict[str, Any]] = [
        {
            "venue_openalex_id": o.venue.openalex_id,
            "venue_issn": o.venue.issn,
            "venue_name": _venue_preferred_name(o.venue),
            "tier": o.tier,
        }
        for o in overrides
    ]

    # ------------------------------------------------------------------
    # 6. Citation edges between seed works
    # ------------------------------------------------------------------
    citation_manifest: list[dict[str, Any]] = []
    if len(seed_ids) >= 2:
        cit_stmt = select(Citation).where(
            Citation.citing_work_id.in_(seed_ids),
            Citation.cited_work_id.in_(seed_ids),
        )
        for cit in db.scalars(cit_stmt).all():
            citing_w = seed_work_map.get(cit.citing_work_id)
            cited_w = seed_work_map.get(cit.cited_work_id)
            if citing_w and cited_w:
                citation_manifest.append(
                    {
                        "citing": _work_ref(citing_w),
                        "cited": _work_ref(cited_w),
                        "source": cit.source,
                    }
                )

    # ------------------------------------------------------------------
    # 7. Chat sessions (project-scoped)
    # ------------------------------------------------------------------
    sessions_stmt = (
        select(ChatSession)
        .where(ChatSession.project_id == project_id)
        .options(selectinload(ChatSession.messages))
    )
    sessions: list[ChatSession] = list(db.scalars(sessions_stmt).all())

    chat_sessions_manifest: list[dict[str, Any]] = []
    for session in sessions:
        chat_sessions_manifest.append(
            {
                "context_type": session.context_type,
                "context_id": session.context_id,
                "title": session.title,
                "is_auto": session.is_auto,
                "messages": [
                    {"role": m.role, "content": m.content}
                    for m in session.messages
                ],
            }
        )

    # ------------------------------------------------------------------
    # 8. Project-scoped work notes
    # ------------------------------------------------------------------
    work_notes_stmt = select(WorkNote).where(
        WorkNote.project_id == project_id,
        WorkNote.work_id.in_(seed_ids) if seed_ids else WorkNote.work_id.in_([]),
    )
    # Exclude extraction result notes (those have schema-prefixed note_types)
    # and ai_proposal notes — we export user / ai / ai_reviewed notes only.
    work_notes_stmt = work_notes_stmt.where(
        WorkNote.provenance != "ai_proposal",
    )
    project_notes: list[WorkNote] = list(db.scalars(work_notes_stmt).all()) if seed_ids else []

    # Build a set of note_types used by extraction schemas so we can exclude them
    extraction_note_types: set[str] = set()
    for schema in schemas:
        for col in schema.columns:
            extraction_note_types.add(_truncate_note_type(f"{schema.title} / {col.name}"))
            extraction_note_types.add(
                _truncate_note_type(f"{schema.title} / {col.name} / reasoning")
            )
            extraction_note_types.add(_truncate_note_type(f"{schema.title} / _parse_error"))

    work_notes_manifest: list[dict[str, Any]] = []
    for note in project_notes:
        if note.note_type in extraction_note_types:
            continue  # extraction results are already included in schemas_manifest
        w = seed_work_map.get(note.work_id)
        if w is None:
            continue
        work_notes_manifest.append(
            {
                "work_ref": _work_ref(w),
                "content": note.content,
                "note_type": note.note_type,
                "provenance": note.provenance,
            }
        )

    # ------------------------------------------------------------------
    # 9. Assemble manifest
    # ------------------------------------------------------------------
    manifest: dict[str, Any] = {
        "format_version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "project": {
            "name": project.name,
            "description": project.description,
        },
        "works": works_manifest,
        "topic_lists": topic_lists_manifest,
        "extraction_schemas": schemas_manifest,
        "venue_tier_overrides": venue_tiers_manifest,
        "citations": citation_manifest,
        "chat_sessions": chat_sessions_manifest,
        "work_notes": work_notes_manifest,
        "files": [],
    }

    # ------------------------------------------------------------------
    # 10. Build BibTeX
    # ------------------------------------------------------------------
    bibtex_content = works_to_bibtex(seed_works)

    # ------------------------------------------------------------------
    # 11. Package into ZIP
    # ------------------------------------------------------------------
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        zf.writestr("seeds.bib", bibtex_content)
    buf.seek(0)
    return buf
