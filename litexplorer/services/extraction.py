"""Structured extraction service — prompt assembly, response parsing, and note creation."""

from __future__ import annotations

import json
import logging
import re
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
    provider: str = "",
    model_id: str = "",
) -> tuple[str, str]:
    """Build the system prompt and user message for a structured extraction request.

    Args:
        schema: The :class:`ExtractionSchema` being applied.
        columns: Ordered list of :class:`ExtractionColumn` objects.
        paper_text: Full extracted text of the paper, or ``None`` if unavailable.
        paper_title: Title of the paper.
        paper_year: Publication year, or ``None``.
        system_prefix: Optional user-customisable prefix prepended to the system prompt.
        provider: LLM provider string (e.g. ``"openai"`` or ``"anthropic"``). Used to
            add extra format reinforcement for local/non-GPT models.
        model_id: Model ID string. Combined with *provider* to detect local models.

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

    # Concrete example using the actual column names from the schema
    if columns:
        example_cols: dict = {}
        for col in columns[:2]:  # use first two columns for brevity
            example_answer = col.allowed_values[0] if col.allowed_values else "your answer here"
            example_cols[col.name] = {
                "answer": example_answer,
                "reasoning": "Brief evidence from the paper.",
            }
        example_json = json.dumps({"columns": example_cols}, indent=2)
        system_parts.append(f"Example output format:\n{example_json}")

    has_allowed_values = any(c.allowed_values for c in columns)
    if has_allowed_values:
        system_parts.append(
            "When allowed values are specified for a column, you MUST choose exactly one "
            "of the listed values as the answer. Do not paraphrase, elaborate, or use a "
            "value not in the list."
        )

    # Negative instructions — critical for weak/local models that default to markdown
    system_parts.append(
        "Do NOT respond with a markdown table. "
        "Do NOT wrap your response in code fences. "
        "Do NOT include any text before or after the JSON object."
    )

    # Extra-aggressive format instruction for local / non-GPT OpenAI-compatible models
    _is_local_model = provider == "openai" and not model_id.startswith("gpt-")
    if _is_local_model:
        system_parts.append(
            "[FORMAT CRITICAL] Your entire response must be parseable as JSON. "
            "Start with { and end with }. Any other format will cause an error."
        )

    system_text = "\n".join(system_parts)

    # --- User message ---
    user_parts: list[str] = []

    # Format reminder at the BEGINNING of the user message
    user_parts.append(
        "Respond with a single JSON object only. "
        "No preamble, no markdown, no explanations outside the JSON."
    )

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
            col_lines.append(
                f"   Allowed values: {values_str}. "
                f"You MUST respond with exactly one of these values: {values_str}. "
                "Do not paraphrase or elaborate the answer."
            )
        user_parts.append("\n".join(col_lines))

    # Format reminder at the END of the user message (weak models pay more attention here)
    user_parts.append(
        "IMPORTANT: Your response must be a single JSON object and nothing else. "
        "No markdown, no tables, no explanations outside the JSON."
    )

    user_message = "\n\n".join(user_parts)

    return system_text, user_message


# ---------------------------------------------------------------------------
# Response parsing — helpers
# ---------------------------------------------------------------------------


def _normalize_value(v: str) -> str:
    """Normalize a string for case-insensitive allowed_values comparison."""
    return _unicode_normalize("NFKD", v).lower().strip()


# Regex that matches common thinking-model reasoning wrappers
_THINKING_TAGS_RE = re.compile(
    r"<(?:think|thinking|reasoning|scratchpad)>.*?</(?:think|thinking|reasoning|scratchpad)>",
    re.DOTALL | re.IGNORECASE,
)


def _strip_thinking_tags(text: str) -> str:
    """Remove <think>...</think> and similar reasoning wrappers from LLM output."""
    return _THINKING_TAGS_RE.sub("", text).strip()


def _strip_code_fence(text: str) -> str:
    """Extract inner content from a ```...``` fenced block."""
    lines = text.splitlines()
    inner: list[str] = []
    in_block = False
    for line in lines:
        if line.startswith("```"):
            in_block = not in_block
            continue
        inner.append(line)
    return "\n".join(inner).strip()


def _extract_json_block(text: str) -> dict | None:
    """Find and parse the first complete JSON object in mixed text.

    Handles cases where the model prepends prose before the JSON object.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    pass
    return None


def _fuzzy_match_col(header: str, col_name: str) -> float:
    """Return a similarity score 0–1 between a table header and a column name.

    Scores:
        1.0 — exact match (case-insensitive)
        0.9 — match after stripping parenthetical suffix from header
        0.8 — containment (one string is a substring of the other)
        0–1 — word-level Jaccard similarity
    """
    h = header.lower().strip()
    c = col_name.lower().strip()

    if h == c:
        return 1.0

    # Strip parenthetical suffixes from the header (e.g. "Training data (field)")
    h_stripped = re.sub(r"\s*\(.*?\)\s*$", "", h).strip()
    c_stripped = re.sub(r"\s*\(.*?\)\s*$", "", c).strip()
    if h_stripped == c or h == c_stripped or h_stripped == c_stripped:
        return 0.9

    # Containment check
    if c in h or h in c or c_stripped in h or h_stripped in c:
        return 0.8

    # Word-level Jaccard
    h_words = set(w for w in re.split(r"\W+", h) if w)
    c_words = set(w for w in re.split(r"\W+", c) if w)
    if not h_words or not c_words:
        return 0.0
    intersection = h_words & c_words
    union = h_words | c_words
    return len(intersection) / len(union)


def _apply_allowed_values(
    result: dict[int, dict[str, str]],
    columns: list[ExtractionColumn],
) -> dict[int, dict[str, str]]:
    """Normalize answers in *result* against each column's allowed_values list."""
    col_map = {col.id: col for col in columns}
    for col_id, cell in result.items():
        col = col_map.get(col_id)
        if col is None or not col.allowed_values:
            continue
        answer = cell.get("answer", "")
        if not answer:
            continue
        norm = _normalize_value(answer)
        for av in col.allowed_values:
            if _normalize_value(av) == norm:
                cell["answer"] = av
                break
    return result


def _extract_columns_from_json(
    parsed: dict,
    columns: list[ExtractionColumn],
) -> dict[int, dict[str, str]]:
    """Extract column answers from a parsed JSON dict with a ``"columns"`` key."""
    col_data: dict = parsed.get("columns", {})
    result: dict[int, dict[str, str]] = {}
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
            for av in col.allowed_values:
                if _normalize_value(av) == norm_answer:
                    answer = av
                    break

        result[col.id] = {"answer": answer, "reasoning": reasoning}
    return result


# Regex to detect markdown table separator lines (e.g. "|---|---|")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|[-|: ]+\|\s*$")
# Regex to detect row-number-only headers (e.g. "#", "No.", "1")
_ROW_NUM_HEADER_RE = re.compile(r"^[#\d\s.]+$")


def _try_parse_markdown_table(
    text: str,
    columns: list[ExtractionColumn],
) -> dict[int, dict[str, str]] | None:
    """Attempt to parse a markdown table from the LLM response.

    Maps table headers to column names via fuzzy matching.  Only the first
    data row is used (extraction is always per-paper, so one row is expected).

    Returns ``None`` if no parseable table is found.
    """
    # Collect lines that look like table rows
    table_lines = [
        line
        for line in text.splitlines()
        if line.strip().startswith("|") and line.strip().endswith("|")
    ]
    if len(table_lines) < 2:
        return None

    # Remove separator lines
    data_lines = [l for l in table_lines if not _TABLE_SEPARATOR_RE.match(l)]
    if len(data_lines) < 2:
        return None

    # First line is the header row
    header_row = data_lines[0]
    headers = [cell.strip() for cell in header_row.strip("|").split("|")]

    # Find the first data row that has the same number of cells as the header
    cells: list[str] = []
    for row_line in data_lines[1:]:
        candidate = [cell.strip() for cell in row_line.strip("|").split("|")]
        if len(candidate) == len(headers):
            cells = candidate
            break

    if not cells:
        return None

    # Map each header to the best-matching column
    result: dict[int, dict[str, str]] = {}
    for i, header in enumerate(headers):
        # Skip row-number columns
        if _ROW_NUM_HEADER_RE.match(header):
            continue
        if i >= len(cells):
            continue

        best_col: ExtractionColumn | None = None
        best_score = 0.0
        for col in columns:
            score = _fuzzy_match_col(header, col.name)
            if score > best_score:
                best_score = score
                best_col = col

        if best_col is not None and best_score >= 0.5:
            cell_value = cells[i]
            if cell_value:  # don't overwrite with empty string
                result[best_col.id] = {"answer": cell_value, "reasoning": ""}

    if not result:
        return None

    return _apply_allowed_values(result, columns)


def _try_parse_key_value(
    text: str,
    columns: list[ExtractionColumn],
) -> dict[int, dict[str, str]] | None:
    """Attempt to parse ``Key: value`` patterns from the LLM response.

    Handles variants like ``**Key**: value``, ``- Key: value``, ``• Key: value``.

    Returns ``None`` if no column-matching patterns are found.
    """
    # Key pattern: optional bullet/markdown prefix, then the key (2–60 chars), colon, value
    kv_pattern = re.compile(
        r"^\s*[-•*>]?\s*\**([^:\n]{2,60}?)\**\s*:\s*(.+)",
        re.MULTILINE,
    )
    matches = kv_pattern.findall(text)
    if not matches:
        return None

    # Generic keys that look like key-value but aren't column names
    _SKIP_KEYS = frozenset({"http", "https", "doi", "url", "note", "ref", "paper"})

    result: dict[int, dict[str, str]] = {}
    for key, value in matches:
        key = key.strip()
        value = value.strip()
        if key.lower() in _SKIP_KEYS:
            continue

        best_col: ExtractionColumn | None = None
        best_score = 0.0
        for col in columns:
            score = _fuzzy_match_col(key, col.name)
            if score > best_score:
                best_score = score
                best_col = col

        # Only accept the first match per column (avoid overwriting with lower-quality hits)
        if best_col is not None and best_score >= 0.5 and best_col.id not in result:
            result[best_col.id] = {"answer": value, "reasoning": ""}

    if not result:
        return None

    return _apply_allowed_values(result, columns)


# ---------------------------------------------------------------------------
# Response parsing — main entry point
# ---------------------------------------------------------------------------


def parse_extraction_response(
    response_text: str,
    columns: list[ExtractionColumn],
) -> tuple[dict[int, dict[str, str]], str]:
    """Parse the LLM response into a mapping of column_id → answer/reasoning.

    Attempts five strategies in order:

    1. **json**: Direct ``json.loads()`` on the (possibly fenced) response.
    2. **json_extracted**: Regex-extracts the first ``{...}`` block from mixed text.
    3. **markdown_table**: Parses a markdown table and fuzzy-maps headers to columns.
    4. **key_value**: Parses ``Key: value`` patterns.
    5. **failed**: All strategies exhausted — returns an empty dict.

    Also strips ``<think>...</think>`` and similar reasoning tags before parsing.

    When ``allowed_values`` are specified for a column, the answer is matched
    case-insensitively against the list. If a match is found, the canonical
    spelling is returned; otherwise the raw answer from the LLM is kept.

    Args:
        response_text: The raw text returned by the LLM.
        columns: Ordered list of columns (used to match by name and validate values).

    Returns:
        ``({column_id: {"answer": str, "reasoning": str}}, parsing_method)``
        where *parsing_method* is one of ``"json"``, ``"json_extracted"``,
        ``"markdown_table"``, ``"key_value"``, or ``"failed"``.
    """
    # Pre-process: strip thinking model reasoning wrappers
    text = _strip_thinking_tags(response_text)
    stripped = text.strip()

    # ------------------------------------------------------------------
    # Strategy 1: Direct JSON parse (handles plain JSON and code fences)
    # ------------------------------------------------------------------
    is_fenced = stripped.startswith("```")
    if is_fenced:
        inner = _strip_code_fence(stripped)
        try:
            parsed = json.loads(inner)
            return _extract_columns_from_json(parsed, columns), "json_extracted"
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass
    else:
        try:
            parsed = json.loads(stripped)
            return _extract_columns_from_json(parsed, columns), "json"
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass

    # ------------------------------------------------------------------
    # Strategy 2: Extract JSON block from mixed content (prose + JSON)
    # ------------------------------------------------------------------
    json_block = _extract_json_block(stripped)
    if json_block is not None:
        try:
            return _extract_columns_from_json(json_block, columns), "json_extracted"
        except (AttributeError, TypeError):
            pass

    # ------------------------------------------------------------------
    # Strategy 3: Parse markdown table
    # ------------------------------------------------------------------
    table_result = _try_parse_markdown_table(stripped, columns)
    if table_result is not None:
        return table_result, "markdown_table"

    # ------------------------------------------------------------------
    # Strategy 4: Parse key-value patterns
    # ------------------------------------------------------------------
    kv_result = _try_parse_key_value(stripped, columns)
    if kv_result is not None:
        return kv_result, "key_value"

    # ------------------------------------------------------------------
    # Strategy 5: All strategies failed
    # ------------------------------------------------------------------
    logger.warning(
        "Failed to parse extraction response with all strategies. "
        "First 300 chars: %r",
        response_text[:300],
    )
    return {}, "failed"


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


def _delete_stale_proposals_for_col(
    db: Session,
    work_id: int,
    schema: ExtractionSchema,
    col: ExtractionColumn,
) -> None:
    """Delete any ai_proposal WorkNote pair (answer + reasoning) for a single column."""
    answer_note_type = _truncate_note_type(f"{schema.title} / {col.name}")
    reasoning_note_type = _truncate_note_type(f"{schema.title} / {col.name} / reasoning")
    for note_type in (answer_note_type, reasoning_note_type):
        stale = db.scalars(
            select(WorkNote)
            .where(
                WorkNote.work_id == work_id,
                WorkNote.note_type == note_type,
                WorkNote.project_id == schema.project_id,
                WorkNote.provenance == "ai_proposal",
            )
            .limit(1)
        ).one_or_none()
        if stale:
            db.delete(stale)


def run_extraction_for_work(
    db: Session,
    work_id: int,
    schema_id: int,
    llm_client: LLMClient,
    model_id: str,
    pdf_root: Path,
    system_prefix: str = "",
    provider: str = "",
    re_evaluate_edited: bool = False,
) -> tuple[list[dict], str]:
    """Run structured extraction for one work against a schema.

    Creates or replaces ``WorkNote`` records for each column (answer + reasoning).
    Columns that already have an ``ai_reviewed`` or ``user`` note are **skipped**
    to preserve human edits, unless *re_evaluate_edited* is ``True``.

    When *re_evaluate_edited* is ``True``, columns with ``user`` or ``ai_reviewed``
    notes are also sent to the LLM.  The result is stored as a parallel
    ``WorkNote`` with ``provenance="ai_proposal"`` — the original note is not
    overwritten.  Any stale ``ai_proposal`` notes are deleted before creating
    new ones.

    When *re_evaluate_edited* is ``False`` (default), stale ``ai_proposal`` notes
    are cleaned up for columns that are being re-extracted (i.e. those that
    previously had ``provenance="ai"``), but columns with user/ai_reviewed notes
    are left untouched.

    When the LLM response cannot be parsed by any strategy, a single
    ``WorkNote`` with ``note_type="{schema.title} / _parse_error"`` is created
    containing the raw response, and no per-column notes are written.

    Args:
        db: SQLAlchemy session.
        work_id: ID of the :class:`~litexplorer.models.library.Work` to process.
        schema_id: ID of the :class:`ExtractionSchema` to apply.
        llm_client: Configured :class:`~litexplorer.external.llm_client.LLMClient`.
        model_id: Model ID string stored on each created note.
        pdf_root: Root directory for PDF / text file storage.
        system_prefix: Optional user-supplied prefix for the system prompt.
        provider: LLM provider string (``"openai"`` or ``"anthropic"``), used
            to add extra format reinforcement for local/non-GPT models.
        re_evaluate_edited: When ``True``, also runs the LLM for user/ai_reviewed
            columns and stores results as ``ai_proposal`` notes.

    Returns:
        A 2-tuple ``(items, parsing_method)`` where *items* is a list of dicts
        (one per column extracted as ``"ai"``; proposal-only columns are omitted):
        ``{"column_id": int, "column_name": str, "answer": str, "reasoning": str, "note": WorkNote}``
        and *parsing_method* is one of ``"json"``, ``"json_extracted"``,
        ``"markdown_table"``, ``"key_value"``, or ``"failed"``.

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
        return [], "json"

    # cols_to_extract: will receive provenance="ai" (normal extraction)
    # cols_for_proposal: will receive provenance="ai_proposal" (re_evaluate_edited mode)
    cols_to_extract: list[ExtractionColumn] = []
    cols_for_proposal: list[ExtractionColumn] = []

    for col in all_columns:
        answer_note_type = _truncate_note_type(f"{schema.title} / {col.name}")
        reasoning_note_type = _truncate_note_type(f"{schema.title} / {col.name} / reasoning")

        # Query for the primary (non-proposal) existing answer note.
        existing = db.scalars(
            select(WorkNote)
            .where(
                WorkNote.work_id == work_id,
                WorkNote.note_type == answer_note_type,
                WorkNote.project_id == schema.project_id,
                WorkNote.provenance != "ai_proposal",
            )
            .limit(1)
        ).one_or_none()

        if existing and existing.provenance in ("ai_reviewed", "user"):
            if re_evaluate_edited:
                logger.debug(
                    "Column %r for work %d has provenance=%r — will create ai_proposal",
                    col.name, work_id, existing.provenance,
                )
                # Delete stale ai_proposal before creating a fresh one.
                _delete_stale_proposals_for_col(db, work_id, schema, col)
                cols_for_proposal.append(col)
            else:
                logger.debug(
                    "Skipping column %r for work %d — note has provenance=%r",
                    col.name, work_id, existing.provenance,
                )
            continue

        # Column has an "ai" or "external_ai" note or no note — extract normally.
        if existing:  # provenance == "ai" or "external_ai"
            db.delete(existing)
            old_reasoning = db.scalars(
                select(WorkNote)
                .where(
                    WorkNote.work_id == work_id,
                    WorkNote.note_type == reasoning_note_type,
                    WorkNote.project_id == schema.project_id,
                    WorkNote.provenance != "ai_proposal",
                )
                .limit(1)
            ).one_or_none()
            if old_reasoning:
                db.delete(old_reasoning)

        # Clean up stale ai_proposal for this column too (regardless of re_evaluate_edited).
        _delete_stale_proposals_for_col(db, work_id, schema, col)

        cols_to_extract.append(col)

    if not cols_to_extract and not cols_for_proposal:
        return [], "json"

    # Flush deletes before inserts so UNIQUE constraints aren't violated.
    db.flush()

    paper_text = _get_primary_text(db, work_id, pdf_root)

    all_cols_for_llm = cols_to_extract + cols_for_proposal
    cols_to_extract_ids = {col.id for col in cols_to_extract}

    system_text, user_message = assemble_extraction_prompt(
        schema=schema,
        columns=all_cols_for_llm,
        paper_text=paper_text,
        paper_title=work.title,
        paper_year=work.publication_year,
        system_prefix=system_prefix,
        provider=provider,
        model_id=model_id,
    )

    # Combine system instructions with user message — the LLMClient uses its own
    # fixed system prompt, so we prepend the extraction-specific instructions to
    # the user message to ensure the LLM follows them.
    full_message = f"{system_text}\n\n{user_message}"

    reply = llm_client.chat(
        messages=[{"role": "user", "content": full_message}],
        context_documents=[],
    )

    parsed, parsing_method = parse_extraction_response(reply, all_cols_for_llm)

    # Handle complete parse failure: store raw response in a single error note
    if parsing_method == "failed":
        parse_error_type = _truncate_note_type(f"{schema.title} / _parse_error")
        error_note = WorkNote(
            work_id=work_id,
            project_id=schema.project_id,
            content=reply,
            note_type=parse_error_type,
            provenance="ai",
            model_id=model_id,
        )
        db.add(error_note)
        db.commit()
        return [], "failed"

    results: list[dict] = []
    for col in all_cols_for_llm:
        cell = parsed.get(col.id, {"answer": "", "reasoning": ""})
        answer = cell["answer"]
        reasoning = cell["reasoning"]

        target_provenance = "ai" if col.id in cols_to_extract_ids else "ai_proposal"

        # Create the answer note (empty content is valid — user sees it as "needs review")
        answer_note_type = _truncate_note_type(f"{schema.title} / {col.name}")
        answer_note = WorkNote(
            work_id=work_id,
            project_id=schema.project_id,
            content=answer,
            note_type=answer_note_type,
            provenance=target_provenance,
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
            provenance=target_provenance,
            model_id=model_id,
        )
        db.add(reasoning_note)

        # Only include "ai" notes in the returned items list.
        if target_provenance == "ai":
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

    return results, parsing_method
