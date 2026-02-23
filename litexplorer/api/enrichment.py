"""Enrichment API router — import works from OpenAlex and fetch citations."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from litexplorer.api.deps import get_db
from litexplorer.config import settings
from litexplorer.external.crossref import CrossrefClient
from litexplorer.external.openalex import OpenAlexClient
from litexplorer.schemas.enrichment import (
    BatchResolveDOIRequest,
    CitationResult,
    ConfirmDOIRequest,
    CrossrefEnrichResult,
    DOIResolutionResult,
    EnrichDOIBatchRequest,
    EnrichDOIBatchResult,
    EnrichDOIRequest,
    EnrichDOIResult,
)
from litexplorer.schemas.works import WorkOut
from litexplorer.services.enrichment import EnrichmentService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/enrich", tags=["enrichment"])


def _get_contact_email(db: Session) -> str | None:
    """Read API contact email: DB setting first, then env var fallback."""
    from litexplorer.api.settings import get_setting_value

    email = get_setting_value(db, "api_contact_email")
    if email:
        return email
    # Env var fallback — either variable works
    return settings.openalex_api_key or settings.crossref_mailto or None


def _get_ssl_verify(db: Session) -> bool:
    """Read ssl_verify setting from DB. Defaults to True (SSL verification enabled)."""
    from litexplorer.api.settings import get_setting_value

    val = get_setting_value(db, "ssl_verify")
    if val is None:
        return True
    return val.lower() != "false"


def _get_crossref_client(db: Session) -> CrossrefClient:
    """Create a Crossref client with polite-pool email if available."""
    email = _get_contact_email(db)
    ssl_verify = _get_ssl_verify(db)
    if not email:
        logger.warning("No API contact email configured — Crossref requests will not use polite pool")
    if not ssl_verify:
        logger.warning("SSL certificate verification is disabled for Crossref requests")
    return CrossrefClient(
        base_url=settings.crossref_base_url,
        mailto=email,
        verify=ssl_verify,
    )


def _get_client(db: Session) -> OpenAlexClient:
    """Create an OpenAlex client with polite-pool email if available."""
    email = _get_contact_email(db)
    ssl_verify = _get_ssl_verify(db)
    if not email:
        logger.warning("No API contact email configured — OpenAlex requests will not use polite pool")
    if not ssl_verify:
        logger.warning("SSL certificate verification is disabled for OpenAlex requests")
    return OpenAlexClient(
        base_url=settings.openalex_base_url,
        api_key=email,
        verify=ssl_verify,
    )


@router.post("/doi", response_model=EnrichDOIResult)
def enrich_by_doi(body: EnrichDOIRequest, db: Session = Depends(get_db)):
    """Import a single work by DOI from OpenAlex (with Crossref fallback)."""
    client = _get_client(db)
    cr_client = _get_crossref_client(db)
    try:
        svc = EnrichmentService(db=db, client=client, crossref_client=cr_client)
        work = svc.import_by_doi(body.doi)
    finally:
        client.close()
        cr_client.close()

    if work is None:
        raise HTTPException(status_code=404, detail=f"DOI not found: {body.doi}")

    return EnrichDOIResult(work=WorkOut.model_validate(work))


@router.post("/doi/batch", response_model=EnrichDOIBatchResult)
def enrich_by_doi_batch(body: EnrichDOIBatchRequest, db: Session = Depends(get_db)):
    """Import multiple works by DOI. Partial failures are reported, not raised."""
    client = _get_client(db)
    cr_client = _get_crossref_client(db)
    try:
        svc = EnrichmentService(db=db, client=client, crossref_client=cr_client)
        results: list[EnrichDOIResult] = []
        errors: list[dict] = []

        for doi in body.dois:
            try:
                work = svc.import_by_doi(doi)
                if work is None:
                    errors.append({"doi": doi, "error": "Not found"})
                else:
                    results.append(EnrichDOIResult(work=WorkOut.model_validate(work)))
            except Exception as e:
                logger.exception("Error importing DOI %s", doi)
                errors.append({"doi": doi, "error": str(e)})
    finally:
        client.close()
        cr_client.close()

    return EnrichDOIBatchResult(results=results, errors=errors)


@router.post("/works/{work_id}/citations/backward", response_model=CitationResult)
def fetch_backward_citations(work_id: int, db: Session = Depends(get_db)):
    """Fetch and persist backward citations (references) for a work."""
    client = _get_client(db)
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
    client = _get_client(db)
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


@router.post("/works/{work_id}/crossref", response_model=CrossrefEnrichResult)
def enrich_from_crossref(work_id: int, db: Session = Depends(get_db)):
    """Enrich a work's venue metadata (ISSN, publisher) from Crossref."""
    cr_client = _get_crossref_client(db)
    oa_client = _get_client(db)
    try:
        svc = EnrichmentService(db=db, client=oa_client, crossref_client=cr_client)
        try:
            work = svc.enrich_from_crossref(work_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))
    finally:
        cr_client.close()
        oa_client.close()

    venue_issn = None
    venue_publisher = None
    if work.venue:
        venue_issn = work.venue.issn
        venue_publisher = work.venue.publisher

    return CrossrefEnrichResult(
        work=WorkOut.model_validate(work),
        venue_issn=venue_issn,
        venue_publisher=venue_publisher,
    )


@router.post("/works/{work_id}/resolve-doi", response_model=DOIResolutionResult)
def resolve_doi(work_id: int, db: Session = Depends(get_db)):
    """Attempt to resolve a DOI for a DOI-less work via Crossref fuzzy search."""
    cr_client = _get_crossref_client(db)
    oa_client = _get_client(db)
    try:
        svc = EnrichmentService(db=db, client=oa_client, crossref_client=cr_client)
        try:
            return svc.resolve_doi_for_work(work_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))
    finally:
        cr_client.close()
        oa_client.close()


@router.post("/works/{work_id}/confirm-doi", response_model=WorkOut)
def confirm_doi(work_id: int, body: ConfirmDOIRequest, db: Session = Depends(get_db)):
    """Confirm a DOI resolution candidate for a work.

    Does NOT require OpenAlex — purely a local DB write.
    """
    from sqlalchemy import select as sa_select
    from litexplorer.models.library import Work as WorkModel

    work = db.get(WorkModel, work_id)
    if not work:
        raise HTTPException(status_code=404, detail=f"Work {work_id} not found")

    doi = body.doi.lower().strip()
    existing = db.execute(
        sa_select(WorkModel).where(WorkModel.doi == doi, WorkModel.id != work_id)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"DOI {doi} is already assigned to another work: \"{existing.title}\" (id={existing.id})",
        )

    work.doi = doi
    work.doi_auto_resolved = True
    db.commit()
    return WorkOut.model_validate(work)


@router.post("/works/resolve-doi/batch", response_model=list[DOIResolutionResult])
def resolve_doi_batch(body: BatchResolveDOIRequest, db: Session = Depends(get_db)):
    """Batch resolve DOIs for multiple works."""
    cr_client = _get_crossref_client(db)
    oa_client = _get_client(db)
    try:
        svc = EnrichmentService(db=db, client=oa_client, crossref_client=cr_client)
        results: list[DOIResolutionResult] = []
        for wid in body.work_ids:
            try:
                results.append(svc.resolve_doi_for_work(wid))
            except (ValueError, RuntimeError) as e:
                logger.warning("DOI resolution failed for work %d: %s", wid, e)
                results.append(DOIResolutionResult(work_id=wid))
    finally:
        cr_client.close()
        oa_client.close()
    return results
