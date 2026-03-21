"""Pydantic schemas for structured extraction."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from sotascope.schemas.notes import WorkNoteOut


# ---------------------------------------------------------------------------
# Column schemas
# ---------------------------------------------------------------------------


class ExtractionColumnCreate(BaseModel):
    name: str
    prompt: str
    description: Optional[str] = None
    allowed_values: Optional[list[str]] = None
    sort_order: int = 0


class ExtractionColumnUpdate(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None
    description: Optional[str] = None
    allowed_values: Optional[list[str]] = None
    sort_order: Optional[int] = None


class ExtractionColumnOut(BaseModel):
    id: int
    schema_id: int
    name: str
    prompt: str
    description: Optional[str]
    allowed_values: Optional[list[str]]
    sort_order: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Schema schemas
# ---------------------------------------------------------------------------


class ExtractionSchemaCreate(BaseModel):
    title: str
    description: Optional[str] = None
    project_id: Optional[int] = None


class ExtractionSchemaUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None


class ExtractionSchemaOut(BaseModel):
    id: int
    project_id: Optional[int]
    title: str
    description: Optional[str]
    is_promoted: bool = False
    selected_work_ids: Optional[list[int]] = None
    created_at: datetime
    updated_at: datetime
    columns: list[ExtractionColumnOut] = []

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Reorder
# ---------------------------------------------------------------------------


class ColumnReorderItem(BaseModel):
    column_id: int
    sort_order: int


class ColumnReorderRequest(BaseModel):
    columns: list[ColumnReorderItem]


# ---------------------------------------------------------------------------
# Extraction result
# ---------------------------------------------------------------------------


class ExtractionColumnResult(BaseModel):
    """The extracted answer + reasoning for a single column."""

    column_id: int
    column_name: str
    answer: str
    reasoning: str
    note: WorkNoteOut


class ExtractionWorkResult(BaseModel):
    """All extracted column results for a single work."""

    work_id: int
    columns: list[ExtractionColumnResult]
    parsing_method: str = "json"


class ExtractionBatchRequest(BaseModel):
    work_ids: list[int]
    re_evaluate_edited: bool = False


class ExtractionBatchResult(BaseModel):
    """Results for multiple works."""

    results: list[ExtractionWorkResult]
    errors: list[dict] = []


# ---------------------------------------------------------------------------
# Existing-note lookup (results table)
# ---------------------------------------------------------------------------


class ExtractionCellResult(BaseModel):
    """Existing extraction notes for a single (work_id, column_id) cell."""

    work_id: int
    column_id: int
    answer_note: WorkNoteOut
    reasoning_note: Optional[WorkNoteOut] = None
    proposal: Optional[WorkNoteOut] = None


class ExtractionResultsResponse(BaseModel):
    """All extraction note cells for a set of works against a schema."""

    cells: list[ExtractionCellResult]


class ExtractionManualFillRequest(BaseModel):
    content: str


# ---------------------------------------------------------------------------
# Paste extraction (external JSON import)
# ---------------------------------------------------------------------------


class PasteExtractionRequest(BaseModel):
    """Raw JSON pasted by the user from an external LLM run.

    Accepted formats:
    - ``{"columns": {"Col Name": {"answer": "...", "reasoning": "..."}}}``
    - ``{"Col Name": {"answer": "...", "reasoning": "..."}}`` (flat, no wrapper)
    """

    data: dict


class PasteExtractionResult(BaseModel):
    """Result of a paste-extraction operation."""

    filled: list[str]       # column names that were written
    skipped: list[dict]     # [{"column": name, "reason": "has user/reviewed value"}]
    not_found: list[str]    # column names in JSON that didn't match any schema column
