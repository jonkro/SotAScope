"""Enrichment API router — import works from OpenAlex and fetch citations."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from sotascope.api.deps import get_db
from sotascope.config import settings
from sotascope.external.crossref import CrossrefClient
from sotascope.external.openalex import OpenAlexClient
from sotascope.external.semantic_scholar import SemanticScholarClient
from sotascope.schemas.enrichment import (
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
from sotascope.schemas.works import WorkOut
from sotascope.services.enrichment import EnrichmentService, normalize_identifier
from sotascope.services.work_lock import work_lock

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/enrich", tags=["enrichment"])


# ---------------------------------------------------------------------------
# Client / settings helpers
# ---------------------------------------------------------------------------

def _get_contact_email(db: Session) -> str | None:
    """Read API contact email: DB setting first, then env var fallback."""
    from sotascope.api.settings import get_setting_value

    email = get_setting_value(db, "api_contact_email")
    if email:
        return email
    # Env var fallback — either variable works
    return settings.openalex_api_key or settings.crossref_mailto or None


def _get_ssl_verify(db: Session) -> bool:
    """Read ssl_verify setting from DB. Defaults to True (SSL verification enabled)."""
    from sotascope.api.settings import get_setting_value

    val = get_setting_value(db, "ssl_verify")
    if val is None:
        return True
    return val.lower() != "false"


def _get_s2_api_key(db: Session) -> str | None:
    """Read Semantic Scholar API key from DB setting."""
    from sotascope.api.settings import get_setting_value

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
    from sotascope.api.settings import get_setting_value

    return get_setting_value(db, "grobid_url") or ""


# ---------------------------------------------------------------------------
# Background task functions (each owns its own DB session + releases lock)
# ---------------------------------------------------------------------------

def _fetch_backward_citations_bg(work_id: int) -> None:
    """Background: fetch backward citations (OpenAlex) and release work lock."""
    from sotascope.database import SessionLocal

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
    from sotascope.database import SessionLocal

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
    from sotascope.database import SessionLocal

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
    from sotascope.database import SessionLocal

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
    from sotascope.database import SessionLocal
    from sotascope.external.grobid import GrobidError

    db = SessionLocal()
    try:
        oa_client = _get_client(db)
        cr_client = _get_crossref_client(db)
        ss_client = _get_ss_client(db)
        try:
            svc = EnrichmentService(db=db, client=oa_client, crossref_client=cr_client)
            svc.enrich_from_grobid(work_id, ss_client=ss_client)
        except ValueError as e:
            logger.warning("GROBID enrichment validation error for work %d: %s", work_id, e)
        except GrobidError as e:
            logger.warning("GROBID service error for work %d: %s", work_id, e)
        except Exception:
            logger.exception("Unexpected error in background GROBID enrichment for work %d", work_id)
        finally:
            oa_client.close()
            cr_client.close()
            ss_client.close()
    finally:
        work_lock.release(work_id)
        db.close()


# ---------------------------------------------------------------------------
# Bulk enrichment background functions (S2 and GROBID)
# ---------------------------------------------------------------------------

def _bulk_s2_bg(job_id: str, work_ids: list[int], direction: str) -> None:
    """Background: fetch S2 refs/cites for a list of works, updating job status."""
    from sotascope.database import SessionLocal
    from sotascope.models.cache import ApiCache
    from sotascope.services.bulk_enrich_jobs import get_job
    from sqlalchemy import select as sa_select

    job = get_job(job_id)
    if job is None:
        return

    db = SessionLocal()
    try:
        ss_client = _get_ss_client(db)
        oa_client = _get_client(db)
        try:
            svc = EnrichmentService(db=db, client=oa_client)

            # Pre-query which works already have S2 enrichment cached
            all_ref_keys = [f"s2_enrich_refs:{wid}" for wid in work_ids]
            all_cite_keys = [f"s2_enrich_citing:{wid}" for wid in work_ids]
            refs_done: set[int] = set()
            citing_done: set[int] = set()
            for qk in db.execute(
                sa_select(ApiCache.query_key).where(
                    ApiCache.source == "semantic_scholar",
                    ApiCache.query_key.in_(all_ref_keys),
                )
            ).scalars().all():
                try:
                    refs_done.add(int(qk.split(":")[-1]))
                except ValueError:
                    pass
            for qk in db.execute(
                sa_select(ApiCache.query_key).where(
                    ApiCache.source == "semantic_scholar",
                    ApiCache.query_key.in_(all_cite_keys),
                )
            ).scalars().all():
                try:
                    citing_done.add(int(qk.split(":")[-1]))
                except ValueError:
                    pass

            for i, work_id in enumerate(work_ids):
                if job.cancel_requested:
                    job.status = "cancelled"
                    break

                # Determine effective direction, skipping already-fetched
                rd = work_id in refs_done
                cd = work_id in citing_done
                if direction == "both" and rd and cd:
                    job.done = i + 1
                    continue
                elif direction == "backward" and rd:
                    job.done = i + 1
                    continue
                elif direction == "forward" and cd:
                    job.done = i + 1
                    continue

                eff_direction = direction
                if direction == "both":
                    if rd:
                        eff_direction = "forward"
                    elif cd:
                        eff_direction = "backward"

                rate_limited = False
                locked = work_lock.acquire(work_id, "Bulk Semantic Scholar fetch")
                try:
                    if locked:
                        svc.enrich_from_semantic_scholar(
                            work_id, ss_client, direction=eff_direction
                        )
                        if eff_direction in ("both", "backward"):
                            refs_done.add(work_id)
                        if eff_direction in ("both", "forward"):
                            citing_done.add(work_id)
                    else:
                        job.errors.append(f"work {work_id}: already locked, skipped")
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 429:
                        rate_limited = True
                    else:
                        job.errors.append(
                            f"work {work_id}: HTTP {exc.response.status_code}"
                        )
                except (ValueError, RuntimeError) as exc:
                    logger.warning("Bulk S2: work %d skipped: %s", work_id, exc)
                except Exception:
                    logger.exception("Bulk S2: unexpected error for work %d", work_id)
                    job.errors.append(f"work {work_id}: unexpected error")
                finally:
                    if locked:
                        work_lock.release(work_id)

                if rate_limited:
                    job.status = "rate_limited"
                    job.rate_limited_at = i  # seeds completed before rate limit
                    break

                job.done = i + 1
            else:
                if job.status == "running":
                    job.status = "completed"
        finally:
            ss_client.close()
            oa_client.close()
    finally:
        db.close()


def _bulk_grobid_bg(job_id: str, work_ids: list[int]) -> None:
    """Background: run GROBID reference extraction for a list of works."""
    from sotascope.database import SessionLocal
    from sotascope.models.cache import ApiCache
    from sotascope.external.grobid import GrobidError
    from sotascope.services.bulk_enrich_jobs import get_job
    from sqlalchemy import select as sa_select

    job = get_job(job_id)
    if job is None:
        return

    db = SessionLocal()
    try:
        oa_client = _get_client(db)
        cr_client = _get_crossref_client(db)
        ss_client = _get_ss_client(db)
        try:
            svc = EnrichmentService(db=db, client=oa_client, crossref_client=cr_client)

            # Pre-query which works have already been GROBID-processed
            all_grobid_keys = [f"grobid_references:{wid}" for wid in work_ids]
            grobid_done: set[int] = set()
            for qk in db.execute(
                sa_select(ApiCache.query_key).where(
                    ApiCache.source == "grobid",
                    ApiCache.query_key.in_(all_grobid_keys),
                )
            ).scalars().all():
                try:
                    grobid_done.add(int(qk.split(":")[-1]))
                except ValueError:
                    pass

            for i, work_id in enumerate(work_ids):
                if job.cancel_requested:
                    job.status = "cancelled"
                    break

                if work_id in grobid_done:
                    job.done = i + 1
                    continue

                locked = work_lock.acquire(work_id, "Bulk GROBID extraction")
                try:
                    if locked:
                        svc.enrich_from_grobid(work_id, ss_client=ss_client)
                        grobid_done.add(work_id)
                    else:
                        job.errors.append(f"work {work_id}: already locked, skipped")
                except ValueError as exc:
                    logger.warning("Bulk GROBID: work %d skipped: %s", work_id, exc)
                except GrobidError as exc:
                    logger.warning("Bulk GROBID: service error for work %d: %s", work_id, exc)
                    job.errors.append(f"work {work_id}: GROBID error")
                except Exception:
                    logger.exception("Bulk GROBID: unexpected error for work %d", work_id)
                    job.errors.append(f"work {work_id}: unexpected error")
                finally:
                    if locked:
                        work_lock.release(work_id)

                job.done = i + 1
            else:
                if job.status == "running":
                    job.status = "completed"
        finally:
            oa_client.close()
            cr_client.close()
            ss_client.close()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/doi", response_model=EnrichDOIResult)
def enrich_by_doi(body: EnrichDOIRequest, db: Session = Depends(get_db)):
    """Import a work by DOI or arXiv ID from OpenAlex (with appropriate fallback).

    Detection: strings starting with "10." are treated as DOIs; everything else
    (modern arXiv IDs like "2301.12345" or old-style like "hep-th/0601001") is
    treated as an arXiv ID.
    """
    identifier = normalize_identifier(body.doi)

    if identifier.startswith("10."):
        # ---- DOI path (OpenAlex → Crossref fallback) ----
        client = _get_client(db)
        cr_client = _get_crossref_client(db)
        try:
            svc = EnrichmentService(db=db, client=client, crossref_client=cr_client)
            work = svc.import_by_doi(identifier)
        finally:
            client.close()
            cr_client.close()

        if work is None:
            raise HTTPException(status_code=404, detail=f"DOI not found: {identifier}")
        return EnrichDOIResult(work=WorkOut.model_validate(work), identifier_type="doi")

    else:
        # ---- arXiv ID path (OpenAlex → Semantic Scholar fallback) ----
        import httpx as _httpx
        client = _get_client(db)
        ss_client = _get_ss_client(db)
        try:
            svc = EnrichmentService(db=db, client=client)
            work = svc.import_by_arxiv_id(identifier, ss_client=ss_client)
        except _httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise HTTPException(
                    status_code=429,
                    detail="Semantic Scholar rate limit reached — try again later or add an S2 API key in Settings.",
                )
            raise
        finally:
            client.close()
            ss_client.close()

        if work is None:
            raise HTTPException(status_code=404, detail=f"arXiv ID not found: {identifier}")
        return EnrichDOIResult(work=WorkOut.model_validate(work), identifier_type="arxiv")


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

    from sotascope.external.openalex import parse_work as _parse_oa
    from sotascope.models.cache import ApiCache

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
    from sotascope.models.library import Work as WorkModel

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
    from sotascope.models.library import Work as WorkModel

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
    from sotascope.models.library import Work as WorkModel

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
    from sotascope.models.library import Work as WorkModel

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
    from sotascope.models.library import Work as WorkModel

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
    from sotascope.models.library import Work as WorkModel, WorkPDF as WorkPDFModel

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


# ---------------------------------------------------------------------------
# Bulk enrichment endpoints
# ---------------------------------------------------------------------------

from pydantic import BaseModel as _BaseModel


class _BulkS2Request(_BaseModel):
    work_ids: list[int]
    direction: str = "both"


class _BulkGrobidRequest(_BaseModel):
    work_ids: list[int]


@router.post("/bulk/semantic-scholar")
def bulk_enrich_semantic_scholar(
    body: _BulkS2Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Schedule bulk Semantic Scholar enrichment for multiple works.

    Returns a job_id that can be polled via GET /jobs/bulk/{job_id}.
    Rate-limits at most 1 request per 1.1 s (shared process-level limiter).
    If S2 returns HTTP 429, the job stops and records the number of seeds
    completed before the rate limit hit.
    """
    from sotascope.services.bulk_enrich_jobs import create_job

    if body.direction not in ("both", "backward", "forward"):
        raise HTTPException(
            status_code=400, detail="direction must be 'both', 'backward', or 'forward'"
        )
    if not body.work_ids:
        raise HTTPException(status_code=400, detail="work_ids must not be empty")

    job = create_job("semantic_scholar", body.work_ids)
    background_tasks.add_task(_bulk_s2_bg, job.job_id, body.work_ids, body.direction)
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job.job_id,
            "message": f"Bulk S2 fetch started for {len(body.work_ids)} seeds",
        },
    )


@router.post("/bulk/grobid")
def bulk_enrich_grobid(
    body: _BulkGrobidRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Schedule bulk GROBID reference extraction for multiple works.

    Only works with uploaded PDFs will be processed; others are skipped.
    Returns a job_id for polling via GET /jobs/bulk/{job_id}.
    """
    from sotascope.services.bulk_enrich_jobs import create_job

    grobid_url = _get_grobid_url_setting(db)
    if not grobid_url:
        raise HTTPException(status_code=400, detail="GROBID is not configured")
    if not body.work_ids:
        raise HTTPException(status_code=400, detail="work_ids must not be empty")

    job = create_job("grobid", body.work_ids)
    background_tasks.add_task(_bulk_grobid_bg, job.job_id, body.work_ids)
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job.job_id,
            "message": f"Bulk GROBID extraction started for {len(body.work_ids)} seeds",
        },
    )


@router.get("/jobs/bulk/{job_id}")
def get_bulk_job_status(job_id: str):
    """Poll status of a bulk enrichment job (S2 or GROBID)."""
    from sotascope.services.bulk_enrich_jobs import get_job

    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return JSONResponse(content=job.to_dict())


@router.delete("/jobs/bulk/{job_id}")
def cancel_bulk_job(job_id: str):
    """Request cancellation of a running bulk enrichment job."""
    from sotascope.services.bulk_enrich_jobs import cancel_job

    ok = cancel_job(job_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"Job {job_id!r} not found or already finished",
        )
    return {"message": "Cancellation requested"}
