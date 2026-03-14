"""Enrichment API router — import works from OpenAlex and fetch citations."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from litexplorer.api.deps import get_db
from litexplorer.config import settings
from litexplorer.external.crossref import CrossrefClient
from litexplorer.external.openalex import OpenAlexClient
from litexplorer.external.semantic_scholar import SemanticScholarClient
from litexplorer.schemas.enrichment import (
    BatchResolveDOIRequest,
    CitationResult,
    ConfirmDOIRequest,
    CrossrefEnrichResult,
    DOIInfoResult,
    DOIResolutionResult,
    EnrichDOIBatchRequest,
    EnrichDOIBatchResult,
    EnrichDOIRequest,
    EnrichDOIResult,
    GrobidEnrichResult,
    SearchImportCandidatesResult,
    SearchImportConfirmRequest,
    SearchImportRequest,
    SemanticScholarEnrichResult,
)
from litexplorer.schemas.works import WorkOut
from litexplorer.services.enrichment import EnrichmentService
from litexplorer.services.work_lock import work_lock

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/enrich", tags=["enrichment"])


# ---------------------------------------------------------------------------
# Client / settings helpers
# ---------------------------------------------------------------------------

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


def _get_s2_api_key(db: Session) -> str | None:
    """Read Semantic Scholar API key from DB setting."""
    from litexplorer.api.settings import get_setting_value

    val = get_setting_value(db, "s2_api_key")
    return val or None


def _get_ss_client(db: Session) -> SemanticScholarClient:
    """Create a Semantic Scholar client, respecting ssl_verify and s2_api_key settings."""
    ssl_verify = _get_ssl_verify(db)
    api_key = _get_s2_api_key(db)
    if not ssl_verify:
        logger.warning("SSL certificate verification is disabled for Semantic Scholar requests")
    if api_key:
        logger.debug("Using Semantic Scholar API key for authenticated access")
    return SemanticScholarClient(api_key=api_key, verify=ssl_verify)


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


def _get_grobid_url_setting(db: Session) -> str:
    """Read grobid_url setting from DB. Returns empty string if not configured."""
    from litexplorer.api.settings import get_setting_value

    return get_setting_value(db, "grobid_url") or ""


# ---------------------------------------------------------------------------
# Background task functions (each owns its own DB session + releases lock)
# ---------------------------------------------------------------------------

def _fetch_backward_citations_bg(work_id: int) -> None:
    """Background: fetch backward citations (OpenAlex) and release work lock."""
    from litexplorer.database import SessionLocal

    db = SessionLocal()
    try:
        client = _get_client(db)
        try:
            svc = EnrichmentService(db=db, client=client)
            svc.fetch_backward_citations(work_id)
        except ValueError:
            logger.warning("Work %d not found during background backward citation fetch", work_id)
        except Exception:
            logger.exception("Error fetching backward citations for work %d", work_id)
        finally:
            client.close()
    finally:
        work_lock.release(work_id)
        db.close()


def _fetch_forward_citations_bg(work_id: int, force_refresh: bool) -> None:
    """Background: fetch forward citations (OpenAlex) and release work lock."""
    from litexplorer.database import SessionLocal

    db = SessionLocal()
    try:
        client = _get_client(db)
        try:
            svc = EnrichmentService(db=db, client=client)
            svc.fetch_forward_citations(work_id, force_refresh=force_refresh)
        except ValueError:
            logger.warning("Work %d not found during background forward citation fetch", work_id)
        except Exception:
            logger.exception("Error fetching forward citations for work %d", work_id)
        finally:
            client.close()
    finally:
        work_lock.release(work_id)
        db.close()


def _enrich_crossref_bg(work_id: int) -> None:
    """Background: enrich venue from Crossref and release work lock."""
    from litexplorer.database import SessionLocal

    db = SessionLocal()
    try:
        cr_client = _get_crossref_client(db)
        oa_client = _get_client(db)
        try:
            svc = EnrichmentService(db=db, client=oa_client, crossref_client=cr_client)
            svc.enrich_from_crossref(work_id)
        except ValueError:
            logger.warning("Crossref enrichment failed for work %d (validation error)", work_id)
        except RuntimeError:
            logger.warning("Crossref service error during enrichment for work %d", work_id)
        except Exception:
            logger.exception("Unexpected error in background Crossref enrichment for work %d", work_id)
        finally:
            cr_client.close()
            oa_client.close()
    finally:
        work_lock.release(work_id)
        db.close()


def _enrich_semantic_scholar_bg(work_id: int, direction: str) -> None:
    """Background: fetch refs/cites from Semantic Scholar and release work lock."""
    from litexplorer.database import SessionLocal

    db = SessionLocal()
    try:
        ss_client = _get_ss_client(db)
        oa_client = _get_client(db)
        try:
            svc = EnrichmentService(db=db, client=oa_client)
            svc.enrich_from_semantic_scholar(work_id, ss_client, direction=direction)
        except (ValueError, RuntimeError):
            logger.warning("Semantic Scholar enrichment failed for work %d", work_id)
        except Exception:
            logger.exception("Unexpected error in background S2 enrichment for work %d", work_id)
        finally:
            ss_client.close()
            oa_client.close()
    finally:
        work_lock.release(work_id)
        db.close()


def _enrich_grobid_bg(work_id: int) -> None:
    """Background: extract references via GROBID and release work lock."""
    from litexplorer.database import SessionLocal
    from litexplorer.external.grobid import GrobidError

    db = SessionLocal()
    try:
        oa_client = _get_client(db)
        cr_client = _get_crossref_client(db)
        try:
            svc = EnrichmentService(db=db, client=oa_client, crossref_client=cr_client)
            svc.enrich_from_grobid(work_id)
        except ValueError as e:
            logger.warning("GROBID enrichment validation error for work %d: %s", work_id, e)
        except GrobidError as e:
            logger.warning("GROBID service error for work %d: %s", work_id, e)
        except Exception:
            logger.exception("Unexpected error in background GROBID enrichment for work %d", work_id)
        finally:
            oa_client.close()
            cr_client.close()
    finally:
        work_lock.release(work_id)
        db.close()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

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


@router.get("/doi/info", response_model=DOIInfoResult)
def doi_info(doi: str = Query(...), db: Session = Depends(get_db)):
    """Look up a DOI and return its title/year without importing the work.

    Checks the OpenAlex cache first; falls back to a live API call if not
    cached.  Returns ``found=False`` (with ``title=None``) when the DOI is
    not found on either OpenAlex or Crossref.  Never persists anything.
    """
    import json as _json

    from litexplorer.external.openalex import parse_work as _parse_oa
    from litexplorer.models.cache import ApiCache

    doi = doi.strip().lower().removeprefix("https://doi.org/")

    # 1. Check local cache (OpenAlex)
    from sqlalchemy import select as _select

    cache_key = f"work:doi:{doi}"
    cached = db.execute(
        _select(ApiCache).where(ApiCache.source == "openalex", ApiCache.query_key == cache_key)
    ).scalar_one_or_none()
    if cached:
        try:
            raw = _json.loads(cached.response_json)
            ext = _parse_oa(raw)
            return DOIInfoResult(doi=doi, title=ext.title, year=ext.publication_year, found=True)
        except Exception:
            pass  # fall through to live lookup

    # 2. Live OpenAlex lookup
    client = _get_client(db)
    try:
        raw = client.get_work_by_doi_raw(doi)
        if raw is not None:
            ext = _parse_oa(raw)
            return DOIInfoResult(doi=doi, title=ext.title, year=ext.publication_year, found=True)
    except Exception:
        pass
    finally:
        client.close()

    # 3. Crossref fallback
    cr_client = _get_crossref_client(db)
    try:
        raw_cr = cr_client.get_work_by_doi_raw(doi)
        if raw_cr is not None:
            ext = parse_crossref_work(raw_cr)
            return DOIInfoResult(doi=doi, title=ext.title, year=ext.publication_year, found=True)
    except Exception:
        pass
    finally:
        cr_client.close()

    return DOIInfoResult(doi=doi, found=False)


@router.post("/works/{work_id}/citations/backward")
def fetch_backward_citations(
    work_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Schedule background fetch of backward citations (references) for a work."""
    from litexplorer.models.library import Work as WorkModel

    work = db.get(WorkModel, work_id)
    if not work:
        raise HTTPException(status_code=404, detail=f"Work {work_id} not found")

    if not work_lock.acquire(work_id, "Fetching backward citations (OpenAlex)"):
        status = work_lock.get_status(work_id)
        raise HTTPException(
            status_code=409,
            detail=f"Work {work_id} is currently being processed: {status['task']}",
        )

    background_tasks.add_task(_fetch_backward_citations_bg, work_id)
    return JSONResponse(
        status_code=202,
        content={"message": "Fetching backward citations in background", "work_id": work_id},
    )


@router.post("/works/{work_id}/citations/forward")
def fetch_forward_citations(
    work_id: int,
    background_tasks: BackgroundTasks,
    force_refresh: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Schedule background fetch of forward citations (papers citing this work)."""
    from litexplorer.models.library import Work as WorkModel

    work = db.get(WorkModel, work_id)
    if not work:
        raise HTTPException(status_code=404, detail=f"Work {work_id} not found")

    if not work_lock.acquire(work_id, "Fetching forward citations (OpenAlex)"):
        status = work_lock.get_status(work_id)
        raise HTTPException(
            status_code=409,
            detail=f"Work {work_id} is currently being processed: {status['task']}",
        )

    background_tasks.add_task(_fetch_forward_citations_bg, work_id, force_refresh)
    return JSONResponse(
        status_code=202,
        content={"message": "Fetching forward citations in background", "work_id": work_id},
    )


@router.post("/works/{work_id}/crossref")
def enrich_from_crossref(
    work_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Schedule background enrichment of venue metadata from Crossref."""
    from litexplorer.models.library import Work as WorkModel

    work = db.get(WorkModel, work_id)
    if not work:
        raise HTTPException(status_code=404, detail=f"Work {work_id} not found")
    if not work.doi:
        raise HTTPException(status_code=404, detail="Work has no DOI; cannot enrich from Crossref")

    if not work_lock.acquire(work_id, "Enriching venue from Crossref"):
        status = work_lock.get_status(work_id)
        raise HTTPException(
            status_code=409,
            detail=f"Work {work_id} is currently being processed: {status['task']}",
        )

    background_tasks.add_task(_enrich_crossref_bg, work_id)
    return JSONResponse(
        status_code=202,
        content={"message": "Enriching from Crossref in background", "work_id": work_id},
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


@router.post("/works/{work_id}/semantic-scholar")
def enrich_from_semantic_scholar(
    work_id: int,
    background_tasks: BackgroundTasks,
    direction: str = Query("both"),
    db: Session = Depends(get_db),
):
    """Schedule background enrichment of refs/cites from Semantic Scholar."""
    from litexplorer.models.library import Work as WorkModel

    if direction not in ("both", "backward", "forward"):
        raise HTTPException(
            status_code=400, detail="direction must be 'both', 'backward', or 'forward'"
        )

    work = db.get(WorkModel, work_id)
    if not work:
        raise HTTPException(status_code=404, detail=f"Work {work_id} not found")

    if not work.doi and not work.semantic_scholar_id:
        raise HTTPException(
            status_code=400,
            detail="Work has no DOI or Semantic Scholar ID; cannot enrich from Semantic Scholar",
        )

    if not work_lock.acquire(work_id, f"Fetching citations from Semantic Scholar ({direction})"):
        status = work_lock.get_status(work_id)
        raise HTTPException(
            status_code=409,
            detail=f"Work {work_id} is currently being processed: {status['task']}",
        )

    background_tasks.add_task(_enrich_semantic_scholar_bg, work_id, direction)
    return JSONResponse(
        status_code=202,
        content={"message": "Fetching Semantic Scholar citations in background", "work_id": work_id},
    )


@router.post("/works/{work_id}/grobid")
def enrich_from_grobid(
    work_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Schedule background reference extraction from a work's primary PDF via GROBID."""
    from sqlalchemy import select as sa_select
    from litexplorer.models.library import Work as WorkModel, WorkPDF as WorkPDFModel

    work = db.get(WorkModel, work_id)
    if not work:
        raise HTTPException(status_code=404, detail=f"Work {work_id} not found")

    primary_pdf = db.execute(
        sa_select(WorkPDFModel).where(
            WorkPDFModel.work_id == work_id,
            WorkPDFModel.is_primary == True,  # noqa: E712
        )
    ).scalar_one_or_none()
    if not primary_pdf:
        raise HTTPException(status_code=404, detail="No PDF available for this work")

    grobid_url = _get_grobid_url_setting(db)
    if not grobid_url:
        raise HTTPException(status_code=400, detail="GROBID is not configured")

    if not work_lock.acquire(work_id, "Extracting references via GROBID"):
        status = work_lock.get_status(work_id)
        raise HTTPException(
            status_code=409,
            detail=f"Work {work_id} is currently being processed: {status['task']}",
        )

    background_tasks.add_task(_enrich_grobid_bg, work_id)
    return JSONResponse(
        status_code=202,
        content={"message": "Extracting GROBID references in background", "work_id": work_id},
    )


@router.post("/search-import/candidates", response_model=SearchImportCandidatesResult)
def search_import_candidates(body: SearchImportRequest, db: Session = Depends(get_db)):
    """Search for papers by title (optionally authors/year).

    Crossref is queried first; Semantic Scholar is used as fallback if Crossref
    returns no results.  Returns up to 5 ranked candidates.
    """
    cr_client = _get_crossref_client(db)
    ss_client = _get_ss_client(db)
    oa_client = _get_client(db)
    try:
        svc = EnrichmentService(db=db, client=oa_client, crossref_client=cr_client)
        candidates = svc.search_import_candidates(
            title=body.title,
            authors=body.authors,
            year=body.year,
            crossref_client=cr_client,
            ss_client=ss_client,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            raise HTTPException(
                status_code=429,
                detail=(
                    "Semantic Scholar rate limit reached — please wait a moment and try again. "
                    "Adding a Semantic Scholar API key in Settings increases the rate limit."
                ),
            )
        raise
    finally:
        cr_client.close()
        ss_client.close()
        oa_client.close()
    return SearchImportCandidatesResult(candidates=candidates)


@router.post("/search-import/confirm", response_model=EnrichDOIResult)
def search_import_confirm(body: SearchImportConfirmRequest, db: Session = Depends(get_db)):
    """Import a paper selected from search candidates.

    Accepts either a DOI or a Semantic Scholar paper ID.  If a DOI is provided
    it takes precedence and the standard OpenAlex-first import pipeline is used.
    """
    if not body.doi and not body.semantic_scholar_id:
        raise HTTPException(
            status_code=400,
            detail="Either doi or semantic_scholar_id must be provided",
        )

    if body.doi:
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
        source = "openalex" if work.openalex_id else "crossref"
        return EnrichDOIResult(work=WorkOut.model_validate(work), source=source)

    # Semantic Scholar ID only
    ss_client = _get_ss_client(db)
    oa_client = _get_client(db)
    try:
        svc = EnrichmentService(db=db, client=oa_client)
        work = svc.import_by_semantic_scholar_id(body.semantic_scholar_id, ss_client)
    finally:
        ss_client.close()
        oa_client.close()
    if work is None:
        raise HTTPException(
            status_code=404,
            detail=f"Paper not found on Semantic Scholar: {body.semantic_scholar_id}",
        )
    return EnrichDOIResult(work=WorkOut.model_validate(work), source="semantic_scholar")
