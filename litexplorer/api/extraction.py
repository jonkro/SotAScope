"""Structured extraction API — schema/column CRUD and extraction execution."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from litexplorer.api.deps import get_db
from litexplorer.external.llm_client import make_llm_client
from litexplorer.models.extraction import ExtractionColumn, ExtractionSchema
from litexplorer.schemas.extraction import (
    ColumnReorderRequest,
    ExtractionBatchRequest,
    ExtractionBatchResult,
    ExtractionColumnCreate,
    ExtractionColumnOut,
    ExtractionColumnResult,
    ExtractionColumnUpdate,
    ExtractionSchemaCreate,
    ExtractionSchemaOut,
    ExtractionSchemaUpdate,
    ExtractionWorkResult,
)
from litexplorer.schemas.notes import WorkNoteOut

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
    return client, model_id


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
) -> ExtractionWorkResult:
    from litexplorer.services.extraction import run_extraction_for_work

    items = run_extraction_for_work(
        db=db,
        work_id=work_id,
        schema_id=schema_id,
        llm_client=llm_client,
        model_id=model_id,
        pdf_root=pdf_root,
        system_prefix=system_prefix,
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
    return ExtractionWorkResult(work_id=work_id, columns=column_results)


@router.post("/schemas/{schema_id}/extract/{work_id}", response_model=ExtractionWorkResult)
def extract_for_work(
    schema_id: int,
    work_id: int,
    db: Session = Depends(get_db),
):
    """Run extraction for a single work. Returns proposed WorkNotes."""
    from litexplorer.api.settings import get_setting_value

    schema = db.get(ExtractionSchema, schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Extraction schema not found")

    llm_client, model_id = _build_llm_client(db)
    pdf_root = _get_pdf_root(db)
    system_prefix = get_setting_value(db, "llm_system_prompt_prefix") or ""

    try:
        return _run_and_format(db, schema_id, work_id, llm_client, model_id, pdf_root, system_prefix)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/schemas/{schema_id}/extract", response_model=ExtractionBatchResult)
def extract_batch(
    schema_id: int,
    body: ExtractionBatchRequest,
    db: Session = Depends(get_db),
):
    """Run extraction for multiple works sequentially. Returns all proposed WorkNotes."""
    from litexplorer.api.settings import get_setting_value

    schema = db.get(ExtractionSchema, schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Extraction schema not found")

    llm_client, model_id = _build_llm_client(db)
    pdf_root = _get_pdf_root(db)
    system_prefix = get_setting_value(db, "llm_system_prompt_prefix") or ""

    results: list[ExtractionWorkResult] = []
    errors: list[dict] = []

    for work_id in body.work_ids:
        try:
            result = _run_and_format(
                db, schema_id, work_id, llm_client, model_id, pdf_root, system_prefix
            )
            results.append(result)
        except ValueError as exc:
            errors.append({"work_id": work_id, "error": str(exc)})
        except Exception as exc:
            errors.append({"work_id": work_id, "error": str(exc)})

    return ExtractionBatchResult(results=results, errors=errors)
