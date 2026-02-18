"""Enrichment API router — import works from OpenAlex and fetch citations."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from litexplorer.api.deps import get_db
from litexplorer.config import settings
from litexplorer.external.openalex import OpenAlexClient
from litexplorer.schemas.enrichment import (
    CitationResult,
    EnrichDOIBatchRequest,
    EnrichDOIBatchResult,
    EnrichDOIRequest,
    EnrichDOIResult,
)
from litexplorer.schemas.works import WorkOut
from litexplorer.services.enrichment import EnrichmentService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/enrich", tags=["enrichment"])


def _get_client() -> OpenAlexClient:
    """Create an OpenAlex client, checking that the API key is configured."""
    if not settings.openalex_api_key:
        raise HTTPException(
            status_code=503,
            detail="OpenAlex API key not configured. Set LITEXPLORER_OPENALEX_API_KEY.",
        )
    return OpenAlexClient(
        base_url=settings.openalex_base_url,
        api_key=settings.openalex_api_key,
    )


@router.post("/doi", response_model=EnrichDOIResult)
def enrich_by_doi(body: EnrichDOIRequest, db: Session = Depends(get_db)):
    """Import a single work by DOI from OpenAlex."""
    client = _get_client()
    try:
        svc = EnrichmentService(db=db, client=client)
        work = svc.import_by_doi(body.doi)
    finally:
        client.close()

    if work is None:
        raise HTTPException(status_code=404, detail=f"DOI not found in OpenAlex: {body.doi}")

    return EnrichDOIResult(work=WorkOut.model_validate(work))


@router.post("/doi/batch", response_model=EnrichDOIBatchResult)
def enrich_by_doi_batch(body: EnrichDOIBatchRequest, db: Session = Depends(get_db)):
    """Import multiple works by DOI. Partial failures are reported, not raised."""
    client = _get_client()
    try:
        svc = EnrichmentService(db=db, client=client)
        results: list[EnrichDOIResult] = []
        errors: list[dict] = []

        for doi in body.dois:
            try:
                work = svc.import_by_doi(doi)
                if work is None:
                    errors.append({"doi": doi, "error": "Not found in OpenAlex"})
                else:
                    results.append(EnrichDOIResult(work=WorkOut.model_validate(work)))
            except Exception as e:
                logger.exception("Error importing DOI %s", doi)
                errors.append({"doi": doi, "error": str(e)})
    finally:
        client.close()

    return EnrichDOIBatchResult(results=results, errors=errors)


@router.post("/works/{work_id}/citations/backward", response_model=CitationResult)
def fetch_backward_citations(work_id: int, db: Session = Depends(get_db)):
    """Fetch and persist backward citations (references) for a work."""
    client = _get_client()
    try:
        svc = EnrichmentService(db=db, client=client)
        try:
            works = svc.fetch_backward_citations(work_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
    finally:
        client.close()

    return CitationResult(
        works=[WorkOut.model_validate(w) for w in works],
        count=len(works),
    )


@router.post("/works/{work_id}/citations/forward", response_model=CitationResult)
def fetch_forward_citations(
    work_id: int,
    force_refresh: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Fetch and persist forward citations (papers citing this work)."""
    client = _get_client()
    try:
        svc = EnrichmentService(db=db, client=client)
        try:
            works = svc.fetch_forward_citations(work_id, force_refresh=force_refresh)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
    finally:
        client.close()

    return CitationResult(
        works=[WorkOut.model_validate(w) for w in works],
        count=len(works),
    )
