"""LLM configuration API — model listing."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from litexplorer.api.deps import get_db
from litexplorer.external.llm_client import make_llm_client

router = APIRouter(prefix="/api/llm", tags=["llm"])


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
