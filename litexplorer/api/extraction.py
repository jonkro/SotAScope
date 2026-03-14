"""Structured extraction API — schema/column CRUD and extraction execution."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from litexplorer.api.deps import get_db
from litexplorer.external.llm_client import make_llm_client
from litexplorer.models.extraction import ExtractionColumn, ExtractionSchema
from litexplorer.schemas.extraction import (
    ColumnReorderRequest,
    ExtractionBatchRequest,
    ExtractionCellResult,
    ExtractionColumnCreate,
    ExtractionColumnOut,
    ExtractionColumnResult,
    ExtractionColumnUpdate,
    ExtractionManualFillRequest,
    ExtractionResultsResponse,
    ExtractionSchemaCreate,
    ExtractionSchemaOut,
    ExtractionSchemaUpdate,
    ExtractionWorkResult,
)
from litexplorer.schemas.notes import WorkNoteOut
from litexplorer.services.extraction_jobs import extraction_jobs
from litexplorer.services.work_lock import work_lock

router = APIRouter(prefix="/api/extraction", tags=["extraction"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_pdf_root(db: Session) -> Path:
    from litexplorer.api.settings import get_setting_value
    from litexplorer.config import settings as app_settings

    custom = get_setting_value(db, "pdf_storage_path")
    if custom:
        return Path(custom)
    return app_settings.pdf_dir


def _build_llm_client(db: Session):
    """Read LLM settings from DB and return a configured client.

    Raises HTTP 400 if the LLM provider or model is not configured.
    """
    from litexplorer.api.settings import get_setting_value

    provider = get_setting_value(db, "llm_provider") or ""
    if not provider:
        raise HTTPException(status_code=400, detail="LLM provider is not configured")

    model_id = get_setting_value(db, "llm_model_id") or ""
    if not model_id:
        raise HTTPException(status_code=400, detail="LLM model is not configured")

    api_key = get_setting_value(db, "llm_api_key") or ""
    base_url = get_setting_value(db, "llm_base_url") or None

    client = make_llm_client(provider, api_key, model_id, base_url)
    return client, model_id, provider


# ---------------------------------------------------------------------------
# Schema CRUD
# ---------------------------------------------------------------------------


@router.post("/schemas", response_model=ExtractionSchemaOut, status_code=201)
def create_schema(body: ExtractionSchemaCreate, db: Session = Depends(get_db)):
    """Create a new extraction schema."""
    schema = ExtractionSchema(
        title=body.title,
        description=body.description,
        project_id=body.project_id,
    )
    db.add(schema)
    db.commit()
    db.refresh(schema)
    return ExtractionSchemaOut.model_validate(schema)


@router.post("/schemas/from-discussion", response_model=ExtractionSchemaOut, status_code=201)
def create_schema_from_discussion(body: ExtractionSchemaCreate, db: Session = Depends(get_db)):
    """Create a new extraction schema materialised from a schema design discussion.

    Semantically identical to ``POST /schemas`` but named distinctly so the
    frontend can signal that the schema originated from an LLM conversation.
    """
    schema = ExtractionSchema(
        title=body.title,
        description=body.description,
        project_id=body.project_id,
    )
    db.add(schema)
    db.commit()
    db.refresh(schema)
    return ExtractionSchemaOut.model_validate(schema)


@router.get("/schemas", response_model=list[ExtractionSchemaOut])
def list_schemas(project_id: int | None = None, db: Session = Depends(get_db)):
    """List extraction schemas, optionally filtered by project."""
    stmt = select(ExtractionSchema)
    if project_id is not None:
        stmt = stmt.where(ExtractionSchema.project_id == project_id)
    schemas = db.scalars(stmt.order_by(ExtractionSchema.created_at)).all()
    return [ExtractionSchemaOut.model_validate(s) for s in schemas]


@router.get("/schemas/{schema_id}", response_model=ExtractionSchemaOut)
def get_schema(schema_id: int, db: Session = Depends(get_db)):
    """Get an extraction schema with its columns."""
    schema = db.get(ExtractionSchema, schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Extraction schema not found")
    return ExtractionSchemaOut.model_validate(schema)


@router.get("/schemas/{schema_id}/summary", response_model=ExtractionSchemaOut)
def get_schema_summary(schema_id: int, db: Session = Depends(get_db)):
    """Return schema title, description, and columns for context display (e.g. discussion sidebar)."""
    schema = db.get(ExtractionSchema, schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Extraction schema not found")
    return ExtractionSchemaOut.model_validate(schema)


@router.put("/schemas/{schema_id}", response_model=ExtractionSchemaOut)
def update_schema(schema_id: int, body: ExtractionSchemaUpdate, db: Session = Depends(get_db)):
    """Update a schema's title and/or description."""
    schema = db.get(ExtractionSchema, schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Extraction schema not found")
    if body.title is not None:
        schema.title = body.title
    if body.description is not None:
        schema.description = body.description
    db.commit()
    db.refresh(schema)
    return ExtractionSchemaOut.model_validate(schema)


@router.delete("/schemas/{schema_id}", status_code=204)
def delete_schema(schema_id: int, db: Session = Depends(get_db)):
    """Delete a schema (cascades to columns)."""
    schema = db.get(ExtractionSchema, schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Extraction schema not found")
    db.delete(schema)
    db.commit()


# ---------------------------------------------------------------------------
# Column CRUD
# ---------------------------------------------------------------------------


class ColumnFromProposalRequest(BaseModel):
    """Request body for creating a column from an LLM-proposed definition."""

    name: str
    prompt: str
    description: str = ""
    allowed_values: list[str] | None = None


@router.post(
    "/schemas/{schema_id}/columns/from-proposal",
    response_model=ExtractionColumnOut,
    status_code=201,
)
def create_column_from_proposal(
    schema_id: int,
    body: ColumnFromProposalRequest,
    db: Session = Depends(get_db),
):
    """Accept an LLM-proposed column and append it to the schema.

    The column is automatically assigned ``sort_order = max_existing + 1`` so it
    lands at the end of the schema.  Returns 404 if the schema does not exist.
    """
    schema = db.get(ExtractionSchema, schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Extraction schema not found")

    existing_orders = [c.sort_order for c in schema.columns]
    next_order = (max(existing_orders) + 1) if existing_orders else 0

    col = ExtractionColumn(
        schema_id=schema_id,
        name=body.name,
        prompt=body.prompt,
        description=body.description if body.description else None,
        allowed_values=body.allowed_values,
        sort_order=next_order,
    )
    db.add(col)
    db.commit()
    db.refresh(col)
    return ExtractionColumnOut.model_validate(col)


@router.post("/schemas/{schema_id}/columns", response_model=ExtractionColumnOut, status_code=201)
def create_column(
    schema_id: int,
    body: ExtractionColumnCreate,
    db: Session = Depends(get_db),
):
    """Add a column to a schema."""
    schema = db.get(ExtractionSchema, schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Extraction schema not found")

    col = ExtractionColumn(
        schema_id=schema_id,
        name=body.name,
        prompt=body.prompt,
        description=body.description,
        allowed_values=body.allowed_values,
        sort_order=body.sort_order,
    )
    db.add(col)
    db.commit()
    db.refresh(col)
    return ExtractionColumnOut.model_validate(col)


@router.put("/columns/{column_id}", response_model=ExtractionColumnOut)
def update_column(
    column_id: int,
    body: ExtractionColumnUpdate,
    db: Session = Depends(get_db),
):
    """Update a column's fields."""
    col = db.get(ExtractionColumn, column_id)
    if col is None:
        raise HTTPException(status_code=404, detail="Extraction column not found")
    if body.name is not None:
        col.name = body.name
    if body.prompt is not None:
        col.prompt = body.prompt
    if body.description is not None:
        col.description = body.description
    if body.allowed_values is not None:
        col.allowed_values = body.allowed_values
    if body.sort_order is not None:
        col.sort_order = body.sort_order
    db.commit()
    db.refresh(col)
    return ExtractionColumnOut.model_validate(col)


@router.delete("/columns/{column_id}", status_code=204)
def delete_column(column_id: int, db: Session = Depends(get_db)):
    """Delete a column."""
    col = db.get(ExtractionColumn, column_id)
    if col is None:
        raise HTTPException(status_code=404, detail="Extraction column not found")
    db.delete(col)
    db.commit()


@router.put("/schemas/{schema_id}/columns/reorder", response_model=list[ExtractionColumnOut])
def reorder_columns(
    schema_id: int,
    body: ColumnReorderRequest,
    db: Session = Depends(get_db),
):
    """Batch update sort_order for a schema's columns."""
    schema = db.get(ExtractionSchema, schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Extraction schema not found")

    order_map = {item.column_id: item.sort_order for item in body.columns}
    for col in schema.columns:
        if col.id in order_map:
            col.sort_order = order_map[col.id]

    db.commit()
    db.refresh(schema)
    return [ExtractionColumnOut.model_validate(c) for c in schema.columns]


# ---------------------------------------------------------------------------
# Extraction results lookup
# ---------------------------------------------------------------------------


@router.get("/schemas/{schema_id}/results", response_model=ExtractionResultsResponse)
def get_extraction_results(
    schema_id: int,
    work_ids: str = "",
    db: Session = Depends(get_db),
):
    """Return existing extraction notes for a schema + set of works.

    ``work_ids`` is a comma-separated list of work IDs, e.g. ``?work_ids=1,2,3``.
    Returns a flat list of :class:`ExtractionCellResult` objects, one per
    (work_id, column_id) pair that already has an answer note.
    """
    from litexplorer.models.library import WorkNote
    from sqlalchemy import select

    schema = db.get(ExtractionSchema, schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Extraction schema not found")

    if not work_ids.strip():
        return ExtractionResultsResponse(cells=[])

    try:
        wids = [int(x.strip()) for x in work_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=422, detail="work_ids must be comma-separated integers")

    if not wids:
        return ExtractionResultsResponse(cells=[])

    columns = sorted(schema.columns, key=lambda c: c.sort_order)

    # Build note_type → column mappings
    answer_type_map: dict[str, ExtractionColumn] = {}
    reasoning_type_map: dict[str, ExtractionColumn] = {}
    for col in columns:
        from litexplorer.services.extraction import _truncate_note_type
        at = _truncate_note_type(f"{schema.title} / {col.name}")
        rt = _truncate_note_type(f"{schema.title} / {col.name} / reasoning")
        answer_type_map[at] = col
        reasoning_type_map[rt] = col

    all_note_types = list(answer_type_map.keys()) + list(reasoning_type_map.keys())

    stmt = (
        select(WorkNote)
        .where(
            WorkNote.work_id.in_(wids),
            WorkNote.note_type.in_(all_note_types),
        )
    )
    if schema.project_id is not None:
        stmt = stmt.where(
            (WorkNote.project_id == schema.project_id) | (WorkNote.project_id == None)  # noqa: E711
        )

    notes = db.scalars(stmt).all()

    # Group by (work_id, column_id), separating primary notes from ai_proposal notes.
    cells_map: dict[tuple[int, int], dict] = {}
    for note in notes:
        if note.note_type in answer_type_map:
            col = answer_type_map[note.note_type]
            key = (note.work_id, col.id)
            cells_map.setdefault(key, {})
            if note.provenance == "ai_proposal":
                cells_map[key]["proposal"] = note
            else:
                cells_map[key]["answer"] = note
        elif note.note_type in reasoning_type_map:
            col = reasoning_type_map[note.note_type]
            key = (note.work_id, col.id)
            cells_map.setdefault(key, {})
            if note.provenance != "ai_proposal":
                cells_map[key]["reasoning"] = note

    cells = [
        ExtractionCellResult(
            work_id=work_id,
            column_id=col_id,
            answer_note=WorkNoteOut.model_validate(data["answer"]),
            reasoning_note=WorkNoteOut.model_validate(data["reasoning"]) if "reasoning" in data else None,
            proposal=WorkNoteOut.model_validate(data["proposal"]) if "proposal" in data else None,
        )
        for (work_id, col_id), data in cells_map.items()
        if "answer" in data
    ]

    return ExtractionResultsResponse(cells=cells)


# ---------------------------------------------------------------------------
# Extraction execution
# ---------------------------------------------------------------------------


def _run_and_format(
    db: Session,
    schema_id: int,
    work_id: int,
    llm_client,
    model_id: str,
    pdf_root: Path,
    system_prefix: str,
    provider: str = "",
    re_evaluate_edited: bool = False,
) -> ExtractionWorkResult:
    from litexplorer.services.extraction import run_extraction_for_work

    items, parsing_method = run_extraction_for_work(
        db=db,
        work_id=work_id,
        schema_id=schema_id,
        llm_client=llm_client,
        model_id=model_id,
        pdf_root=pdf_root,
        system_prefix=system_prefix,
        provider=provider,
        re_evaluate_edited=re_evaluate_edited,
    )
    column_results = [
        ExtractionColumnResult(
            column_id=item["column_id"],
            column_name=item["column_name"],
            answer=item["answer"],
            reasoning=item["reasoning"],
            note=WorkNoteOut.model_validate(item["note"]),
        )
        for item in items
    ]
    return ExtractionWorkResult(
        work_id=work_id,
        columns=column_results,
        parsing_method=parsing_method,
    )


def _extraction_bg(
    job_id: str,
    schema_id: int,
    work_ids: list[int],
    llm_client,
    model_id: str,
    provider: str,
    pdf_root: Path,
    system_prefix: str,
    re_evaluate_edited: bool,
) -> None:
    """Background task: run LLM extraction for all work_ids, updating the job tracker.

    Each work's lock is released as soon as that work finishes (success or failure).
    The job is marked completed after all works are processed.

    Must NOT use the request-scoped ``db`` session — creates its own via
    :func:`litexplorer.database.SessionLocal` and closes it in a ``finally`` block.
    """
    from litexplorer.database import SessionLocal

    db = SessionLocal()
    try:
        for work_id in work_ids:
            extraction_jobs.update_work_status(job_id, work_id, "running")
            try:
                _run_and_format(
                    db,
                    schema_id,
                    work_id,
                    llm_client,
                    model_id,
                    pdf_root,
                    system_prefix,
                    provider,
                    re_evaluate_edited,
                )
                extraction_jobs.update_work_status(job_id, work_id, "done")
            except Exception as exc:
                extraction_jobs.update_work_status(job_id, work_id, "failed", str(exc))
            finally:
                work_lock.release(work_id)
        extraction_jobs.mark_completed(job_id)
    finally:
        db.close()


@router.post("/schemas/{schema_id}/extract/{work_id}", status_code=202)
def extract_for_work(
    schema_id: int,
    work_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Start LLM extraction for a single work in a background task.

    Returns **202 Accepted** immediately with a ``job_id`` that can be polled
    via ``GET /api/extraction/jobs/{job_id}``.  Returns 409 if the work is
    already being processed.
    """
    from litexplorer.api.settings import get_setting_value

    schema = db.get(ExtractionSchema, schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Extraction schema not found")

    llm_client, model_id, provider = _build_llm_client(db)
    pdf_root = _get_pdf_root(db)
    system_prefix = get_setting_value(db, "llm_system_prompt_prefix") or ""

    if not work_lock.acquire(work_id, "Running LLM extraction"):
        raise HTTPException(
            status_code=409, detail=f"Work {work_id} is already being processed"
        )

    job_id = extraction_jobs.create_job(schema_id, [work_id])
    background_tasks.add_task(
        _extraction_bg,
        job_id=job_id,
        schema_id=schema_id,
        work_ids=[work_id],
        llm_client=llm_client,
        model_id=model_id,
        provider=provider,
        pdf_root=pdf_root,
        system_prefix=system_prefix,
        re_evaluate_edited=False,
    )
    return {"job_id": job_id, "message": "Extraction started for 1 work"}


@router.post("/schemas/{schema_id}/extract", status_code=202)
def extract_batch(
    schema_id: int,
    body: ExtractionBatchRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Start batch LLM extraction for multiple works in a background task.

    Returns **202 Accepted** immediately with a ``job_id``.  Returns 409 if
    any requested work is already locked; no locks are acquired in that case.
    """
    from litexplorer.api.settings import get_setting_value

    schema = db.get(ExtractionSchema, schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Extraction schema not found")

    llm_client, model_id, provider = _build_llm_client(db)
    pdf_root = _get_pdf_root(db)
    system_prefix = get_setting_value(db, "llm_system_prompt_prefix") or ""

    # Check all locks before acquiring any (avoids partial acquisition).
    already_locked = [wid for wid in body.work_ids if work_lock.is_locked(wid)]
    if already_locked:
        raise HTTPException(
            status_code=409,
            detail=f"Works already locked: {already_locked}",
        )

    # Acquire locks one by one; handle rare race condition.
    acquired: list[int] = []
    for wid in body.work_ids:
        if not work_lock.acquire(wid, "Running LLM extraction"):
            for a in acquired:
                work_lock.release(a)
            raise HTTPException(
                status_code=409,
                detail=f"Work {wid} became locked during batch acquisition",
            )
        acquired.append(wid)

    n = len(body.work_ids)
    job_id = extraction_jobs.create_job(schema_id, body.work_ids)
    background_tasks.add_task(
        _extraction_bg,
        job_id=job_id,
        schema_id=schema_id,
        work_ids=body.work_ids,
        llm_client=llm_client,
        model_id=model_id,
        provider=provider,
        pdf_root=pdf_root,
        system_prefix=system_prefix,
        re_evaluate_edited=body.re_evaluate_edited,
    )
    return {
        "job_id": job_id,
        "message": f"Extraction started for {n} work{'s' if n != 1 else ''}",
    }


# ---------------------------------------------------------------------------
# Job status
# ---------------------------------------------------------------------------


@router.get("/jobs/{job_id}")
def get_extraction_job(job_id: str):
    """Return the current status of an extraction job.

    Response shape::

        {
            "job_id": "...",
            "schema_id": 1,
            "status": "running" | "completed",
            "progress": {"total": 10, "completed": 3, "failed": 1},
            "works": {
                "42": {"status": "done"},
                "43": {"status": "failed", "error": "..."},
                "44": {"status": "running"},
                "45": {"status": "pending"}
            }
        }

    Returns 404 if no job with the given ``job_id`` is found (jobs are kept
    for one hour after creation).
    """
    job = extraction_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Extraction job not found")

    works = job["works"]
    total = len(works)
    completed = sum(1 for w in works.values() if w["status"] == "done")
    failed = sum(1 for w in works.values() if w["status"] == "failed")

    return {
        "job_id": job["job_id"],
        "schema_id": job["schema_id"],
        "status": job["status"],
        "progress": {"total": total, "completed": completed, "failed": failed},
        "works": works,
    }


# ---------------------------------------------------------------------------
# Export (CSV / LaTeX)
# ---------------------------------------------------------------------------


@router.get("/schemas/{schema_id}/export")
def export_schema(
    schema_id: int,
    fmt: str = Query(..., alias="format", description="'csv' or 'latex'"),
    work_ids: str = Query("", description="Comma-separated work IDs; empty = all with notes"),
    column_ids: str = Query("", description="Comma-separated column IDs; empty = all columns"),
    db: Session = Depends(get_db),
) -> Response:
    """Export an extraction schema as CSV or LaTeX.

    Query parameters:
    - ``format``: ``"csv"`` or ``"latex"`` (required).
    - ``work_ids``: comma-separated integers. If omitted, all works that have
      any notes for this schema are included.
    - ``column_ids``: comma-separated integers. If omitted, all schema columns
      are included.
    """
    from litexplorer.models.library import Work, WorkNote
    from litexplorer.services.extraction import _truncate_note_type
    from litexplorer.services.extraction_export import export_as_csv, export_as_latex

    if fmt not in ("csv", "latex"):
        raise HTTPException(status_code=422, detail="format must be 'csv' or 'latex'")

    schema = db.get(ExtractionSchema, schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Extraction schema not found")

    # Resolve columns (filter if column_ids provided)
    all_columns = sorted(schema.columns, key=lambda c: c.sort_order)
    if column_ids.strip():
        try:
            cids = {int(x.strip()) for x in column_ids.split(",") if x.strip()}
        except ValueError:
            raise HTTPException(status_code=422, detail="column_ids must be comma-separated integers")
        columns = [c for c in all_columns if c.id in cids]
    else:
        columns = all_columns

    if not columns:
        raise HTTPException(status_code=422, detail="No columns to export")

    # Build note_type → column mapping for answer notes only
    answer_type_to_col: dict[str, ExtractionColumn] = {}
    for col in columns:
        at = _truncate_note_type(f"{schema.title} / {col.name}")
        answer_type_to_col[at] = col

    # Resolve work IDs
    if work_ids.strip():
        try:
            wids = [int(x.strip()) for x in work_ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(status_code=422, detail="work_ids must be comma-separated integers")
    else:
        # Discover all works that have any answer note for the selected columns
        stmt = (
            select(WorkNote.work_id)
            .where(WorkNote.note_type.in_(list(answer_type_to_col.keys())))
            .distinct()
        )
        if schema.project_id is not None:
            stmt = stmt.where(
                (WorkNote.project_id == schema.project_id) | (WorkNote.project_id == None)  # noqa: E711
            )
        wids = list(db.scalars(stmt).all())

    if not wids:
        # Return an empty export (header only for CSV, empty table for LaTeX)
        works: list[Work] = []
    else:
        works = list(
            db.scalars(select(Work).where(Work.id.in_(wids)).order_by(Work.publication_year, Work.title)).all()
        )

    # Fetch answer notes for these works + columns
    answer_note_types = list(answer_type_to_col.keys())
    notes_stmt = select(WorkNote).where(
        WorkNote.work_id.in_(wids) if wids else WorkNote.work_id.in_([]),
        WorkNote.note_type.in_(answer_note_types),
    )
    if schema.project_id is not None:
        notes_stmt = notes_stmt.where(
            (WorkNote.project_id == schema.project_id) | (WorkNote.project_id == None)  # noqa: E711
        )
    raw_notes = db.scalars(notes_stmt).all() if wids else []

    # Build (work_id, column_id) → WorkNote mapping
    notes_by_work_column: dict[tuple[int, int], WorkNote] = {}
    for note in raw_notes:
        col = answer_type_to_col.get(note.note_type)
        if col is not None:
            notes_by_work_column[(note.work_id, col.id)] = note

    # Sanitize filename (strip path-unsafe characters)
    safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in schema.title).strip() or "export"

    if fmt == "csv":
        content = export_as_csv(schema, columns, works, notes_by_work_column)
        return Response(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{safe_title}.csv"'},
        )
    else:
        content = export_as_latex(schema, columns, works, notes_by_work_column)
        return Response(
            content=content,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{safe_title}.tex"'},
        )


# ---------------------------------------------------------------------------
# Manual cell fill
# ---------------------------------------------------------------------------


@router.post(
    "/schemas/{schema_id}/columns/{column_id}/works/{work_id}/manual-fill",
    response_model=ExtractionCellResult,
)
def manual_fill_cell(
    schema_id: int,
    column_id: int,
    work_id: int,
    body: ExtractionManualFillRequest,
    db: Session = Depends(get_db),
):
    """Manually fill a single extraction cell with user-provided content.

    Creates or updates the WorkNote for this (work, column) pair with
    ``provenance="user"``.  Any existing ``ai_proposal`` note for the same cell
    is deleted so the proposal is no longer surfaced to the user.

    Returns the updated :class:`ExtractionCellResult` (proposal field will be None).
    """
    from litexplorer.models.library import Work, WorkNote
    from litexplorer.services.extraction import _truncate_note_type

    schema = db.get(ExtractionSchema, schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Extraction schema not found")

    col = db.get(ExtractionColumn, column_id)
    if col is None or col.schema_id != schema_id:
        raise HTTPException(status_code=404, detail="Extraction column not found")

    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=404, detail="Work not found")

    answer_note_type = _truncate_note_type(f"{schema.title} / {col.name}")
    reasoning_note_type = _truncate_note_type(f"{schema.title} / {col.name} / reasoning")

    # Find or create the primary (non-proposal) answer note.
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

    if existing:
        existing.content = body.content
        existing.provenance = "user"
        answer_note = existing
    else:
        answer_note = WorkNote(
            work_id=work_id,
            project_id=schema.project_id,
            content=body.content,
            note_type=answer_note_type,
            provenance="user",
            model_id=None,
        )
        db.add(answer_note)

    # Delete any ai_proposal note (answer + reasoning) for this cell.
    for nt in (answer_note_type, reasoning_note_type):
        stale = db.scalars(
            select(WorkNote)
            .where(
                WorkNote.work_id == work_id,
                WorkNote.note_type == nt,
                WorkNote.project_id == schema.project_id,
                WorkNote.provenance == "ai_proposal",
            )
            .limit(1)
        ).one_or_none()
        if stale:
            db.delete(stale)

    db.commit()
    db.refresh(answer_note)

    # Fetch the primary reasoning note (if any).
    reasoning_note = db.scalars(
        select(WorkNote)
        .where(
            WorkNote.work_id == work_id,
            WorkNote.note_type == reasoning_note_type,
            WorkNote.project_id == schema.project_id,
            WorkNote.provenance != "ai_proposal",
        )
        .limit(1)
    ).one_or_none()

    return ExtractionCellResult(
        work_id=work_id,
        column_id=column_id,
        answer_note=WorkNoteOut.model_validate(answer_note),
        reasoning_note=WorkNoteOut.model_validate(reasoning_note) if reasoning_note else None,
        proposal=None,
    )


@router.delete(
    "/schemas/{schema_id}/columns/{column_id}/works/{work_id}/proposal",
    status_code=204,
)
def dismiss_proposal(
    schema_id: int,
    column_id: int,
    work_id: int,
    db: Session = Depends(get_db),
):
    """Delete the ai_proposal note for a single extraction cell.

    Both the proposal answer note and the proposal reasoning note are removed.
    Returns 204 regardless of whether a proposal existed.
    """
    from litexplorer.models.library import WorkNote
    from litexplorer.services.extraction import _truncate_note_type

    schema = db.get(ExtractionSchema, schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Extraction schema not found")

    col = db.get(ExtractionColumn, column_id)
    if col is None or col.schema_id != schema_id:
        raise HTTPException(status_code=404, detail="Extraction column not found")

    answer_note_type = _truncate_note_type(f"{schema.title} / {col.name}")
    reasoning_note_type = _truncate_note_type(f"{schema.title} / {col.name} / reasoning")

    for nt in (answer_note_type, reasoning_note_type):
        stale = db.scalars(
            select(WorkNote)
            .where(
                WorkNote.work_id == work_id,
                WorkNote.note_type == nt,
                WorkNote.project_id == schema.project_id,
                WorkNote.provenance == "ai_proposal",
            )
            .limit(1)
        ).one_or_none()
        if stale:
            db.delete(stale)

    db.commit()


# ---------------------------------------------------------------------------
# Prompt preview (for UI transparency)
# ---------------------------------------------------------------------------


@router.get("/schemas/{schema_id}/preview-prompt")
def preview_extraction_prompt(
    schema_id: int,
    work_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """Return the extraction prompt for a given work with paper text replaced by a placeholder.

    The actual paper content is replaced by ``[Text of "{title}"]`` so the user can
    inspect the full prompt structure without transmitting the entire paper text to
    the browser.

    Returns ``{"system_text": str, "user_message": str}``.
    """
    from litexplorer.api.settings import get_setting_value
    from litexplorer.models.library import Work, WorkPDF
    from litexplorer.services.extraction import assemble_extraction_prompt

    schema = db.get(ExtractionSchema, schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Extraction schema not found")

    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=404, detail="Work not found")

    columns = sorted(schema.columns, key=lambda c: c.sort_order)

    provider = get_setting_value(db, "llm_provider") or ""
    model_id = get_setting_value(db, "llm_model_id") or ""
    system_prefix = get_setting_value(db, "llm_system_prompt_prefix") or ""

    # Determine whether the work has extracted text available
    has_ready_pdf = (
        db.scalars(
            select(WorkPDF)
            .where(WorkPDF.work_id == work_id, WorkPDF.extraction_status == "ready")
            .limit(1)
        ).one_or_none()
        is not None
    )

    # Use a placeholder in place of the actual paper text
    paper_text_placeholder = f'[Text of "{work.title}"]' if has_ready_pdf else None

    system_text, user_message = assemble_extraction_prompt(
        schema=schema,
        columns=columns,
        paper_text=paper_text_placeholder,
        paper_title=work.title,
        paper_year=work.publication_year,
        system_prefix=system_prefix,
        provider=provider,
        model_id=model_id,
    )

    return {"system_text": system_text, "user_message": user_message}
