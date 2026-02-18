"""Smoke tests for every library-layer API endpoint."""


# ---------------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------------

def test_create_and_list_fields(client):
    r = client.post("/api/fields", json={"name": "ai_ml"})
    assert r.status_code == 201
    assert r.json()["name"] == "ai_ml"

    r = client.post("/api/fields", json={"name": "computer_networks"})
    assert r.status_code == 201

    r = client.get("/api/fields")
    assert r.status_code == 200
    names = [f["name"] for f in r.json()]
    assert "ai_ml" in names
    assert "computer_networks" in names


def test_duplicate_field_rejected(client):
    client.post("/api/fields", json={"name": "ai_ml"})
    r = client.post("/api/fields", json={"name": "ai_ml"})
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Venues
# ---------------------------------------------------------------------------

def test_venue_crud(client):
    # Create
    r = client.post("/api/venues", json={
        "name": "ACM SIGCOMM",
        "dblp_id": "conf/sigcomm",
        "venue_type": "conference",
    })
    assert r.status_code == 201
    venue = r.json()
    vid = venue["id"]
    assert venue["name"] == "ACM SIGCOMM"
    assert venue["aliases"] == []

    # Get detail
    r = client.get(f"/api/venues/{vid}")
    assert r.status_code == 200
    assert r.json()["name"] == "ACM SIGCOMM"

    # Update
    r = client.patch(f"/api/venues/{vid}", json={"venue_type": "journal"})
    assert r.status_code == 200
    assert r.json()["venue_type"] == "journal"

    # List with search
    r = client.get("/api/venues", params={"q": "SIGCOMM"})
    assert len(r.json()) == 1

    # Delete
    r = client.delete(f"/api/venues/{vid}")
    assert r.status_code == 204
    r = client.get(f"/api/venues/{vid}")
    assert r.status_code == 404


def test_venue_aliases(client):
    r = client.post("/api/venues", json={"name": "INFOCOM"})
    vid = r.json()["id"]

    # Add alias
    r = client.post(f"/api/venues/{vid}/aliases", json={"alias": "IEEE INFOCOM 2023"})
    assert r.status_code == 201
    alias_id = r.json()["id"]

    # Verify alias in detail
    r = client.get(f"/api/venues/{vid}")
    assert len(r.json()["aliases"]) == 1

    # Remove alias
    r = client.delete(f"/api/venues/{vid}/aliases/{alias_id}")
    assert r.status_code == 204


# ---------------------------------------------------------------------------
# Venue tiers
# ---------------------------------------------------------------------------

def test_venue_tier_upsert(client):
    v = client.post("/api/venues", json={"name": "SIGCOMM"}).json()
    f = client.post("/api/fields", json={"name": "networks"}).json()

    # Create tier
    r = client.put("/api/venue-tiers", json={
        "venue_id": v["id"], "field_id": f["id"], "tier": 1,
    })
    assert r.status_code == 200
    tier_id = r.json()["id"]
    assert r.json()["tier"] == 1
    assert r.json()["venue_name"] == "SIGCOMM"
    assert r.json()["field_name"] == "networks"

    # Upsert (update) same pair
    r = client.put("/api/venue-tiers", json={
        "venue_id": v["id"], "field_id": f["id"], "tier": 2,
    })
    assert r.status_code == 200
    assert r.json()["id"] == tier_id  # same row
    assert r.json()["tier"] == 2

    # List filtered
    r = client.get("/api/venue-tiers", params={"field_id": f["id"]})
    assert len(r.json()) == 1

    # Delete
    r = client.delete(f"/api/venue-tiers/{tier_id}")
    assert r.status_code == 204


# ---------------------------------------------------------------------------
# Authors
# ---------------------------------------------------------------------------

def test_author_crud(client):
    r = client.post("/api/authors", json={"name": "Jane Doe"})
    assert r.status_code == 201
    assert r.json()["name"] == "Jane Doe"

    r = client.get("/api/authors", params={"q": "Jane"})
    assert len(r.json()) == 1


# ---------------------------------------------------------------------------
# Works
# ---------------------------------------------------------------------------

def test_work_crud(client):
    # Create venue first
    v = client.post("/api/venues", json={"name": "NSDI"}).json()

    # Create author
    a = client.post("/api/authors", json={"name": "Alice Smith"}).json()

    # Create work with inline location and author
    r = client.post("/api/works", json={
        "title": "A Cool Paper",
        "doi": "10.1234/cool",
        "publication_year": 2024,
        "venue_id": v["id"],
        "locations": [{"location_type": "venue", "url": "https://example.com/cool"}],
        "authors": [{"author_id": a["id"], "position": 0}],
    })
    assert r.status_code == 201
    work = r.json()
    wid = work["id"]
    assert work["title"] == "A Cool Paper"
    assert work["venue_name"] == "NSDI"
    assert len(work["locations"]) == 1
    assert len(work["authors"]) == 1

    # Get detail
    r = client.get(f"/api/works/{wid}")
    assert r.status_code == 200
    assert r.json()["doi"] == "10.1234/cool"

    # Update
    r = client.patch(f"/api/works/{wid}", json={"citation_count": 42})
    assert r.status_code == 200
    assert r.json()["citation_count"] == 42

    # List with search
    r = client.get("/api/works", params={"q": "Cool"})
    assert len(r.json()) == 1

    # Delete
    r = client.delete(f"/api/works/{wid}")
    assert r.status_code == 204


def test_work_locations(client):
    w = client.post("/api/works", json={"title": "Paper A"}).json()
    wid = w["id"]

    r = client.post(f"/api/works/{wid}/locations", json={
        "location_type": "preprint",
        "url": "https://arxiv.org/abs/1234",
        "is_primary": False,
    })
    assert r.status_code == 201
    loc_id = r.json()["id"]

    r = client.delete(f"/api/works/{wid}/locations/{loc_id}")
    assert r.status_code == 204


def test_work_author_link_unlink(client):
    w = client.post("/api/works", json={"title": "Paper B"}).json()
    a = client.post("/api/authors", json={"name": "Bob"}).json()

    r = client.post(f"/api/works/{w['id']}/authors", json={
        "author_id": a["id"], "position": 0,
    })
    assert r.status_code == 201

    # Duplicate should 409
    r = client.post(f"/api/works/{w['id']}/authors", json={
        "author_id": a["id"], "position": 0,
    })
    assert r.status_code == 409

    r = client.delete(f"/api/works/{w['id']}/authors/{a['id']}")
    assert r.status_code == 204


# ---------------------------------------------------------------------------
# Citations (forward / backward)
# ---------------------------------------------------------------------------

def test_citation_neighbors(client, db_session):
    from litexplorer.models.library import Citation, Work

    w1 = Work(title="Seed Paper", doi="10.1/seed", publication_year=2020)
    w2 = Work(title="Citing Paper", doi="10.1/citing", publication_year=2023)
    w3 = Work(title="Referenced Paper", doi="10.1/ref", publication_year=2018)
    db_session.add_all([w1, w2, w3])
    db_session.flush()

    db_session.add(Citation(citing_work_id=w2.id, cited_work_id=w1.id, source="openalex"))
    db_session.add(Citation(citing_work_id=w1.id, cited_work_id=w3.id, source="openalex"))
    db_session.commit()

    # Forward citations of w1 — papers citing w1
    r = client.get(f"/api/works/{w1.id}/citations/forward")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["title"] == "Citing Paper"

    # Backward citations of w1 — papers w1 references
    r = client.get(f"/api/works/{w1.id}/citations/backward")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["title"] == "Referenced Paper"


# ---------------------------------------------------------------------------
# BibTeX import
# ---------------------------------------------------------------------------

SAMPLE_BIBTEX = """
@inproceedings{Smith2024Cool,
  title = {A Cool Paper About Networks},
  author = {Smith, Alice and Doe, Bob},
  year = {2024},
  doi = {10.5555/cool-networks},
  booktitle = {Proceedings of SIGCOMM},
}

@article{Jones2023Survey,
  title = {A Survey of Surveys},
  author = {Jones, Charlie},
  year = {2023},
}
"""


def test_bibtex_import(client):
    r = client.post("/api/works/import/bibtex", json={"bibtex": SAMPLE_BIBTEX})
    assert r.status_code == 200
    result = r.json()
    assert result["imported"] == 2
    assert result["skipped"] == 0

    # Import again — both should be skipped (DOI match and bibtex_key match)
    r = client.post("/api/works/import/bibtex", json={"bibtex": SAMPLE_BIBTEX})
    result = r.json()
    assert result["skipped"] == 2
    assert result["imported"] == 0


def test_bibtex_import_stores_bibtex_key(client):
    r = client.post("/api/works/import/bibtex", json={"bibtex": SAMPLE_BIBTEX})
    works = r.json()["works"]
    keys = [w["bibtex_key"] for w in works]
    assert "Smith2024Cool" in keys
    assert "Jones2023Survey" in keys
