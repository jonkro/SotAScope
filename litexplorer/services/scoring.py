"""Shared scoring utilities.

Single source of truth for relevance scoring so the formula can be changed
in one place and affects all callers (timeline API, side-panel sorting, etc.).
"""

from __future__ import annotations

import math


def compute_relevance_score(citation_count: int, publication_year: int | None) -> float:
    """Relevance score combining citation impact (log-scale) and a recency bonus.

    Formula: log(1 + citations) + max(0, (year - 2000) / 2)

    Papers published before 2000 receive no recency bonus.
    The result is rounded to 4 decimal places.
    """
    score = math.log(1 + (citation_count or 0))
    if publication_year is not None:
        score += max(0, (publication_year - 2000) / 2)
    return round(score, 4)
