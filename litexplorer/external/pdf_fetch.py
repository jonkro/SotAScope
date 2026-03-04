"""Open-access PDF fetching from arXiv and Unpaywall."""

from __future__ import annotations

import logging
import re

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_UA = "LitExplorer/1.0"


class PDFFetchError(Exception):
    """Raised when a PDF URL was found but the download itself failed."""


def fetch_pdf_from_arxiv(arxiv_id: str, verify: bool = True) -> bytes | None:
    """Fetch a PDF from arXiv. Returns PDF bytes or None on failure."""
    url = f"https://arxiv.org/pdf/{arxiv_id}"
    try:
        with httpx.Client(verify=verify, follow_redirects=True, timeout=60.0) as client:
            resp = client.get(url, headers={"User-Agent": _DEFAULT_UA})
    except Exception:
        logger.warning("arXiv PDF fetch failed for %s", arxiv_id, exc_info=True)
        return None
    if resp.status_code != 200:
        logger.debug("arXiv returned %s for %s", resp.status_code, arxiv_id)
        return None
    ct = resp.headers.get("content-type", "")
    if "application/pdf" not in ct:
        logger.debug("arXiv response not PDF for %s (content-type: %s)", arxiv_id, ct)
        return None
    return resp.content


def fetch_pdf_url_from_unpaywall(doi: str, email: str, verify: bool = True) -> str | None:
    """Fetch the OA PDF URL for a DOI from Unpaywall. Returns URL string or None."""
    url = f"https://api.unpaywall.org/v2/{doi}?email={email}"
    try:
        with httpx.Client(verify=verify, follow_redirects=True, timeout=30.0) as client:
            resp = client.get(url, headers={"User-Agent": _DEFAULT_UA})
    except Exception:
        logger.warning("Unpaywall fetch failed for DOI %s", doi, exc_info=True)
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    oa_loc = data.get("best_oa_location")
    if not oa_loc:
        return None
    pdf_url = oa_loc.get("url_for_pdf")
    if pdf_url:
        return pdf_url
    plain_url = oa_loc.get("url")
    if plain_url and plain_url.endswith(".pdf"):
        return plain_url
    return None


def fetch_pdf_from_url(url: str, verify: bool = True) -> bytes | None:
    """Download a PDF from an arbitrary URL. Returns bytes or None on failure."""
    try:
        with httpx.Client(verify=verify, follow_redirects=True, timeout=60.0) as client:
            resp = client.get(url, headers={"User-Agent": _DEFAULT_UA})
    except Exception:
        logger.warning("PDF URL fetch failed for %s", url, exc_info=True)
        return None
    if resp.status_code != 200:
        return None
    ct = resp.headers.get("content-type", "")
    content = resp.content
    if "application/pdf" not in ct and not content[:4] == b"%PDF":
        return None
    return content


def fetch_pdf_for_work(
    db, work, verify: bool = True, email: str = ""
) -> tuple[bytes, str] | None:
    """Try to fetch a PDF for the given work from open-access sources.

    Priority:
    1. arXiv (if work.arxiv_id is set)
    2. Unpaywall (if work.doi and email are set)

    Returns (pdf_bytes, suggested_filename) on success, None if no OA source found.
    Raises PDFFetchError if a PDF URL was resolved but the download failed.
    """
    # 1. arXiv
    if work.arxiv_id:
        pdf_bytes = fetch_pdf_from_arxiv(work.arxiv_id, verify=verify)
        if pdf_bytes is not None:
            arxiv_safe = re.sub(r"[^\w.\-]", "_", work.arxiv_id)
            return pdf_bytes, f"{arxiv_safe}.pdf"

    # 2. Unpaywall (requires DOI + contact email for polite access)
    if work.doi and email:
        pdf_url = fetch_pdf_url_from_unpaywall(work.doi, email, verify=verify)
        if pdf_url:
            pdf_bytes = fetch_pdf_from_url(pdf_url, verify=verify)
            if pdf_bytes is not None:
                doi_slug = re.sub(r"[^\w\-]", "_", work.doi)
                return pdf_bytes, f"{doi_slug}.pdf"
            raise PDFFetchError(f"PDF download failed from {pdf_url}")

    return None
