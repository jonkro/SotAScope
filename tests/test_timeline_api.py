"""Tests for the timeline endpoint."""

import pytest

from litexplorer.models.library import Citation, Venue, Work
from litexplorer.models.project import Project, TopicList, TopicListWork


@pytest.fixture()
def timeline_data(db_session):
    """Set up a project with topic lists, works, and citation edges.

    Layout:
        seed_a (topic list 1) --cites--> ref_1 (backward neighbor)
        seed_a --cites--> seed_b (seed-to-seed citation)
        seed_b (topic list 2) --cites--> ref_2 (backward neighbor)
        citer_1 --cites--> seed_a (forward neighbor)
        citer_2 --cites--> seed_a (forward neighbor)
        citer_2 --cites--> seed_b (forward neighbor of both seeds)
    """
    # Venues with tiers
    venue_top = Venue(name="NeurIPS", venue_type="conference", tier=1)
    venue_other = Venue(name="Some Workshop", venue_type="conference", tier=2)
    db_session.add_all([venue_top, venue_other])
    db_session.flush()

    # Works
    seed_a = Work(title="Seed A", publication_year=2022, venue_id=venue_top.id, citation_count=50)
    seed_b = Work(title="Seed B", publication_year=2023, venue_id=venue_other.id, citation_count=10)
    ref_1 = Work(title="Reference 1", publication_year=2020, venue_id=venue_top.id, citation_count=100)
    ref_2 = Work(title="Reference 2", publication_year=2019, citation_count=5)
    citer_1 = Work(title="Citer 1", publication_year=2024, venue_id=venue_top.id, citation_count=2)
    citer_2 = Work(title="Citer 2", publication_year=2024, citation_count=1)
    db_session.add_all([seed_a, seed_b, ref_1, ref_2, citer_1, citer_2])
    db_session.flush()

    # Project + topic lists
    project = Project(name="Test Project")
    db_session.add(project)
    db_session.flush()

    tl1 = TopicList(project_id=project.id, name="ML Methods", color="#3b82f6")
    tl2 = TopicList(project_id=project.id, name="Applications", color="#ef4444")
    db_session.add_all([tl1, tl2])
    db_session.flush()

    db_session.add(TopicListWork(topic_list_id=tl1.id, work_id=seed_a.id))
    db_session.add(TopicListWork(topic_list_id=tl2.id, work_id=seed_b.id))
    db_session.flush()

    # Citation edges
    db_session.add(Citation(citing_work_id=seed_a.id, cited_work_id=ref_1.id, source="openalex"))
    db_session.add(Citation(citing_work_id=seed_a.id, cited_work_id=seed_b.id, source="openalex"))
    db_session.add(Citation(citing_work_id=seed_b.id, cited_work_id=ref_2.id, source="openalex"))
    db_session.add(Citation(citing_work_id=citer_1.id, cited_work_id=seed_a.id, source="openalex"))
    db_session.add(Citation(citing_work_id=citer_2.id, cited_work_id=seed_a.id, source="openalex"))
    db_session.add(Citation(citing_work_id=citer_2.id, cited_work_id=seed_b.id, source="openalex"))
    db_session.commit()

    return {
        "project": project,
        "tl1": tl1,
        "tl2": tl2,
        "seed_a": seed_a,
        "seed_b": seed_b,
        "ref_1": ref_1,
        "ref_2": ref_2,
        "citer_1": citer_1,
        "citer_2": citer_2,
        "venue_top": venue_top,
        "venue_other": venue_other,
    }


def test_timeline_response_shape(client, timeline_data):
    pid = timeline_data["project"].id
    resp = client.get(f"/api/projects/{pid}/timeline")
    assert resp.status_code == 200
    data = resp.json()

    assert "seeds" in data
    assert "neighbors" in data
    assert "topic_lists" in data
    assert "tier1_venue_ids" in data
    assert "ignored_venue_ids" in data
    assert "seed_citations" in data


def test_timeline_seeds(client, timeline_data):
    pid = timeline_data["project"].id
    resp = client.get(f"/api/projects/{pid}/timeline")
    data = resp.json()

    seed_ids = {s["id"] for s in data["seeds"]}
    assert seed_ids == {timeline_data["seed_a"].id, timeline_data["seed_b"].id}

    # Seed A should be in topic list 1
    seed_a = next(s for s in data["seeds"] if s["id"] == timeline_data["seed_a"].id)
    assert seed_a["topic_list_ids"] == [timeline_data["tl1"].id]
    assert seed_a["has_backward_citations"] is True
    assert seed_a["has_forward_citations"] is True

    # Seed B should be in topic list 2
    seed_b = next(s for s in data["seeds"] if s["id"] == timeline_data["seed_b"].id)
    assert seed_b["topic_list_ids"] == [timeline_data["tl2"].id]


def test_timeline_neighbors(client, timeline_data):
    pid = timeline_data["project"].id
    resp = client.get(f"/api/projects/{pid}/timeline")
    data = resp.json()

    backward = [n for n in data["neighbors"] if n["direction"] == "backward"]
    forward = [n for n in data["neighbors"] if n["direction"] == "forward"]

    bwd_ids = {n["id"] for n in backward}
    fwd_ids = {n["id"] for n in forward}

    # ref_1 and ref_2 are backward neighbors (not seeds)
    assert timeline_data["ref_1"].id in bwd_ids
    assert timeline_data["ref_2"].id in bwd_ids

    # citer_1 and citer_2 are forward neighbors
    assert timeline_data["citer_1"].id in fwd_ids
    assert timeline_data["citer_2"].id in fwd_ids

    # Seeds should NOT be in neighbors
    seed_ids = {timeline_data["seed_a"].id, timeline_data["seed_b"].id}
    assert not (bwd_ids & seed_ids)
    assert not (fwd_ids & seed_ids)


def test_timeline_seed_citations(client, timeline_data):
    pid = timeline_data["project"].id
    resp = client.get(f"/api/projects/{pid}/timeline")
    data = resp.json()

    # seed_a cites seed_b — should appear in seed_citations
    sc = data["seed_citations"]
    assert len(sc) == 1
    assert sc[0]["citing_seed_id"] == timeline_data["seed_a"].id
    assert sc[0]["cited_seed_id"] == timeline_data["seed_b"].id


def test_timeline_connected_seed_ids(client, timeline_data):
    pid = timeline_data["project"].id
    resp = client.get(f"/api/projects/{pid}/timeline")
    data = resp.json()

    # citer_2 cites both seed_a and seed_b
    fwd = [n for n in data["neighbors"] if n["direction"] == "forward"]
    citer_2 = next(n for n in fwd if n["id"] == timeline_data["citer_2"].id)
    assert set(citer_2["connected_seed_ids"]) == {
        timeline_data["seed_a"].id,
        timeline_data["seed_b"].id,
    }


def test_timeline_tier1_venue_ids(client, timeline_data):
    """venue_top has tier=1, so it should appear in tier1_venue_ids."""
    pid = timeline_data["project"].id
    resp = client.get(f"/api/projects/{pid}/timeline")
    data = resp.json()
    assert timeline_data["venue_top"].id in data["tier1_venue_ids"]


def test_timeline_ignored_venue_ids(client, timeline_data, db_session):
    """Mark venue_other as ignored (tier=3) and verify it appears in ignored list."""
    pid = timeline_data["project"].id
    venue_other = timeline_data["venue_other"]

    venue_other.tier = 3
    db_session.commit()

    resp = client.get(f"/api/projects/{pid}/timeline")
    data = resp.json()
    assert venue_other.id in data["ignored_venue_ids"]


def test_timeline_topic_lists(client, timeline_data):
    pid = timeline_data["project"].id
    resp = client.get(f"/api/projects/{pid}/timeline")
    data = resp.json()

    tl_ids = {tl["id"] for tl in data["topic_lists"]}
    assert tl_ids == {timeline_data["tl1"].id, timeline_data["tl2"].id}


def test_timeline_404_for_missing_project(client):
    resp = client.get("/api/projects/9999/timeline")
    assert resp.status_code == 404


def test_timeline_empty_project(client, db_session):
    project = Project(name="Empty")
    db_session.add(project)
    db_session.commit()

    resp = client.get(f"/api/projects/{project.id}/timeline")
    assert resp.status_code == 200
    data = resp.json()
    assert data["seeds"] == []
    assert data["neighbors"] == []
    assert data["seed_citations"] == []
