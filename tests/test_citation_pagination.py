"""Tests for citation list pagination, sorting, and total_count."""

from __future__ import annotations

import math

import pytest

from litexplorer.models.library import Citation, Work
from litexplorer.api.works import _relevance_score


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def seed_work(db_session):
    w = Work(title="The Seed Paper", doi="10.1/seed", publication_year=2020, citation_count=500)
    db_session.add(w)
    db_session.commit()
    return w


@pytest.fixture()
def citing_works(db_session, seed_work):
    """Create 7 citing works with varying citation_count and publication_year."""
    works = [
        Work(title="High-cited recent",    doi="10.1/c1", publication_year=2023, citation_count=1000),
        Work(title="High-cited old",       doi="10.1/c2", publication_year=2005, citation_count=900),
        Work(title="Low-cited recent",     doi="10.1/c3", publication_year=2022, citation_count=10),
        Work(title="Low-cited old",        doi="10.1/c4", publication_year=2003, citation_count=5),
        Work(title="Null year high-cited", doi="10.1/c5", publication_year=None, citation_count=800),
        Work(title="Zero citations",       doi="10.1/c6", publication_year=2018, citation_count=0),
        Work(title="Null citations",       doi="10.1/c7", publication_year=2015, citation_count=None),
    ]
    for w in works:
        db_session.add(w)
    db_session.flush()
    for w in works:
        db_session.add(Citation(citing_work_id=w.id, cited_work_id=seed_work.id, source="openalex"))
    db_session.commit()
    return works


@pytest.fixture()
def referenced_works(db_session, seed_work):
    """Create 3 works referenced by seed_work (backward citations)."""
    works = [
        Work(title="Reference A", doi="10.1/r1", publication_year=2010, citation_count=200),
        Work(title="Reference B", doi="10.1/r2", publication_year=2015, citation_count=50),
        Work(title="Reference C", doi="10.1/r3", publication_year=2000, citation_count=5),
    ]
    for w in works:
        db_session.add(w)
    db_session.flush()
    for w in works:
        db_session.add(Citation(citing_work_id=seed_work.id, cited_work_id=w.id, source="openalex"))
    db_session.commit()
    return works


# ---------------------------------------------------------------------------
# Unit tests for _relevance_score
# ---------------------------------------------------------------------------


def test_relevance_score_basic():
    score = _relevance_score(100, 2020)
    assert score == pytest.approx(math.log1p(100) + (2020 - 2000) / 5.0)


def test_relevance_score_null_values():
    # NULL citation_count and year treated as 0
    assert _relevance_score(None, None) == pytest.approx(0.0)
    assert _relevance_score(0, 2000) == pytest.approx(math.log1p(0) + 0.0)


def test_relevance_score_old_year_no_penalty():
    # Year before 2000 → no penalty (clamped at 0)
    score_old = _relevance_score(0, 1990)
    score_zero = _relevance_score(0, None)
    assert score_old == score_zero  # both 0


def test_relevance_ordering():
    # High-cited recent paper should beat high-cited old paper
    assert _relevance_score(900, 2023) > _relevance_score(900, 2005)
    # High-cited paper should beat low-cited recent paper
    assert _relevance_score(1000, 2010) > _relevance_score(10, 2023)


# ---------------------------------------------------------------------------
# API tests: forward citations
# ---------------------------------------------------------------------------


def test_forward_citations_returns_response_shape(client, seed_work, citing_works):
    resp = client.get(f"/api/works/{seed_work.id}/citations/forward")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total_count" in data
    assert data["total_count"] == 7


def test_forward_citations_relevance_order(client, seed_work, citing_works):
    resp = client.get(f"/api/works/{seed_work.id}/citations/forward?sort=relevance&limit=7")
    assert resp.status_code == 200
    items = resp.json()["items"]
    scores = [
        _relevance_score(w.get("citation_count"), w.get("publication_year"))
        for w in items
    ]
    assert scores == sorted(scores, reverse=True), "Items not in descending relevance order"


def test_forward_citations_default_sort_is_relevance(client, seed_work, citing_works):
    resp_default = client.get(f"/api/works/{seed_work.id}/citations/forward?limit=7")
    resp_relevance = client.get(f"/api/works/{seed_work.id}/citations/forward?sort=relevance&limit=7")
    assert resp_default.json()["items"] == resp_relevance.json()["items"]


def test_forward_citations_pagination_offset_limit(client, seed_work, citing_works):
    # Page 1: items 0-1 (limit=2, offset=0)
    p1 = client.get(f"/api/works/{seed_work.id}/citations/forward?sort=relevance&limit=2&offset=0").json()
    # Page 2: items 2-3 (limit=2, offset=2)
    p2 = client.get(f"/api/works/{seed_work.id}/citations/forward?sort=relevance&limit=2&offset=2").json()

    assert p1["total_count"] == 7
    assert p2["total_count"] == 7
    assert len(p1["items"]) == 2
    assert len(p2["items"]) == 2
    # Pages should not overlap
    ids_p1 = {w["id"] for w in p1["items"]}
    ids_p2 = {w["id"] for w in p2["items"]}
    assert ids_p1.isdisjoint(ids_p2)


def test_forward_citations_total_count_unaffected_by_pagination(client, seed_work, citing_works):
    p1 = client.get(f"/api/works/{seed_work.id}/citations/forward?limit=1&offset=0").json()
    p3 = client.get(f"/api/works/{seed_work.id}/citations/forward?limit=1&offset=6").json()
    assert p1["total_count"] == 7
    assert p3["total_count"] == 7


def test_forward_citations_year_desc_sort(client, seed_work, citing_works):
    resp = client.get(f"/api/works/{seed_work.id}/citations/forward?sort=year_desc&limit=10")
    items = resp.json()["items"]
    # NULL years should be at the end; non-null years should be descending
    non_null = [w["publication_year"] for w in items if w["publication_year"] is not None]
    assert non_null == sorted(non_null, reverse=True)
    # NULL year items appear last
    null_indices = [i for i, w in enumerate(items) if w["publication_year"] is None]
    non_null_indices = [i for i, w in enumerate(items) if w["publication_year"] is not None]
    if null_indices and non_null_indices:
        assert max(non_null_indices) < min(null_indices)


def test_forward_citations_citations_desc_sort(client, seed_work, citing_works):
    resp = client.get(f"/api/works/{seed_work.id}/citations/forward?sort=citations_desc&limit=10")
    items = resp.json()["items"]
    non_null = [w["citation_count"] for w in items if w["citation_count"] is not None]
    assert non_null == sorted(non_null, reverse=True)


def test_forward_citations_null_year_included(client, seed_work, citing_works):
    """Works with NULL publication_year are included in results (not filtered out)."""
    resp = client.get(f"/api/works/{seed_work.id}/citations/forward?limit=10")
    ids = {w["id"] for w in resp.json()["items"]}
    null_year_work = next(w for w in citing_works if w.title == "Null year high-cited")
    assert null_year_work.id in ids


def test_forward_citations_invalid_sort_falls_back(client, seed_work, citing_works):
    resp = client.get(f"/api/works/{seed_work.id}/citations/forward?sort=bogus&limit=7")
    assert resp.status_code == 200
    # Falls back to relevance; just check it returns valid data
    data = resp.json()
    assert data["total_count"] == 7


# ---------------------------------------------------------------------------
# API tests: backward citations
# ---------------------------------------------------------------------------


def test_backward_citations_returns_response_shape(client, seed_work, referenced_works):
    resp = client.get(f"/api/works/{seed_work.id}/citations/backward")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total_count" in data
    assert data["total_count"] == 3


def test_backward_citations_relevance_order(client, seed_work, referenced_works):
    resp = client.get(f"/api/works/{seed_work.id}/citations/backward?sort=relevance")
    items = resp.json()["items"]
    scores = [
        _relevance_score(w.get("citation_count"), w.get("publication_year"))
        for w in items
    ]
    assert scores == sorted(scores, reverse=True)


def test_backward_citations_pagination(client, seed_work, referenced_works):
    p1 = client.get(f"/api/works/{seed_work.id}/citations/backward?limit=1&offset=0").json()
    p2 = client.get(f"/api/works/{seed_work.id}/citations/backward?limit=1&offset=1").json()
    assert p1["total_count"] == 3
    assert p2["total_count"] == 3
    assert p1["items"][0]["id"] != p2["items"][0]["id"]


# ---------------------------------------------------------------------------
# citation_count field on CitationWorkBrief
# ---------------------------------------------------------------------------


def test_citation_work_brief_includes_citation_count(client, seed_work, citing_works):
    resp = client.get(f"/api/works/{seed_work.id}/citations/forward?limit=10")
    items = resp.json()["items"]
    assert all("citation_count" in w for w in items)
    high_cited = next(w for w in items if w["title"] == "High-cited recent")
    assert high_cited["citation_count"] == 1000
