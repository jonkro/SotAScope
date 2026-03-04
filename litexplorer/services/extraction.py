"""Structured extraction service — prompt assembly, response parsing, and note creation."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unicodedata import normalize as _unicode_normalize

from sqlalchemy import select
from sqlalchemy.orm import Session

from litexplorer.external.llm_client import LLMClient
from litexplorer.models.extraction import ExtractionColumn, ExtractionSchema
from litexplorer.models.library import Work, WorkNote, WorkPDF

logger = logging.getLogger(__name__)

# Maximum length for WorkNote.note_type (DB constraint is VARCHAR(64))
_MAX_NOTE_TYPE_LEN = 64


def _truncate_note_type(value: str) -> str:
    return value[:_MAX_NOTE_TYPE_LEN]


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def assemble_extraction_prompt(
    schema: ExtractionSchema,
    columns: list[ExtractionColumn],
    paper_text: str | None,
    paper_title: str,
    paper_year: int | None,
    system_prefix: str = "",
) -> tuple[str, str]:
    """Build the system prompt and user message for a structured extraction request.

    Args:
        schema: The :class:`ExtractionSchema` being applied.
        columns: Ordered list of :class:`ExtractionColumn` objects.
        paper_text: Full extracted text of the paper, or ``None`` if unavailable.
        paper_title: Title of the paper.
        paper_year: Publication year, or ``None``.
        system_prefix: Optional user-customisable prefix prepended to the system prompt.

    Returns:
        A 2-tuple ``(system_text, user_message)`` both as plain strings.
    """
    system_parts: list[str] = []

    if system_prefix:
        system_parts.append(system_prefix.rstrip())
        system_parts.append("")  # blank line separator

    system_parts.append(
        f"You are a research assistant analyzing a scientific paper to extract "
        f"structured information for a literature survey table titled '{schema.title}'."
    )

    if schema.description:
        system_parts.append(f"Purpose of this table: {schema.description}")

    system_parts.append(
        "Respond ONLY with a JSON object. For each column, provide an 'answer' field "
        "(the table cell value) and a 'reasoning' field (brief justification with evidence "
        "from the paper). Use this exact structure: "
        '{"columns": {"column_name": {"answer": "...", "reasoning": "..."}, ...}}'
    )

    has_allowed_values = any(c.allowed_values for c in columns)
    if has_allowed_values:
        system_parts.append(
            "When allowed values are specified, you MUST choose one of them as the answer. "
            "Use the value exactly as written."
        )

    system_text = "\n".join(system_parts)

    # --- User message ---
    user_parts: list[str] = []

    year_str = str(paper_year) if paper_year is not None else "n.d."
    user_parts.append(f"Paper: {paper_title} ({year_str})")

    if paper_text:
        user_parts.append(f"Full text:\n{paper_text}")
    else:
        user_parts.append(
            "No full text available — answer based on the title only."
        )

    user_parts.append("Extract information for the following columns:")
    for i, col in enumerate(columns, start=1):
        col_lines = [f"{i}. {col.name}: {col.prompt}"]
        if col.description:
            col_lines.append(f"   Description: {col.description}")
        if col.allowed_values:
            values_str = ", ".join(col.allowed_values)
            col_lines.append(f"   Allowed values: {values_str}")
        user_parts.append("\n".join(col_lines))

    user_message = "\n\n".join(user_parts)

    return system_text, user_message


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _normalize_value(v: str) -> str:
    """Normalize a string for case-insensitive allowed_values comparison."""
    return _unicode_normalize("NFKD", v).lower().strip()


def parse_extraction_response(
    response_text: str,
    columns: list[ExtractionColumn],
) -> dict[int, dict[str, str]]:
    """Parse the LLM JSON response into a mapping of column_id → answer/reasoning.

    On JSON parse failure, every column receives the raw response as its answer
    and an empty reasoning string.

    When ``allowed_values`` are specified for a column, the answer is matched
    case-insensitively against the list. If a match is found, the canonical
    spelling is returned; otherwise the raw answer from the LLM is kept.

    Args:
        response_text: The raw text returned by the LLM.
        columns: Ordered list of columns (used to match by name and validate values).

    Returns:
        ``{column_id: {"answer": str, "reasoning": str}}``
    """
    # Try to extract JSON even if there's surrounding prose (e.g. ```json ... ```)
    cleaned = response_text.strip()
    # Strip fenced code blocks
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        # Drop first and last lines if they look like fences
        inner = []
        in_block = False
        for line in lines:
            if line.startswith("```"):
                in_block = not in_block
                continue
            inner.append(line)
        cleaned = "\n".join(inner).strip()

    result: dict[int, dict[str, str]] = {}

    try:
        parsed = json.loads(cleaned)
        col_data: dict = parsed.get("columns", {})

        # Build a name → column mapping
        name_map: dict[str, ExtractionColumn] = {c.name: c for c in columns}

        for col in columns:
            entry = col_data.get(col.name, {})
            if not isinstance(entry, dict):
                answer = str(entry) if entry is not None else ""
                reasoning = ""
            else:
                answer = str(entry.get("answer", "")).strip()
                reasoning = str(entry.get("reasoning", "")).strip()

            # Validate against allowed_values (case-insensitive)
            if col.allowed_values and answer:
                norm_answer = _normalize_value(answer)
                matched = None
                for av in col.allowed_values:
                    if _normalize_value(av) == norm_answer:
                        matched = av
                        break
                if matched is not None:
                    answer = matched
                # else: keep raw answer (LLM deviated from allowed values)

            result[col.id] = {"answer": answer, "reasoning": reasoning}

    except (json.JSONDecodeError, AttributeError, TypeError) as exc:
        logger.warning("Failed to parse extraction response as JSON: %s", exc)
        # Fallback: every column gets the raw response as answer
        for col in columns:
            result[col.id] = {"answer": response_text.strip(), "reasoning": ""}

    return result


# ---------------------------------------------------------------------------
# End-to-end extraction
# ---------------------------------------------------------------------------


def _get_primary_text(db: Session, work_id: int, pdf_root: Path) -> str | None:
    """Return extracted text for the primary (or first ready) PDF of a work."""
    # Prefer primary PDF with status=ready
    pdf = db.scalars(
        select(WorkPDF)
        .where(
            WorkPDF.work_id == work_id,
            WorkPDF.is_primary == True,  # noqa: E712
            WorkPDF.extraction_status == "ready",
        )
        .limit(1)
    ).one_or_none()

    if pdf is None:
        # Fall back to any ready PDF
        pdf = db.scalars(
            select(WorkPDF)
            .where(
                WorkPDF.work_id == work_id,
                WorkPDF.extraction_status == "ready",
            )
            .order_by(WorkPDF.id)
            .limit(1)
        ).one_or_none()

    if pdf is None:
        return None

    txt_path = pdf_root / str(work_id) / Path(pdf.filename).with_suffix(".txt")
    if txt_path.is_file():
        return txt_path.read_text(encoding="utf-8")
    return None


def run_extraction_for_work(
    db: Session,
    work_id: int,
    schema_id: int,
    llm_client: LLMClient,
    model_id: str,
    pdf_root: Path,
    system_prefix: str = "",
) -> list[dict]:
    """Run structured extraction for one work against a schema.

    Creates or replaces ``WorkNote`` records for each column (answer + reasoning).
    Columns that already have an ``ai_reviewed`` or ``user`` note are **skipped**
    to preserve human edits.  Existing ``ai`` notes are deleted before new ones
    are created so that re-running extraction doesn't accumulate duplicates.

    Args:
        db: SQLAlchemy session.
        work_id: ID of the :class:`~litexplorer.models.library.Work` to process.
        schema_id: ID of the :class:`ExtractionSchema` to apply.
        llm_client: Configured :class:`~litexplorer.external.llm_client.LLMClient`.
        model_id: Model ID string stored on each created note.
        pdf_root: Root directory for PDF / text file storage.
        system_prefix: Optional user-supplied prefix for the system prompt.

    Returns:
        A list of dicts, one per **extracted** column (skipped columns omitted):
        ``{"column_id": int, "column_name": str, "answer": str, "reasoning": str, "note": WorkNote}``

    Raises:
        ValueError: If the schema or work is not found.
    """
    schema = db.get(ExtractionSchema, schema_id)
    if schema is None:
        raise ValueError(f"ExtractionSchema {schema_id} not found")

    work = db.get(Work, work_id)
    if work is None:
        raise ValueError(f"Work {work_id} not found")

    all_columns = sorted(schema.columns, key=lambda c: c.sort_order)
    if not all_columns:
        return []

    # Determine which columns to extract vs. skip (preserve reviewed/user notes).
    cols_to_extract: list[ExtractionColumn] = []
    for col in all_columns:
        answer_note_type = _truncate_note_type(f"{schema.title} / {col.name}")
        existing = db.scalars(
            select(WorkNote)
            .where(
                WorkNote.work_id == work_id,
                WorkNote.note_type == answer_note_type,
                WorkNote.project_id == schema.project_id,
            )
            .limit(1)
        ).one_or_none()

        if existing and existing.provenance in ("ai_reviewed", "user"):
            logger.debug(
                "Skipping column %r for work %d — note has provenance=%r",
                col.name, work_id, existing.provenance,
            )
            continue

        # Delete any stale "ai" answer + reasoning notes before re-creating.
        if existing:
            db.delete(existing)
            reasoning_type = _truncate_note_type(f"{schema.title} / {col.name} / reasoning")
            old_reasoning = db.scalars(
                select(WorkNote)
                .where(
                    WorkNote.work_id == work_id,
                    WorkNote.note_type == reasoning_type,
                    WorkNote.project_id == schema.project_id,
                )
                .limit(1)
            ).one_or_none()
            if old_reasoning:
                db.delete(old_reasoning)

        cols_to_extract.append(col)

    if not cols_to_extract:
        return []

    # Flush deletes before inserts so UNIQUE constraints aren't violated.
    db.flush()

    paper_text = _get_primary_text(db, work_id, pdf_root)

    system_text, user_message = assemble_extraction_prompt(
        schema=schema,
        columns=cols_to_extract,
        paper_text=paper_text,
        paper_title=work.title,
        paper_year=work.publication_year,
        system_prefix=system_prefix,
    )

    # Combine system instructions with user message — the LLMClient uses its own
    # fixed system prompt, so we prepend the extraction-specific instructions to
    # the user message to ensure the LLM follows them.
    full_message = f"{system_text}\n\n{user_message}"

    reply = llm_client.chat(
        messages=[{"role": "user", "content": full_message}],
        context_documents=[],
    )

    parsed = parse_extraction_response(reply, cols_to_extract)

    results: list[dict] = []
    for col in cols_to_extract:
        cell = parsed.get(col.id, {"answer": "", "reasoning": ""})
        answer = cell["answer"]
        reasoning = cell["reasoning"]

        # Create the answer note
        answer_note_type = _truncate_note_type(f"{schema.title} / {col.name}")
        answer_note = WorkNote(
            work_id=work_id,
            project_id=schema.project_id,
            content=answer,
            note_type=answer_note_type,
            provenance="ai",
            model_id=model_id,
        )
        db.add(answer_note)

        # Create the reasoning note
        reasoning_note_type = _truncate_note_type(f"{schema.title} / {col.name} / reasoning")
        reasoning_note = WorkNote(
            work_id=work_id,
            project_id=schema.project_id,
            content=reasoning if reasoning else "(no reasoning provided)",
            note_type=reasoning_note_type,
            provenance="ai",
            model_id=model_id,
        )
        db.add(reasoning_note)

        results.append({
            "column_id": col.id,
            "column_name": col.name,
            "answer": answer,
            "reasoning": reasoning,
            "note": answer_note,
        })

    db.commit()

    # Refresh answer notes so they have DB-assigned IDs and timestamps
    for item in results:
        db.refresh(item["note"])

    return results
