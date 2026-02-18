"""Enrichment service — imports works from external sources into the local DB.

Handles cache-first fetching, deduplication, venue alias auto-creation,
and stub work creation for citation neighbors.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

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
    WorkLocation,
)

logger = logging.getLogger(__name__)


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

    def fetch_backward_citations(self, work_id: int) -> list[Work]:
        """Fetch and persist backward citations (references) for a work.

        Reads referenced_work_ids from the cached work data, fetches metadata
        for each referenced work, creates stub Work records, and creates
        Citation edges.
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
                # Cache empty result
                self._set_cache("openalex", cache_key, "[]", "permanent")
                return []

            raw_list = self.client.get_works_by_ids_raw(ref_ids)
            self._set_cache("openalex", cache_key, json.dumps(raw_list), "permanent")

        # Parse and persist each referenced work
        results: list[Work] = []
        for raw in raw_list:
            ext_work = parse_work(raw)
            db_work = self._upsert_work(ext_work)
            results.append(db_work)

            # Create citation edge: work (the seed) cites db_work (the reference)
            self._ensure_citation(citing_work_id=work.id, cited_work_id=db_work.id)

        self.db.commit()
        return results

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

        self.db.commit()
        return results

    def _get_referenced_work_ids(self, work: Work) -> list[str]:
        """Get the referenced_work_ids for a work from its cached API response."""
        # Try to get from the cached work data
        for doi_key in (work.doi, ):
            if doi_key:
                cache_key = f"work:doi:{doi_key}"
                cached = self._get_cache("openalex", cache_key)
                if cached:
                    raw = json.loads(cached.response_json)
                    ref_urls = raw.get("referenced_works") or []
                    return [
                        url.removeprefix("https://openalex.org/")
                        for url in ref_urls
                        if url
                    ]
        return []

    def _upsert_work(self, ext: ExternalWork) -> Work:
        """Find or create a Work from an ExternalWork. Update-without-overwrite."""
        existing = self._find_existing_work(ext)
        if existing:
            return self._update_work(existing, ext)
        return self._create_work(ext)

    def _find_existing_work(self, ext: ExternalWork) -> Work | None:
        """Deduplication cascade: DOI → openalex_id → arxiv_id."""
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
        return None

    def _create_work(self, ext: ExternalWork) -> Work:
        """Create a new Work record from an ExternalWork."""
        venue = self._resolve_venue(ext) if ext.venue else None

        work = Work(
            doi=ext.doi,
            arxiv_id=ext.arxiv_id,
            openalex_id=ext.external_id,
            title=ext.title,
            abstract=ext.abstract,
            publication_year=ext.publication_year,
            citation_count=ext.citation_count,
            venue_id=venue.id if venue else None,
        )
        self.db.add(work)
        self.db.flush()  # Get work.id

        # Authors
        for i, ext_author in enumerate(ext.authors):
            author = self._resolve_author(ext_author)
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
        if work.abstract is None and ext.abstract:
            work.abstract = ext.abstract
        if work.publication_year is None and ext.publication_year:
            work.publication_year = ext.publication_year
        if work.title == "(untitled)" and ext.title and ext.title != "(untitled)":
            work.title = ext.title

        # Always update citation count (it changes over time)
        if ext.citation_count is not None:
            work.citation_count = ext.citation_count

        # Venue: fill if missing
        if work.venue_id is None and ext.venue:
            venue = self._resolve_venue(ext)
            if venue:
                work.venue_id = venue.id

        self.db.commit()
        return work

    def _resolve_venue(self, ext: ExternalWork) -> Venue | None:
        """Find or create a Venue, handling alias auto-creation and ISSN matching."""
        if not ext.venue:
            return None

        # Try by ISSN first (stable identifier from Crossref)
        if ext.venue.issn:
            venue = self.db.execute(
                select(Venue).where(Venue.issn == ext.venue.issn)
            ).scalar_one_or_none()
            if venue:
                # Auto-create alias if the Crossref name differs from canonical
                if venue.name != ext.venue.name:
                    self._ensure_venue_alias(venue, ext.venue.name)
                if venue.publisher is None and ext.venue.publisher:
                    venue.publisher = ext.venue.publisher
                return venue

        # Try by openalex_id
        if ext.venue.external_id:
            venue = self.db.execute(
                select(Venue).where(Venue.openalex_id == ext.venue.external_id)
            ).scalar_one_or_none()
            if venue:
                # Auto-create alias if the name differs
                if venue.name != ext.venue.name:
                    self._ensure_venue_alias(venue, ext.venue.name)
                return venue

        # Try by exact name
        venue = self.db.execute(
            select(Venue).where(Venue.name == ext.venue.name)
        ).scalar_one_or_none()
        if venue:
            # Fill openalex_id if missing
            if venue.openalex_id is None and ext.venue.external_id:
                venue.openalex_id = ext.venue.external_id
            return venue

        # Check aliases
        alias = self.db.execute(
            select(VenueAlias).where(VenueAlias.alias == ext.venue.name)
        ).scalar_one_or_none()
        if alias:
            return alias.venue

        # Create new venue
        venue = Venue(
            name=ext.venue.name,
            openalex_id=ext.venue.external_id,
            issn=ext.venue.issn,
            publisher=ext.venue.publisher,
            venue_type=ext.venue.venue_type,
        )
        self.db.add(venue)
        self.db.flush()
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

    def _ensure_citation(self, citing_work_id: int, cited_work_id: int) -> None:
        """Create a citation edge if it doesn't already exist."""
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
                    source="openalex",
                )
            )

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
