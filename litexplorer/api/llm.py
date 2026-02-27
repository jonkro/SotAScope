"""LLM configuration API — model listing and per-paper chat."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from litexplorer.api.deps import get_db
from litexplorer.external.llm_client import ContextDocument, make_llm_client
from litexplorer.models.library import Work, WorkPDF

router = APIRouter(prefix="/api/llm", tags=["llm"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class ChatPaperEntry(BaseModel):
    work_id: int
    use_pdf: bool = False
    remark: Optional[str] = None


class ChatRequest(BaseModel):
    project_id: Optional[int] = None
    papers: list[ChatPaperEntry] = []
    history: list[dict] = []
    message: str


class ChatResponse(BaseModel):
    reply: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_pdf_root(db: Session) -> Path:
    """Resolve PDF storage root: DB setting > config default."""
    from litexplorer.api.settings import get_setting_value
    from litexplorer.config import settings as app_settings

    custom = get_setting_value(db, "pdf_storage_path")
    if custom:
        return Path(custom)
    return app_settings.pdf_dir


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/models")
def list_llm_models(db: Session = Depends(get_db)) -> dict:
    """Return the list of models available from the configured LLM provider.

    Reads ``llm_provider``, ``llm_api_key``, ``llm_model_id``, and
    ``llm_base_url`` from the DB settings.

    - If ``llm_provider`` is not configured, returns ``{"models": []}``.
    - On SDK / network errors, returns HTTP 200 with
      ``{"models": [], "error": "<message>"}`` so the frontend can surface
      the problem gracefully without treating it as a fatal API failure.
    """
    from litexplorer.api.settings import get_setting_value

    provider = get_setting_value(db, "llm_provider") or ""
    if not provider:
        return {"models": []}

    api_key = get_setting_value(db, "llm_api_key") or ""
    model_id = get_setting_value(db, "llm_model_id") or ""
    base_url = get_setting_value(db, "llm_base_url") or None

    try:
        client = make_llm_client(provider, api_key, model_id, base_url)
        models = client.list_models()
        return {"models": models}
    except Exception as exc:
        return {"models": [], "error": str(exc)}


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, db: Session = Depends(get_db)) -> ChatResponse:
    """Send a chat message with optional paper context to the configured LLM.

    - Returns HTTP 400 if ``llm_provider`` or ``llm_model_id`` is not configured.
    - Returns HTTP 400 if ``use_pdf=True`` is requested for a non-Anthropic provider.
    - Returns HTTP 404 if a requested ``work_id`` does not exist.
    - Returns HTTP 502 on SDK / network errors from the LLM provider.
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

    # Validate PDF vision requirement before touching files
    if any(p.use_pdf for p in body.papers) and provider != "anthropic":
        raise HTTPException(
            status_code=400,
            detail="PDF vision requires Anthropic provider",
        )

    pdf_root = _get_pdf_root(db)

    # Build context documents
    context_documents: list[ContextDocument] = []
    for entry in body.papers:
        work = db.get(Work, entry.work_id)
        if work is None:
            raise HTTPException(status_code=404, detail=f"Work {entry.work_id} not found")

        pdf_bytes: bytes | None = None
        text: str | None = None

        if entry.use_pdf:
            # Find the primary PDF (is_primary=True first, then lowest id)
            primary_pdf = db.scalars(
                select(WorkPDF)
                .where(WorkPDF.work_id == entry.work_id, WorkPDF.is_primary == True)  # noqa: E712
                .limit(1)
            ).one_or_none()
            if primary_pdf is None:
                primary_pdf = db.scalars(
                    select(WorkPDF)
                    .where(WorkPDF.work_id == entry.work_id)
                    .order_by(WorkPDF.id)
                    .limit(1)
                ).one_or_none()

            if primary_pdf is not None:
                pdf_path = pdf_root / str(entry.work_id) / primary_pdf.filename
                if pdf_path.is_file():
                    pdf_bytes = pdf_path.read_bytes()
                # else: file missing on disk — fall back to no content gracefully
        else:
            # Find the primary PDF with extraction_status="ready"
            ready_pdf = db.scalars(
                select(WorkPDF)
                .where(
                    WorkPDF.work_id == entry.work_id,
                    WorkPDF.is_primary == True,  # noqa: E712
                    WorkPDF.extraction_status == "ready",
                )
                .limit(1)
            ).one_or_none()
            if ready_pdf is None:
                # Fall back to any ready PDF for this work
                ready_pdf = db.scalars(
                    select(WorkPDF)
                    .where(
                        WorkPDF.work_id == entry.work_id,
                        WorkPDF.extraction_status == "ready",
                    )
                    .order_by(WorkPDF.id)
                    .limit(1)
                ).one_or_none()

            if ready_pdf is not None:
                txt_path = pdf_root / str(entry.work_id) / Path(ready_pdf.filename).with_suffix(".txt")
                if txt_path.is_file():
                    text = txt_path.read_text(encoding="utf-8")
            # No ready PDF → text stays None; frontend prevents sending such papers

        context_documents.append(
            ContextDocument(
                work_id=entry.work_id,
                title=work.title,
                year=work.publication_year,
                text=text,
                pdf_bytes=pdf_bytes,
                remark=entry.remark,
            )
        )

    # Assemble full message list (history + current user turn)
    messages = list(body.history) + [{"role": "user", "content": body.message}]

    try:
        llm_client = make_llm_client(provider, api_key, model_id, base_url)
        reply = llm_client.chat(messages=messages, context_documents=context_documents)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ChatResponse(reply=reply)
