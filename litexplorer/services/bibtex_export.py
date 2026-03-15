"""BibTeX export helpers for the library."""

from __future__ import annotations

import re

from litexplorer.models.library import Venue, Work


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_TITLE_STOPWORDS = frozenset(
    {"a", "an", "the", "of", "in", "on", "at", "for", "to", "and", "or", "with", "is", "are"}
)


def _bibtex_key(work: Work) -> str:
    """Return a BibTeX key for *work*.

    Uses the stored ``bibtex_key`` when available; otherwise synthesises one
    from the first author's last name, publication year, and the first
    significant word of the title.
    """
    if work.bibtex_key:
        return work.bibtex_key

    first_author = ""
    if work.authors:
        name = work.authors[0].author.name.strip()
        parts = name.split()
        last = parts[-1] if parts else "Unknown"
        first_author = re.sub(r"[^A-Za-z]", "", last)

    year = str(work.publication_year) if work.publication_year else ""

    title_word = ""
    if work.title:
        for raw_word in re.split(r"\W+", work.title):
            w = re.sub(r"[^A-Za-z]", "", raw_word)
            if w and w.lower() not in _TITLE_STOPWORDS:
                title_word = w.capitalize()
                break

    return f"{first_author}{year}{title_word}" or f"work{work.id}"


def _venue_display_name(venue: Venue) -> str:
    """Return the preferred display name for *venue* (first alias by sort_order)."""
    if venue.aliases:
        return venue.aliases[0].alias
    return venue.name


def _generate_entry(work: Work) -> str:
    """Generate a BibTeX entry from the structured fields of *work*."""
    key = _bibtex_key(work)

    # Determine entry type and venue field name
    if work.venue:
        if work.venue.venue_type == "conference":
            entry_type = "inproceedings"
            venue_key = "booktitle"
        else:
            entry_type = "article"
            venue_key = "journal"
        venue_value: str | None = _venue_display_name(work.venue)
    elif work.arxiv_id:
        entry_type = "misc"
        venue_key = ""
        venue_value = None
    else:
        entry_type = "article"
        venue_key = ""
        venue_value = None

    fields: list[tuple[str, str]] = []
    fields.append(("title", work.title))

    if work.authors:
        author_str = " and ".join(wa.author.name for wa in work.authors)
        fields.append(("author", author_str))

    if work.publication_year is not None:
        fields.append(("year", str(work.publication_year)))

    if venue_value and venue_key:
        fields.append((venue_key, venue_value))

    if work.doi:
        fields.append(("doi", work.doi))

    # URL: venue location first, then any location
    url: str | None = None
    if work.locations:
        venue_loc = next((loc for loc in work.locations if loc.location_type == "venue"), None)
        url = venue_loc.url if venue_loc else work.locations[0].url
    if url:
        fields.append(("url", url))

    if work.arxiv_id:
        fields.append(("eprint", work.arxiv_id))
        fields.append(("archivePrefix", "arXiv"))

    lines = [f"@{entry_type}{{{key},"]
    for k, v in fields:
        lines.append(f"  {k} = {{{v}}},")
    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def works_to_bibtex(works: list[Work]) -> str:
    """Convert a list of :class:`~litexplorer.models.library.Work` objects to BibTeX.

    If a work has a stored ``bibtex_entry`` it is returned verbatim; otherwise
    a BibTeX entry is generated from the work's structured fields.

    The relationships ``authors`` (with nested ``author``), ``venue``,
    ``venue.aliases``, and ``locations`` must be loaded before calling this
    function.

    Returns:
        A BibTeX string with one entry per work separated by blank lines,
        or an empty string when *works* is empty.
    """
    if not works:
        return ""
    entries = [work.bibtex_entry if work.bibtex_entry else _generate_entry(work) for work in works]
    return "\n\n".join(entries) + "\n"
