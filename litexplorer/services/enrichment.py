"""Enrichment service — imports works from external sources into the local DB.

Handles cache-first fetching, deduplication, venue alias auto-creation,
and stub work creation for citation neighbors.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from litexplorer.config import settings
from litexplorer.external.base import ExternalWork
from litexplorer.external.crossref import CrossrefClient, parse_crossref_work
from litexplorer.external.openalex import OpenAlexClient, parse_work
from litexplorer.models.cache import ApiCache
from litexplorer.models.library import (
    Author,
    Citation,
    Venue,
    VenueAlias,
    Work,
    WorkAuthor,
    WorkDOI,
    WorkLocation,
    WorkPDF,
)
from litexplorer.models.settings import Setting
from litexplorer.schemas.enrichment import DOICandidate, DOIResolutionResult, SearchImportCandidate

logger = logging.getLogger(__name__)

# Regex patterns for arXiv ID normalization
_ARXIV_PREFIX_RE = re.compile(r'^arxiv:', re.IGNORECASE)
_ARXIV_VERSION_RE = re.compile(r'v\d+$', re.IGNORECASE)

# Regex patterns for venue name normalization
_ORDINAL_RE = re.compile(r'\b\d{1,3}(?:st|nd|rd|th)\b', re.IGNORECASE)
_YEAR_RE = re.compile(r'\b(?:19|20)\d{2}\b')
_PROCEEDINGS_PREFIX_RE = re.compile(
    r'^proceedings\s+of\s+the\s+|^proceedings\s+of\s+|^proceedings\s+on\s+|^proceedings\s+',
    re.IGNORECASE,
)


def _normalize_title_for_cmp(title: str) -> str:
    """Normalize a paper title for fuzzy comparison.

    Lowercases, decomposes unicode (handles fancy quotes, dashes, accented
    chars), replaces all non-alphanumeric characters with spaces, and
    collapses whitespace.  This makes comparisons robust against typesetting
    differences like em-dash vs hyphen, curly vs straight apostrophes, etc.
    """
    # NFKD decomposes ligatures, fancy quotes, etc.
    title = unicodedata.normalize("NFKD", title).lower()
    title = re.sub(r"[^a-z0-9]", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def normalize_identifier(identifier: str) -> str:
    """Normalize a DOI or arXiv ID input string.

    - Strips whitespace.
    - Removes a leading "arXiv:" prefix (case-insensitive), e.g.
      "arXiv:2204.05862" → "2204.05862".
    - Removes a trailing version suffix, e.g. "2402.03300v3" → "2402.03300".

    DOIs (starting with "10.") pass through unchanged in practice because they
    don't carry arXiv prefixes or version suffixes.
    """
    identifier = identifier.strip()
    identifier = _ARXIV_PREFIX_RE.sub('', identifier)
    identifier = _ARXIV_VERSION_RE.sub('', identifier)
    return identifier


def normalize_venue_name(name: str) -> str:
    """Normalize a venue name by removing proceedings prefixes, calendar years,
    and ordinal edition numbers so that the same conference across years maps
    to a single canonical venue.

    Examples:
        "Proceedings 2023 Network and Distributed System Security Symposium"
        → "Network and Distributed System Security Symposium"

        "Proceedings of the 17th International Conference on Availability, Reliability and Security"
        → "International Conference on Availability, Reliability and Security"
    """
    s = _PROCEEDINGS_PREFIX_RE.sub('', name)
    s = _YEAR_RE.sub('', s)
    s = _ORDINAL_RE.sub('', s)
    # Collapse multiple spaces and strip
    s = re.sub(r'\s{2,}', ' ', s).strip()
    # Remove leading/trailing punctuation artifacts (e.g. leftover commas)
    s = s.strip(' ,.-')
    return s


def _extract_first_author_from_bibtex(bibtex_entry: str) -> str | None:
    """Extract the first author name from a raw BibTeX entry string."""
    m = re.search(r'author\s*=\s*\{([^}]+)\}', bibtex_entry, re.IGNORECASE)
    if not m:
        return None
    authors_str = m.group(1).strip()
    # Split on " and " (BibTeX convention)
    first = authors_str.split(" and ")[0].strip()
    if not first:
        return None
    # Handle "Last, First" format → return "Last"
    if "," in first:
        return first.split(",")[0].strip()
    # "First Last" format → return last word
    parts = first.split()
    return parts[-1] if parts else None


@dataclass
class GrobidEnrichResult:
    """Summary of a GROBID reference extraction and resolution run."""

    new_count: int       # References resolved to works newly imported into the library
    existing_count: int  # References resolved to works already in the library
    failed_count: int    # References that could not be resolved to any work
    total_extracted: int  # Total references GROBID extracted from the PDF


def _jaccard_similarity(a: str, b: str) -> float:
    """Jaccard similarity between the word sets of two already-normalized strings."""
    sa = set(a.split())
    sb = set(b.split())
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _grobid_ref_to_dict(ref) -> dict:
    """Serialize a GrobidReference to a plain dict for JSON caching."""
    return {
        "title": ref.title,
        "authors": ref.authors,
        "doi": ref.doi,
        "arxiv_id": ref.arxiv_id,
        "journal": ref.journal,
        "volume": ref.volume,
        "pages": ref.pages,
        "year": ref.year,
        "raw_string": ref.raw_string,
    }


def _dict_to_grobid_ref(d: dict):
    """Deserialize a plain dict back to a GrobidReference."""
    from litexplorer.external.grobid import GrobidReference

    return GrobidReference(
        title=d.get("title"),
        authors=d.get("authors") or [],
        doi=d.get("doi"),
        arxiv_id=d.get("arxiv_id"),
        journal=d.get("journal"),
        volume=d.get("volume"),
        pages=d.get("pages"),
        year=d.get("year"),
        raw_string=d.get("raw_string"),
    )


class EnrichmentService:
    """Orchestrates importing and enriching works from OpenAlex and Crossref."""

    def __init__(
        self,
        db: Session,
        client: OpenAlexClient,
        crossref_client: CrossrefClient | None = None,
    ):
        self.db = db
        self.client = client
        self.crossref_client = crossref_client

    # -- Public API -----------------------------------------------------------

    def import_by_doi(self, doi: str) -> Work | None:
        """Import a work by DOI. Returns the persisted Work or None if not found.

        Cache-first: checks ApiCache before calling the API.
        Dedup: checks for existing Work by DOI/openalex_id/arxiv_id before creating.
        """
        doi = doi.lower().strip()

        # Check cache first
        cache_key = f"work:doi:{doi}"
        cached = self._get_cache("openalex", cache_key)
        if cached is not None:
            raw = json.loads(cached.response_json)
            ext_work = parse_work(raw)
            return self._upsert_work(ext_work)

        # Call OpenAlex API
        raw = self.client.get_work_by_doi_raw(doi)
        if raw is not None:
            self._set_cache("openalex", cache_key, json.dumps(raw), "permanent")
            ext_work = parse_work(raw)
            return self._upsert_work(ext_work)

        # Crossref fallback
        if self.crossref_client is None:
            return None

        cr_cache_key = f"work:doi:{doi}"
        cr_cached = self._get_cache("crossref", cr_cache_key)
        if cr_cached is not None:
            cr_raw = json.loads(cr_cached.response_json)
            ext_work = parse_crossref_work(cr_raw)
            return self._upsert_work(ext_work)

        cr_raw = self.crossref_client.get_work_by_doi_raw(doi)
        if cr_raw is None:
            return None

        self._set_cache("crossref", cr_cache_key, json.dumps(cr_raw), "permanent")
        ext_work = parse_crossref_work(cr_raw)
        return self._upsert_work(ext_work)

    def import_by_arxiv_id(self, arxiv_id: str, ss_client=None) -> Work | None:
        """Import a work by arXiv ID. OpenAlex first, then Semantic Scholar fallback.

        Resolution order:
        1. Dedup: return existing Work if arxiv_id is already in the library.
        2. Cache: return from ApiCache if a previous OA response was cached.
        3. OpenAlex: GET /works/arxiv:{arxiv_id}
        4. Semantic Scholar fallback (if ss_client provided): GET /paper/ARXIV:{arxiv_id}

        Returns the persisted Work, or None if all sources fail.
        doi, openalex_id, arxiv_id, and semantic_scholar_id are populated from
        whichever source responded.
        """
        arxiv_id = normalize_identifier(arxiv_id)

        # 1. Dedup — avoid any API call if we already have this paper
        existing = self.db.execute(
            select(Work).where(Work.arxiv_id == arxiv_id)
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        # 2. OA cache
        oa_cache_key = f"work:arxiv:{arxiv_id}"
        cached = self._get_cache("openalex", oa_cache_key)
        if cached is not None:
            raw = json.loads(cached.response_json)
            ext_work = parse_work(raw)
            return self._upsert_work(ext_work)

        # 3. OpenAlex API
        raw = self.client.get_work_by_arxiv_id_raw(arxiv_id)
        if raw is not None:
            self._set_cache("openalex", oa_cache_key, json.dumps(raw), "permanent")
            ext_work = parse_work(raw)
            return self._upsert_work(ext_work)

        # 4. Semantic Scholar fallback
        if ss_client is None:
            return None
        ss_paper = ss_client.get_paper(f"ARXIV:{arxiv_id}")
        if ss_paper is None:
            return None
        return self._upsert_work(ss_paper)

    def fetch_backward_citations(self, work_id: int) -> tuple[list[Work], int]:
        """Fetch and persist backward citations (references) for a work.

        Reads referenced_work_ids from the cached work data, fetches metadata
        for each referenced work, creates stub Work records, and creates
        Citation edges.

        Returns a tuple of (works, raw_count) where raw_count is the number
        of full work records returned by OpenAlex.  raw_count == 0 means OA
        has no reference list for this paper (not merely that all refs were
        already in the library).
        """
        work = self.db.get(Work, work_id)
        if work is None:
            raise ValueError(f"Work {work_id} not found")
        if not work.openalex_id:
            raise ValueError(f"Work {work_id} has no OpenAlex ID — cannot fetch citations")

        cache_key = f"backward_citations:{work.openalex_id}"
        cached = self._get_cache("openalex", cache_key)

        if cached is not None:
            raw_list = json.loads(cached.response_json)
        else:
            # Get the work's referenced_work_ids from the original cached response
            ref_ids = self._get_referenced_work_ids(work)
            if not ref_ids:
                # Cache empty result and commit so it persists
                self._set_cache("openalex", cache_key, "[]", "permanent")
                self.db.commit()
                return [], 0

            raw_list = self.client.get_works_by_ids_raw(ref_ids)
            self._set_cache("openalex", cache_key, json.dumps(raw_list), "permanent")

        raw_count = len(raw_list)

        # Parse and persist each referenced work
        results: list[Work] = []
        for raw in raw_list:
            ext_work = parse_work(raw)
            db_work = self._upsert_work(ext_work)
            results.append(db_work)

            # Create citation edge: work (the seed) cites db_work (the reference)
            self._ensure_citation(citing_work_id=work.id, cited_work_id=db_work.id)

        self.db.commit()
        return results, raw_count

    def fetch_forward_citations(
        self, work_id: int, force_refresh: bool = False
    ) -> list[Work]:
        """Fetch and persist forward citations (papers citing this work).

        Uses cursor pagination. Cache is timestamped (can be refreshed).
        """
        work = self.db.get(Work, work_id)
        if work is None:
            raise ValueError(f"Work {work_id} not found")
        if not work.openalex_id:
            raise ValueError(f"Work {work_id} has no OpenAlex ID — cannot fetch citations")

        cache_key = f"forward_citations:{work.openalex_id}"

        if not force_refresh:
            cached = self._get_cache("openalex", cache_key)
            if cached is not None:
                raw_list = json.loads(cached.response_json)
                return self._persist_forward_citations(work, raw_list)

        # Fetch from API
        raw_list = self.client.get_forward_citations_raw(work.openalex_id)
        self._set_cache("openalex", cache_key, json.dumps(raw_list), "timestamped")

        return self._persist_forward_citations(work, raw_list)

    def enrich_from_crossref(self, work_id: int) -> Work:
        """Enrich a work's venue metadata (ISSN, publisher) from Crossref.

        Requires self.crossref_client to be set. Caches permanently.
        Returns the updated Work.
        Raises ValueError if work not found or has no DOI.
        Raises RuntimeError if crossref_client is not configured.
        """
        if self.crossref_client is None:
            raise RuntimeError("Crossref client not configured")

        work = self.db.get(Work, work_id)
        if work is None:
            raise ValueError(f"Work {work_id} not found")
        if not work.doi:
            raise ValueError(f"Work {work_id} has no DOI — cannot enrich from Crossref")

        cache_key = f"work:doi:{work.doi}"
        cached = self._get_cache("crossref", cache_key)

        if cached is not None:
            cr_raw = json.loads(cached.response_json)
        else:
            cr_raw = self.crossref_client.get_work_by_doi_raw(work.doi)
            if cr_raw is None:
                raise ValueError(f"DOI not found in Crossref: {work.doi}")
            self._set_cache("crossref", cache_key, json.dumps(cr_raw), "permanent")

        ext_work = parse_crossref_work(cr_raw)

        # Update venue with ISSN/publisher if the venue has them
        if ext_work.venue and work.venue_id:
            venue = self.db.get(Venue, work.venue_id)
            if venue:
                if venue.issn is None and ext_work.venue.issn:
                    venue.issn = ext_work.venue.issn
                if venue.publisher is None and ext_work.venue.publisher:
                    venue.publisher = ext_work.venue.publisher
        elif ext_work.venue and not work.venue_id:
            # Work has no venue yet — resolve one from Crossref data
            venue = self._resolve_venue(ext_work)
            if venue:
                work.venue_id = venue.id

        self.db.commit()
        return work

    def resolve_doi_for_work(self, work_id: int) -> DOIResolutionResult:
        """Attempt to resolve a DOI for a work via Crossref fuzzy search.

        Returns auto_resolved_doi if confidence is high, otherwise candidates.
        """
        if self.crossref_client is None:
            raise RuntimeError("Crossref client not configured")

        work = self.db.get(Work, work_id)
        if work is None:
            raise ValueError(f"Work {work_id} not found")
        if work.doi:
            return DOIResolutionResult(work_id=work_id, auto_resolved_doi=work.doi)

        query = self._build_bibliographic_query(work)
        if not query:
            return DOIResolutionResult(work_id=work_id)

        # Check cache
        cache_key = f"doi_resolve:{query}"
        cached = self._get_cache("crossref", cache_key)
        if cached is not None:
            items = json.loads(cached.response_json)
        else:
            items = self.crossref_client.search_works(query)
            self._set_cache("crossref", cache_key, json.dumps(items), "permanent")
            self.db.flush()

        if not items:
            return DOIResolutionResult(work_id=work_id)

        # Build candidates
        candidates: list[DOICandidate] = []
        for item in items:
            doi = item.get("DOI", "").lower()
            if not doi:
                continue
            titles = item.get("title") or []
            title = titles[0] if titles else "(untitled)"
            authors_list: list[str] = []
            for a in item.get("author") or []:
                given = a.get("given", "")
                family = a.get("family", "")
                authors_list.append(f"{given} {family}".strip())
            pub_year = None
            issued = item.get("issued") or {}
            date_parts = issued.get("date-parts") or []
            if date_parts and date_parts[0] and date_parts[0][0]:
                pub_year = date_parts[0][0]
            container = item.get("container-title") or []
            venue = container[0] if container else None
            score = item.get("score", 0)
            candidates.append(DOICandidate(
                doi=doi, title=title, authors=authors_list,
                publication_year=pub_year, venue=venue, score=score,
            ))

        if not candidates:
            return DOIResolutionResult(work_id=work_id)

        # Evaluate confidence
        top = candidates[0]
        second_score = candidates[1].score if len(candidates) > 1 else 0
        ratio = top.score / second_score if second_score > 0 else float("inf")

        if (
            top.score >= settings.crossref_resolve_score_threshold
            and ratio >= settings.crossref_resolve_ratio_threshold
        ):
            # Check DOI not already used
            existing = self.db.execute(
                select(Work).where(Work.doi == top.doi)
            ).scalar_one_or_none()
            if existing is None:
                work.doi = top.doi
                work.doi_auto_resolved = True
                self.db.commit()
                return DOIResolutionResult(work_id=work_id, auto_resolved_doi=top.doi)

        # Return candidates for user confirmation
        return DOIResolutionResult(work_id=work_id, candidates=candidates)

    def confirm_doi_resolution(self, work_id: int, doi: str) -> Work:
        """Confirm a DOI resolution for a work."""
        work = self.db.get(Work, work_id)
        if work is None:
            raise ValueError(f"Work {work_id} not found")

        doi = doi.lower().strip()

        # Check uniqueness
        existing = self.db.execute(
            select(Work).where(Work.doi == doi, Work.id != work_id)
        ).scalar_one_or_none()
        if existing:
            raise ValueError(f"DOI {doi} already assigned to work {existing.id}")

        work.doi = doi
        work.doi_auto_resolved = True
        self.db.commit()
        return work

    @staticmethod
    def _build_bibliographic_query(work: Work) -> str:
        """Build a Crossref bibliographic query from work metadata."""
        parts: list[str] = []
        if work.title and work.title != "(untitled)":
            parts.append(work.title)

        # Try to get first author from WorkAuthor relationship
        if work.authors:
            first_wa = min(work.authors, key=lambda wa: wa.position)
            parts.append(first_wa.author.name)
        elif work.bibtex_entry:
            author_name = _extract_first_author_from_bibtex(work.bibtex_entry)
            if author_name:
                parts.append(author_name)

        if work.publication_year:
            parts.append(str(work.publication_year))

        return " ".join(parts)

    # -- Internal helpers -----------------------------------------------------

    def _persist_forward_citations(self, work: Work, raw_list: list[dict]) -> list[Work]:
        """Parse raw forward citation data, persist works and citation edges."""
        results: list[Work] = []
        for raw in raw_list:
            ext_work = parse_work(raw)
            db_work = self._upsert_work(ext_work)
            results.append(db_work)
            # Citation edge: db_work (the citer) cites work (the seed)
            self._ensure_citation(citing_work_id=db_work.id, cited_work_id=work.id)

        # Refresh the seed work's own metadata (citation_count, citations_by_year)
        self._refresh_work_metadata(work)

        self.db.commit()
        return results

    def _refresh_work_metadata(self, work: Work) -> None:
        """Re-fetch and update a work's metadata from OpenAlex."""
        if not work.openalex_id:
            return
        cache_key = f"work:openalex:{work.openalex_id}"
        cached = self._get_cache("openalex", cache_key)
        if cached:
            raw = json.loads(cached.response_json)
            ext = parse_work(raw)
            self._update_work(work, ext)
            return
        raw = self.client.get_work_by_id_raw(work.openalex_id)
        if raw and isinstance(raw, dict):
            self._set_cache("openalex", cache_key, json.dumps(raw), "permanent")
            ext = parse_work(raw)
            self._update_work(work, ext)

    def _get_referenced_work_ids(self, work: Work) -> list[str]:
        """Get the referenced_work_ids for a work from its cached API response.

        Tries multiple cache keys (DOI, then OpenAlex ID). If no cache is
        found and the work has an OpenAlex ID, fetches the work from the API
        directly and caches the result.
        """
        # Try cached work data by DOI
        if work.doi:
            cache_key = f"work:doi:{work.doi}"
            cached = self._get_cache("openalex", cache_key)
            if cached:
                return self._extract_ref_ids(json.loads(cached.response_json))

        # Try cached work data by OpenAlex ID
        if work.openalex_id:
            cache_key = f"work:openalex:{work.openalex_id}"
            cached = self._get_cache("openalex", cache_key)
            if cached:
                return self._extract_ref_ids(json.loads(cached.response_json))

            # No cache found — fetch from API by OpenAlex ID
            raw = self.client.get_work_by_id_raw(work.openalex_id)
            if raw:
                self._set_cache("openalex", cache_key, json.dumps(raw), "permanent")
                # Also update the work with any new metadata from the fresh fetch
                ext_work = parse_work(raw)
                self._update_work(work, ext_work)
                return self._extract_ref_ids(raw)

        return []

    @staticmethod
    def _extract_ref_ids(raw: dict) -> list[str]:
        """Extract OpenAlex IDs from the referenced_works field."""
        ref_urls = raw.get("referenced_works") or []
        return [
            url.removeprefix("https://openalex.org/")
            for url in ref_urls
            if url
        ]

    def _upsert_work(self, ext: ExternalWork) -> Work:
        """Find or create a Work from an ExternalWork. Update-without-overwrite."""
        existing = self._find_existing_work(ext)
        if existing:
            return self._update_work(existing, ext)
        return self._create_work(ext)

    def _find_existing_work(self, ext: ExternalWork) -> Work | None:
        """Deduplication cascade: DOI → openalex_id → arxiv_id → semantic_scholar_id."""
        if ext.doi:
            work = self.db.execute(
                select(Work).where(Work.doi == ext.doi)
            ).scalar_one_or_none()
            if work:
                return work
        if ext.external_id:
            work = self.db.execute(
                select(Work).where(Work.openalex_id == ext.external_id)
            ).scalar_one_or_none()
            if work:
                return work
        if ext.arxiv_id:
            work = self.db.execute(
                select(Work).where(Work.arxiv_id == ext.arxiv_id)
            ).scalar_one_or_none()
            if work:
                return work
        if ext.semantic_scholar_id:
            work = self.db.execute(
                select(Work).where(Work.semantic_scholar_id == ext.semantic_scholar_id)
            ).scalar_one_or_none()
            if work:
                return work
        return None

    def _create_work(self, ext: ExternalWork) -> Work:
        """Create a new Work record from an ExternalWork."""
        venue = self._resolve_venue(ext) if ext.venue else None

        work = Work(
            doi=ext.doi,
            arxiv_id=ext.arxiv_id,
            openalex_id=ext.external_id,
            semantic_scholar_id=ext.semantic_scholar_id,
            title=ext.title,
            abstract=ext.abstract,
            publication_year=ext.publication_year,
            citation_count=ext.citation_count,
            citations_by_year=ext.citations_by_year,
            venue_id=venue.id if venue else None,
        )
        self.db.add(work)
        self.db.flush()  # Get work.id

        # Authors (deduplicate by author_id — OpenAlex sometimes lists the same author twice)
        seen_author_ids: set[int] = set()
        for i, ext_author in enumerate(ext.authors):
            author = self._resolve_author(ext_author)
            if author.id in seen_author_ids:
                continue
            seen_author_ids.add(author.id)
            wa = WorkAuthor(work_id=work.id, author_id=author.id, position=i)
            self.db.add(wa)

        # Locations
        for ext_loc in ext.locations:
            loc = WorkLocation(
                work_id=work.id,
                location_type=ext_loc.location_type,
                url=ext_loc.url,
                is_primary=ext_loc.is_primary,
            )
            self.db.add(loc)

        self.db.commit()
        return work

    def _update_work(self, work: Work, ext: ExternalWork) -> Work:
        """Update-without-overwrite: only fill None fields. Always update citation_count."""
        if work.doi is None and ext.doi:
            work.doi = ext.doi
        if work.arxiv_id is None and ext.arxiv_id:
            work.arxiv_id = ext.arxiv_id
        if work.openalex_id is None and ext.external_id:
            work.openalex_id = ext.external_id
        if work.semantic_scholar_id is None and ext.semantic_scholar_id:
            work.semantic_scholar_id = ext.semantic_scholar_id
        if work.abstract is None and ext.abstract:
            work.abstract = ext.abstract
        if work.publication_year is None and ext.publication_year:
            work.publication_year = ext.publication_year
        if work.title == "(untitled)" and ext.title and ext.title != "(untitled)":
            work.title = ext.title

        # Always update citation count and per-year breakdown (they change over time)
        if ext.citation_count is not None:
            work.citation_count = ext.citation_count
        if ext.citations_by_year is not None:
            work.citations_by_year = ext.citations_by_year

        # Venue: fill if missing
        if work.venue_id is None and ext.venue:
            venue = self._resolve_venue(ext)
            if venue:
                work.venue_id = venue.id

        self.db.commit()
        return work

    def _resolve_venue(self, ext: ExternalWork) -> Venue | None:
        """Find or create a Venue, handling alias auto-creation and ISSN matching.

        Venue names are normalized (strip "Proceedings..." prefixes, calendar
        years, ordinal edition numbers) so the same conference across years
        maps to a single canonical venue.
        """
        if not ext.venue:
            return None

        raw_name = ext.venue.name
        canonical_name = normalize_venue_name(raw_name)

        # Try by ISSN first (stable identifier from Crossref)
        if ext.venue.issn:
            venue = self.db.execute(
                select(Venue).where(Venue.issn == ext.venue.issn)
            ).scalar_one_or_none()
            if venue:
                if venue.name != raw_name:
                    self._ensure_venue_alias(venue, raw_name)
                if venue.publisher is None and ext.venue.publisher:
                    venue.publisher = ext.venue.publisher
                return venue

        # Try by openalex_id
        if ext.venue.external_id:
            venue = self.db.execute(
                select(Venue).where(Venue.openalex_id == ext.venue.external_id)
            ).scalar_one_or_none()
            if venue:
                if venue.name != raw_name:
                    self._ensure_venue_alias(venue, raw_name)
                return venue

        # Try by exact raw name
        venue = self.db.execute(
            select(Venue).where(Venue.name == raw_name)
        ).scalar_one_or_none()
        if venue:
            if venue.openalex_id is None and ext.venue.external_id:
                venue.openalex_id = ext.venue.external_id
            return venue

        # Try by normalized canonical name
        if canonical_name != raw_name:
            venue = self.db.execute(
                select(Venue).where(Venue.name == canonical_name)
            ).scalar_one_or_none()
            if venue:
                self._ensure_venue_alias(venue, raw_name)
                if venue.openalex_id is None and ext.venue.external_id:
                    venue.openalex_id = ext.venue.external_id
                return venue

        # Check aliases (try both raw and normalized)
        alias = self.db.execute(
            select(VenueAlias).where(VenueAlias.alias == raw_name)
        ).scalar_one_or_none()
        if alias:
            return alias.venue

        if canonical_name != raw_name:
            alias = self.db.execute(
                select(VenueAlias).where(VenueAlias.alias == canonical_name)
            ).scalar_one_or_none()
            if alias:
                self._ensure_venue_alias(alias.venue, raw_name)
                return alias.venue

        # Create new venue with normalized name; store raw as alias if different
        venue = Venue(
            name=canonical_name,
            openalex_id=ext.venue.external_id,
            issn=ext.venue.issn,
            publisher=ext.venue.publisher,
            venue_type=ext.venue.venue_type,
        )
        self.db.add(venue)
        self.db.flush()
        if canonical_name != raw_name:
            self._ensure_venue_alias(venue, raw_name)
        return venue

    def _ensure_venue_alias(self, venue: Venue, alias_name: str) -> None:
        """Add a VenueAlias if it doesn't already exist."""
        existing = self.db.execute(
            select(VenueAlias).where(
                VenueAlias.venue_id == venue.id, VenueAlias.alias == alias_name
            )
        ).scalar_one_or_none()
        if not existing:
            self.db.add(VenueAlias(venue_id=venue.id, alias=alias_name))

    def _resolve_author(self, ext_author: ExternalWork) -> Author:
        """Find or create an Author by openalex_id or name."""
        if ext_author.external_id:
            author = self.db.execute(
                select(Author).where(Author.openalex_id == ext_author.external_id)
            ).scalar_one_or_none()
            if author:
                return author

        # Fallback: match by name (imprecise but sufficient for local use)
        author = self.db.execute(
            select(Author).where(Author.name == ext_author.name)
        ).scalar_one_or_none()
        if author:
            if author.openalex_id is None and ext_author.external_id:
                author.openalex_id = ext_author.external_id
            return author

        author = Author(name=ext_author.name, openalex_id=ext_author.external_id)
        self.db.add(author)
        self.db.flush()
        return author

    def _ensure_citation(
        self, citing_work_id: int, cited_work_id: int, source: str = "openalex"
    ) -> bool:
        """Create a citation edge if it doesn't already exist.

        Returns True if a new edge was created, False if it already existed.
        """
        existing = self.db.execute(
            select(Citation).where(
                Citation.citing_work_id == citing_work_id,
                Citation.cited_work_id == cited_work_id,
            )
        ).scalar_one_or_none()
        if not existing:
            self.db.add(
                Citation(
                    citing_work_id=citing_work_id,
                    cited_work_id=cited_work_id,
                    source=source,
                )
            )
            return True
        return False

    def import_by_semantic_scholar_id(self, ss_id: str, ss_client) -> Work | None:
        """Import a work by Semantic Scholar ID. Returns the persisted Work or None if not found."""
        ss_paper = ss_client.get_paper_by_id(ss_id)
        if ss_paper is None:
            return None
        # If S2 provided a DOI, run the full OA pipeline so we also get openalex_id.
        if ss_paper.doi:
            oa_work = self.import_by_doi(ss_paper.doi)
            if oa_work is not None:
                if oa_work.semantic_scholar_id is None and ss_paper.semantic_scholar_id:
                    oa_work.semantic_scholar_id = ss_paper.semantic_scholar_id
                    self.db.commit()
                return oa_work
        return self._upsert_work(ss_paper)

    # -- Search-based import --------------------------------------------------

    def search_import_candidates(
        self,
        title: str,
        authors: str | None,
        year: int | None,
        crossref_client,
        ss_client=None,
        max_results: int = 5,
    ) -> list[SearchImportCandidate]:
        """Search Crossref (with Semantic Scholar fallback) and return candidates.

        The Semantic Scholar fallback is used when Crossref returns zero results.
        Candidates are returned with source label and a relevance score.
        """
        # Build combined query string
        parts: list[str] = [title]
        if authors:
            parts.append(authors)
        if year:
            parts.append(str(year))
        query = " ".join(parts)

        # ---- Crossref search ------------------------------------------------
        crossref_items = crossref_client.search_works(query, rows=max_results)
        crossref_candidates = self._parse_crossref_search_candidates(crossref_items)

        if crossref_candidates:
            return crossref_candidates[:max_results]

        # ---- Semantic Scholar fallback --------------------------------------
        if ss_client is None:
            return []

        ss_items = ss_client.search_by_title(query, limit=max_results)
        return self._parse_ss_search_candidates(ss_items)[:max_results]

    @staticmethod
    def _parse_crossref_search_candidates(
        items: list[dict],
    ) -> list[SearchImportCandidate]:
        candidates: list[SearchImportCandidate] = []
        for item in items:
            doi = (item.get("DOI") or "").lower() or None
            titles = item.get("title") or []
            title = titles[0] if titles else "(untitled)"
            authors: list[str] = []
            for a in item.get("author") or []:
                name = f"{a.get('given', '')} {a.get('family', '')}".strip()
                if name:
                    authors.append(name)
            pub_year = None
            issued = item.get("issued") or {}
            date_parts = issued.get("date-parts") or []
            if date_parts and date_parts[0] and date_parts[0][0]:
                pub_year = date_parts[0][0]
            container = item.get("container-title") or []
            venue = container[0] if container else None
            score = float(item.get("score") or 0)
            candidates.append(SearchImportCandidate(
                title=title,
                authors=authors,
                year=pub_year,
                venue=venue,
                doi=doi,
                semantic_scholar_id=None,
                source="crossref",
                score=score,
            ))
        return candidates

    @staticmethod
    def _parse_ss_search_candidates(
        items: list[dict],
    ) -> list[SearchImportCandidate]:
        candidates: list[SearchImportCandidate] = []
        for i, item in enumerate(items):
            paper_id = item.get("paperId") or None
            ext_ids = item.get("externalIds") or {}
            doi_raw = ext_ids.get("DOI") or ""
            doi = doi_raw.lower() or None
            title = item.get("title") or "(untitled)"
            authors: list[str] = [
                a.get("name", "") for a in (item.get("authors") or []) if a.get("name")
            ]
            year = item.get("year")
            # S2 search doesn't return a score — assign descending synthetic scores
            score = float(50 - i)
            candidates.append(SearchImportCandidate(
                title=title,
                authors=authors,
                year=year,
                venue=None,  # S2 search results don't include venue
                doi=doi,
                semantic_scholar_id=paper_id,
                source="semantic_scholar",
                score=score,
            ))
        return candidates

    # -- Semantic Scholar enrichment ------------------------------------------

    def enrich_from_semantic_scholar(
        self, work_id: int, ss_client, direction: str = "both"
    ) -> dict:
        """Fetch backward and forward citations from Semantic Scholar.

        Looks up the paper on Semantic Scholar (by DOI, then by stored
        semantic_scholar_id), fetches all references and citing papers,
        upserts each returned work into the library, and creates Citation
        edges (skipping duplicates).

        Returns a summary dict with new/existing counts for each direction.

        Raises:
            ValueError: work not found, or work has no DOI or S2 ID.
            RuntimeError: paper not found on Semantic Scholar.
        """
        work = self.db.get(Work, work_id)
        if not work:
            raise ValueError(f"Work {work_id} not found")

        if not work.doi and not work.semantic_scholar_id:
            raise ValueError(
                f"Work {work_id} has no DOI or Semantic Scholar ID — "
                "cannot look up on Semantic Scholar"
            )

        # Look up the paper to get its Semantic Scholar ID
        ss_paper: ExternalWork | None = None
        if work.doi:
            ss_paper = ss_client.get_paper_by_doi(work.doi)
        if ss_paper is None and work.semantic_scholar_id:
            ss_paper = ss_client.get_paper_by_id(work.semantic_scholar_id)
        if ss_paper is None and work.title:
            # Title-based fallback: handles cases where the DOI in our DB
            # differs from the one S2 knows, or the S2 ID is a CorpusId that
            # the /paper/{id} endpoint doesn't accept directly.
            norm_local = _normalize_title_for_cmp(work.title)
            candidates = ss_client.search_by_title(work.title, limit=5)
            for cand in candidates:
                if _normalize_title_for_cmp(cand.get("title") or "") == norm_local:
                    paper_id = cand.get("paperId")
                    if paper_id:
                        ss_paper = ss_client.get_paper_by_id(paper_id)
                        break

        if ss_paper is None:
            raise RuntimeError(
                f"Paper not found on Semantic Scholar (doi={work.doi!r}, "
                f"semantic_scholar_id={work.semantic_scholar_id!r})"
            )

        paper_id = ss_paper.semantic_scholar_id
        if not paper_id:
            raise RuntimeError("Semantic Scholar returned a paper without a paperId")

        # Store the S2 ID on the work if we just discovered it
        if work.semantic_scholar_id is None and paper_id:
            work.semantic_scholar_id = paper_id
            self.db.commit()

        # Fetch backward citations (references)
        new_refs = 0
        existing_refs = 0
        raw_refs = 0
        if direction in ("both", "backward"):
            refs = ss_client.get_references(paper_id)
            raw_refs = len(refs)
            logger.info("S2 references for work=%d: %d items returned by API", work_id, raw_refs)
            for ref_ext in refs:
                ref_work = self._upsert_work(ref_ext)
                # If S2 gave us a DOI but openalex_id is still missing, try OA
                # enrichment so the work gets a proper openalex_id for future fetches.
                if ref_ext.doi and ref_work.openalex_id is None:
                    try:
                        oa_work = self.import_by_doi(ref_ext.doi)
                        if oa_work is not None:
                            ref_work = oa_work
                    except Exception:
                        pass  # Best-effort; keep the S2 stub
                created = self._ensure_citation(
                    citing_work_id=work.id,
                    cited_work_id=ref_work.id,
                    source="semantic_scholar",
                )
                if created:
                    new_refs += 1
                else:
                    existing_refs += 1
            self.db.commit()

        # Fetch forward citations (papers citing this work)
        new_citing = 0
        existing_citing = 0
        raw_citing = 0
        if direction in ("both", "forward"):
            citing_papers = ss_client.get_citations(paper_id)
            raw_citing = len(citing_papers)
            logger.info("S2 citing papers for work=%d: %d items returned by API", work_id, raw_citing)
            for cite_ext in citing_papers:
                cite_work = self._upsert_work(cite_ext)
                # Same OA enrichment fallback as for references above.
                if cite_ext.doi and cite_work.openalex_id is None:
                    try:
                        oa_work = self.import_by_doi(cite_ext.doi)
                        if oa_work is not None:
                            cite_work = oa_work
                    except Exception:
                        pass  # Best-effort; keep the S2 stub
                created = self._ensure_citation(
                    citing_work_id=cite_work.id,
                    cited_work_id=work.id,
                    source="semantic_scholar",
                )
                if created:
                    new_citing += 1
                else:
                    existing_citing += 1
            self.db.commit()

        logger.info(
            "S2 enrichment work=%d: +%d refs (%d exist, %d raw), +%d citing (%d exist, %d raw)",
            work_id, new_refs, existing_refs, raw_refs, new_citing, existing_citing, raw_citing,
        )
        return {
            "new_references": new_refs,
            "existing_references": existing_refs,
            "raw_references": raw_refs,
            "new_citing": new_citing,
            "existing_citing": existing_citing,
            "raw_citing": raw_citing,
        }

    # -- GROBID reference resolution ------------------------------------------

    def enrich_from_grobid(self, work_id: int) -> GrobidEnrichResult:
        """Extract and resolve references from a work's primary PDF via GROBID.

        Steps:
        1. Load the work's primary PDF and read its bytes.
        2. Send to the configured GROBID instance (``grobid_url`` setting).
           The raw reference list is cached permanently so subsequent calls
           skip the GROBID network round-trip and re-attempt only resolution.
        3. Resolve each reference through three paths:
           A. DOI  → library lookup → import_by_doi()
           B. arXiv ID (no DOI) → library lookup → OpenAlex lookup
           C. Title only → search_import_candidates() with Jaccard / author / year matching
        4. Create Citation edges for all successfully resolved works.
        5. Return a :class:`GrobidEnrichResult` summary.

        Raises:
            ValueError: work not found, has no primary PDF, PDF missing on
                        disk, or ``grobid_url`` is not configured in Settings.
            GrobidError: GROBID service is unreachable or returns an HTTP error.
        """
        from litexplorer.external.grobid import GrobidClient

        # ------------------------------------------------------------------
        # Step 1: Load work and locate primary PDF
        # ------------------------------------------------------------------
        work = self.db.get(Work, work_id)
        if work is None:
            raise ValueError(f"Work {work_id} not found")

        pdfs = self.db.execute(
            select(WorkPDF).where(WorkPDF.work_id == work_id)
        ).scalars().all()

        primary_pdf = next((p for p in pdfs if p.is_primary), None)
        if primary_pdf is None and pdfs:
            primary_pdf = pdfs[0]
        if primary_pdf is None:
            raise ValueError(
                f"Work {work_id} has no PDF — attach a PDF before running GROBID extraction"
            )

        # ------------------------------------------------------------------
        # Step 2: Resolve PDF storage path and read bytes
        # ------------------------------------------------------------------
        pdf_storage_row = self.db.execute(
            select(Setting).where(Setting.key == "pdf_storage_path")
        ).scalar_one_or_none()
        pdf_storage_val = (pdf_storage_row.value or "").strip() if pdf_storage_row else ""
        pdf_root = Path(pdf_storage_val) if pdf_storage_val else settings.pdf_dir

        pdf_path = pdf_root / str(work_id) / primary_pdf.filename
        if not pdf_path.exists():
            raise ValueError(f"PDF file not found on disk: {pdf_path}")

        pdf_bytes = pdf_path.read_bytes()

        # ------------------------------------------------------------------
        # Step 3: Extract references — use cache when available
        # ------------------------------------------------------------------
        cache_key = f"grobid_references:{work_id}"
        cached = self._get_cache("grobid", cache_key)

        if cached is not None:
            refs_data: list[dict] = json.loads(cached.response_json)
            references = [_dict_to_grobid_ref(r) for r in refs_data]
            logger.info(
                "GROBID: loaded %d references from cache for work=%d",
                len(references), work_id,
            )
        else:
            grobid_url_row = self.db.execute(
                select(Setting).where(Setting.key == "grobid_url")
            ).scalar_one_or_none()
            grobid_url = (grobid_url_row.value or "").strip() if grobid_url_row else ""
            if not grobid_url:
                raise ValueError(
                    "GROBID URL is not configured — set 'grobid_url' in Settings"
                )

            ssl_verify_row = self.db.execute(
                select(Setting).where(Setting.key == "ssl_verify")
            ).scalar_one_or_none()
            ssl_val = (ssl_verify_row.value or "true").strip() if ssl_verify_row else "true"
            ssl_verify = ssl_val.lower() not in ("false", "0", "no")

            grobid_client = GrobidClient(base_url=grobid_url, ssl_verify=ssl_verify)
            try:
                references = grobid_client.extract_references(pdf_bytes)
            finally:
                grobid_client.close()

            refs_data = [_grobid_ref_to_dict(r) for r in references]
            self._set_cache("grobid", cache_key, json.dumps(refs_data), "permanent")
            logger.info(
                "GROBID: extracted %d references from work=%d PDF",
                len(references), work_id,
            )

        # ------------------------------------------------------------------
        # Steps 4–6: Resolve each reference, create citation edges, count
        # ------------------------------------------------------------------
        new_count = 0
        existing_count = 0
        failed_count = 0

        for ref in references:
            try:
                resolved_id, is_new_import = self._resolve_grobid_reference(ref)
                if resolved_id is not None:
                    self._ensure_citation(
                        citing_work_id=work_id,
                        cited_work_id=resolved_id,
                        source="grobid",
                    )
                    if is_new_import:
                        new_count += 1
                    else:
                        existing_count += 1
                else:
                    failed_count += 1
            except Exception as exc:
                label = (ref.title or ref.raw_string or "")[:80]
                logger.warning(
                    "GROBID: error resolving reference %r for work=%d: %s",
                    label, work_id, exc,
                )
                failed_count += 1

        self.db.commit()

        logger.info(
            "GROBID enrichment work=%d: total=%d new=%d existing=%d failed=%d",
            work_id, len(references), new_count, existing_count, failed_count,
        )
        return GrobidEnrichResult(
            new_count=new_count,
            existing_count=existing_count,
            failed_count=failed_count,
            total_extracted=len(references),
        )

    def _resolve_grobid_reference(self, ref) -> tuple[int | None, bool]:
        """Resolve a single GrobidReference to a (work_id, is_new_import) pair.

        Tries three paths in order:
        A. DOI → library lookup then import_by_doi()
        B. arXiv ID (only when no DOI) → library lookup then OpenAlex
        C. Title-only fallback → search_import_candidates() with strict matching

        Returns ``(None, False)`` when all paths fail.
        ``is_new_import`` is True when a new Work was added to the library.
        """
        # ---- PATH A: DOI present ------------------------------------------
        if ref.doi:
            doi = ref.doi.lower().strip()

            # Check primary DOI
            work = self.db.execute(
                select(Work).where(Work.doi == doi)
            ).scalar_one_or_none()

            # Also check secondary DOIs (WorkDOI table)
            if work is None:
                wdoi = self.db.execute(
                    select(WorkDOI).where(WorkDOI.doi == doi)
                ).scalar_one_or_none()
                if wdoi is not None:
                    work = self.db.get(Work, wdoi.work_id)

            if work is not None:
                return work.id, False

            # Not in library — import via OpenAlex / Crossref pipeline
            imported = self.import_by_doi(doi)
            if imported is not None:
                # Cross-check title similarity; log warning but still accept
                # (GROBID DOI extraction is high-precision)
                if ref.title and imported.title and imported.title != "(untitled)":
                    norm_ref = _normalize_title_for_cmp(ref.title)
                    norm_imp = _normalize_title_for_cmp(imported.title)
                    sim = _jaccard_similarity(norm_ref, norm_imp)
                    if sim < 0.7:
                        logger.warning(
                            "GROBID: DOI %r resolved but title Jaccard %.2f < 0.7 "
                            "(ref=%r, imported=%r)",
                            doi, sim, ref.title[:80], imported.title[:80],
                        )
                return imported.id, True

            # DOI present but import failed — fall through to title search
            logger.debug(
                "GROBID: DOI %r not importable, falling back to title search", doi
            )

        # ---- PATH B: arXiv ID present (no DOI) ----------------------------
        if ref.arxiv_id and not ref.doi:
            arxiv_id = ref.arxiv_id.strip()

            work = self.db.execute(
                select(Work).where(Work.arxiv_id == arxiv_id)
            ).scalar_one_or_none()
            if work is not None:
                return work.id, False

            # Attempt OpenAlex lookup via works/arxiv:{id}
            try:
                raw = self.client.get_work_by_id_raw(f"arxiv:{arxiv_id}")
                if raw is not None:
                    ext = parse_work(raw)
                    oa_cache_key = (
                        f"work:doi:{ext.doi}" if ext.doi
                        else f"work:openalex:{ext.external_id}" if ext.external_id
                        else f"work:arxiv:{arxiv_id}"
                    )
                    self._set_cache("openalex", oa_cache_key, json.dumps(raw), "permanent")
                    imported = self._upsert_work(ext)
                    return imported.id, True
            except Exception as exc:
                logger.debug(
                    "GROBID: OpenAlex arXiv lookup failed for %r: %s", arxiv_id, exc
                )

            # Fall through to title search

        # ---- PATH C: Title-only fallback ----------------------------------
        if not ref.title:
            return None, False

        if self.crossref_client is None:
            logger.debug("GROBID: no Crossref client configured — cannot search by title")
            return None, False

        norm_ref_title = _normalize_title_for_cmp(ref.title)

        # Extract first-author surname (last word of the first author string)
        ref_first_surname: str | None = None
        if ref.authors:
            parts = ref.authors[0].strip().split()
            ref_first_surname = parts[-1].lower() if parts else None

        candidates = self.search_import_candidates(
            title=ref.title,
            authors=None,
            year=None,
            crossref_client=self.crossref_client,
        )

        best_cand = None
        for cand in candidates:
            # Condition 1: title Jaccard >= 0.7
            norm_cand_title = _normalize_title_for_cmp(cand.title)
            if _jaccard_similarity(norm_ref_title, norm_cand_title) < 0.7:
                continue

            # Condition 2: first-author surname match (if both sides have data)
            if ref_first_surname and cand.authors:
                cand_parts = cand.authors[0].strip().split()
                cand_surname = cand_parts[-1].lower() if cand_parts else ""
                if cand_surname and cand_surname != ref_first_surname:
                    continue

            # Condition 3: year ±1 (if both sides have data)
            if ref.year and cand.year:
                try:
                    if abs(cand.year - int(ref.year)) > 1:
                        continue
                except (ValueError, TypeError):
                    pass  # Unparseable year — ignore this condition

            best_cand = cand
            break

        if best_cand is None:
            return None, False

        # Import the best candidate
        if best_cand.doi:
            work = self.db.execute(
                select(Work).where(Work.doi == best_cand.doi)
            ).scalar_one_or_none()
            if work is not None:
                return work.id, False

            imported = self.import_by_doi(best_cand.doi)
            if imported is not None:
                return imported.id, True

        # Candidate matched but has no DOI — cannot import
        return None, False

    # -- Cache helpers --------------------------------------------------------

    def _get_cache(self, source: str, query_key: str) -> ApiCache | None:
        return self.db.execute(
            select(ApiCache).where(
                ApiCache.source == source, ApiCache.query_key == query_key
            )
        ).scalar_one_or_none()

    def _set_cache(
        self, source: str, query_key: str, response_json: str, cache_type: str
    ) -> None:
        existing = self._get_cache(source, query_key)
        if existing:
            existing.response_json = response_json
            existing.fetched_at = datetime.now(timezone.utc)
        else:
            self.db.add(
                ApiCache(
                    source=source,
                    query_key=query_key,
                    response_json=response_json,
                    cache_type=cache_type,
                )
            )
        self.db.flush()
