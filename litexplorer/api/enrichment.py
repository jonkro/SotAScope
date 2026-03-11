"""Enrichment API router — import works from OpenAlex and fetch citations."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
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


@router.post("/works/{work_id}/citations/backward", response_model=CitationResult)
def fetch_backward_citations(work_id: int, db: Session = Depends(get_db)):
    """Fetch and persist backward citations (references) for a work."""
    client = _get_client(db)
    try:
        svc = EnrichmentService(db=db, client=client)
        try:
            works, raw_count = svc.fetch_backward_citations(work_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
    finally:
        client.close()

    return CitationResult(
        works=[WorkOut.model_validate(w) for w in works],
        count=len(works),
        raw_count=raw_count,
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


@router.post("/works/{work_id}/semantic-scholar", response_model=SemanticScholarEnrichResult)
def enrich_from_semantic_scholar(
    work_id: int,
    direction: str = Query("both"),
    db: Session = Depends(get_db),
):
    """Fetch backward and forward citations from Semantic Scholar.

    Looks up the work by DOI (preferred) or stored semantic_scholar_id.
    Upserts returned papers into the library and merges citation edges,
    skipping duplicates.  Returns counts of new vs. already-existing edges.
    """
    ss_client = _get_ss_client(db)
    oa_client = _get_client(db)
    try:
        svc = EnrichmentService(db=db, client=oa_client)
        if direction not in ("both", "backward", "forward"):
            raise HTTPException(status_code=400, detail="direction must be 'both', 'backward', or 'forward'")
        try:
            summary = svc.enrich_from_semantic_scholar(work_id, ss_client, direction=direction)
        except ValueError as e:
            raise HTTPException(status_code=404 if "not found" in str(e).lower() else 400, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))
    finally:
        ss_client.close()
        oa_client.close()

    from litexplorer.models.library import Work as WorkModel
    work = db.get(WorkModel, work_id)
    return SemanticScholarEnrichResult(
        work=WorkOut.model_validate(work),
        **summary,
    )


@router.post("/works/{work_id}/grobid", response_model=GrobidEnrichResult)
def enrich_from_grobid(work_id: int, db: Session = Depends(get_db)):
    """Extract and resolve references from a work's primary PDF via GROBID.

    Sends the PDF to the configured GROBID instance, parses the returned
    reference list, and resolves each reference through DOI → arXiv ID →
    title-search fallback paths.  The raw GROBID extraction is cached
    permanently so re-runs skip the GROBID network call and re-attempt only
    resolution for previously unresolved references.
    """
    from litexplorer.external.grobid import GrobidError

    oa_client = _get_client(db)
    cr_client = _get_crossref_client(db)
    try:
        svc = EnrichmentService(db=db, client=oa_client, crossref_client=cr_client)
        try:
            result = svc.enrich_from_grobid(work_id)
        except ValueError as e:
            msg = str(e)
            if "no pdf" in msg.lower():
                raise HTTPException(status_code=404, detail="No PDF available for this work")
            if "not configured" in msg.lower():
                raise HTTPException(status_code=400, detail="GROBID is not configured")
            raise HTTPException(status_code=404, detail=msg)
        except GrobidError as e:
            logger.warning("GROBID service error for work %d: %s", work_id, e)
            raise HTTPException(status_code=503, detail="GROBID service is not available")
        except Exception as e:
            logger.exception("Unexpected error during GROBID enrichment for work %d", work_id)
            raise HTTPException(status_code=500, detail=str(e))
    finally:
        oa_client.close()
        cr_client.close()

    return GrobidEnrichResult(
        new_count=result.new_count,
        existing_count=result.existing_count,
        failed_count=result.failed_count,
        total_extracted=result.total_extracted,
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
