"""Tests for per-project venue tier overrides.

Covers:
- CRUD: create, read, update, delete (reset to global)
- Effective tier resolution: with override vs without
- Global tier change does NOT affect existing local override
- Venue list scoped to project-relevant venues
"""

from litexplorer.models.library import Citation, Venue, Work
from litexplorer.models.project import Project, ProjectVenueTier, TopicList, TopicListWork
from litexplorer.services.venue_tiers import bulk_resolve_venue_tiers, resolve_venue_tier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_venue(db, name: str = "Test Venue", tier: int = 2) -> Venue:
    v = Venue(name=name, tier=tier)
    db.add(v)
    db.flush()
    return v


def _make_work(db, title: str = "A Paper", venue: Venue | None = None) -> Work:
    w = Work(
        title=title,
        venue_id=venue.id if venue else None,
    )
    db.add(w)
    db.flush()
    return w


def _make_project(db, name: str = "My Project") -> Project:
    p = Project(name=name)
    db.add(p)
    db.flush()
    return p


def _make_topic_list(db, project: Project, name: str = "TL1") -> TopicList:
    tl = TopicList(project_id=project.id, name=name, color="#3b82f6")
    db.add(tl)
    db.flush()
    return tl


def _add_seed(db, topic_list: TopicList, work: Work) -> TopicListWork:
    assoc = TopicListWork(topic_list_id=topic_list.id, work_id=work.id)
    db.add(assoc)
    db.flush()
    return assoc


def _make_citation(db, citing: Work, cited: Work) -> Citation:
    c = Citation(citing_work_id=citing.id, cited_work_id=cited.id, source="openalex")
    db.add(c)
    db.flush()
    return c


# ---------------------------------------------------------------------------
# Service-layer unit tests
# ---------------------------------------------------------------------------

class TestResolveVenueTier:
    def test_no_override_returns_global(self, db_session):
        venue = _make_venue(db_session, tier=1)
        project = _make_project(db_session)
        result = resolve_venue_tier(project.id, venue.id, db_session)
        assert result == 1

    def test_with_override_returns_local(self, db_session):
        venue = _make_venue(db_session, tier=2)
        project = _make_project(db_session)
        override = ProjectVenueTier(project_id=project.id, venue_id=venue.id, tier=1)
        db_session.add(override)
        db_session.flush()

        result = resolve_venue_tier(project.id, venue.id, db_session)
        assert result == 1

    def test_override_does_not_affect_other_project(self, db_session):
        venue = _make_venue(db_session, tier=2)
        proj_a = _make_project(db_session, "Project A")
        proj_b = _make_project(db_session, "Project B")
        # Override only in proj_a
        db_session.add(ProjectVenueTier(project_id=proj_a.id, venue_id=venue.id, tier=3))
        db_session.flush()

        assert resolve_venue_tier(proj_a.id, venue.id, db_session) == 3
        assert resolve_venue_tier(proj_b.id, venue.id, db_session) == 2

    def test_global_tier_change_does_not_affect_local_override(self, db_session):
        venue = _make_venue(db_session, tier=2)
        project = _make_project(db_session)
        db_session.add(ProjectVenueTier(project_id=project.id, venue_id=venue.id, tier=1))
        db_session.flush()

        # Change the global tier
        venue.tier = 3
        db_session.flush()

        # Local override still in effect
        result = resolve_venue_tier(project.id, venue.id, db_session)
        assert result == 1

    def test_unknown_venue_defaults_to_2(self, db_session):
        project = _make_project(db_session)
        result = resolve_venue_tier(project.id, 99999, db_session)
        assert result == 2


class TestBulkResolveVenueTiers:
    def test_empty_input(self, db_session):
        project = _make_project(db_session)
        result = bulk_resolve_venue_tiers(project.id, set(), db_session)
        assert result == {}

    def test_all_global(self, db_session):
        v1 = _make_venue(db_session, "V1", tier=1)
        v2 = _make_venue(db_session, "V2", tier=3)
        project = _make_project(db_session)

        result = bulk_resolve_venue_tiers(project.id, {v1.id, v2.id}, db_session)
        assert result[v1.id] == 1
        assert result[v2.id] == 3

    def test_mixed_local_and_global(self, db_session):
        v1 = _make_venue(db_session, "V1", tier=2)
        v2 = _make_venue(db_session, "V2", tier=2)
        project = _make_project(db_session)
        db_session.add(ProjectVenueTier(project_id=project.id, venue_id=v1.id, tier=1))
        db_session.flush()

        result = bulk_resolve_venue_tiers(project.id, {v1.id, v2.id}, db_session)
        assert result[v1.id] == 1   # local override
        assert result[v2.id] == 2   # global


# ---------------------------------------------------------------------------
# API-level tests
# ---------------------------------------------------------------------------

class TestProjectVenueTiersAPI:

    def _setup_project_with_seed(self, db_session):
        """Create a project with one seed work that has a venue."""
        venue = _make_venue(db_session, "NeurIPS", tier=2)
        work = _make_work(db_session, "Deep Learning Paper", venue)
        project = _make_project(db_session)
        tl = _make_topic_list(db_session, project)
        _add_seed(db_session, tl, work)
        db_session.commit()
        return project, venue, work

    def test_list_empty_when_no_seeds(self, client, db_session):
        project = _make_project(db_session)
        db_session.commit()

        resp = client.get(f"/api/projects/{project.id}/venue-tiers")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_returns_seed_venues(self, client, db_session):
        project, venue, work = self._setup_project_with_seed(db_session)

        resp = client.get(f"/api/projects/{project.id}/venue-tiers")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["venue_id"] == venue.id
        assert data[0]["venue_name"] == "NeurIPS"
        assert data[0]["global_tier"] == 2
        assert data[0]["local_tier"] is None
        assert data[0]["effective_tier"] == 2

    def test_list_returns_neighbor_venues(self, client, db_session):
        """Venues of citation neighbors should also appear."""
        venue_seed = _make_venue(db_session, "NeurIPS", tier=2)
        venue_neighbor = _make_venue(db_session, "ICML", tier=1)
        work_seed = _make_work(db_session, "Seed", venue_seed)
        work_neighbor = _make_work(db_session, "Neighbor", venue_neighbor)
        project = _make_project(db_session)
        tl = _make_topic_list(db_session, project)
        _add_seed(db_session, tl, work_seed)
        _make_citation(db_session, work_seed, work_neighbor)
        db_session.commit()

        resp = client.get(f"/api/projects/{project.id}/venue-tiers")
        assert resp.status_code == 200
        venue_ids = {v["venue_id"] for v in resp.json()}
        assert venue_seed.id in venue_ids
        assert venue_neighbor.id in venue_ids

    def test_list_venue_from_other_project_not_included(self, client, db_session):
        """Venues from a different project should not appear."""
        venue_a = _make_venue(db_session, "ICML", tier=1)
        venue_b = _make_venue(db_session, "CVPR", tier=2)
        work_a = _make_work(db_session, "Work A", venue_a)
        work_b = _make_work(db_session, "Work B", venue_b)
        proj_a = _make_project(db_session, "Project A")
        proj_b = _make_project(db_session, "Project B")
        tl_a = _make_topic_list(db_session, proj_a)
        tl_b = _make_topic_list(db_session, proj_b)
        _add_seed(db_session, tl_a, work_a)
        _add_seed(db_session, tl_b, work_b)
        db_session.commit()

        resp = client.get(f"/api/projects/{proj_a.id}/venue-tiers")
        venue_ids = {v["venue_id"] for v in resp.json()}
        assert venue_a.id in venue_ids
        assert venue_b.id not in venue_ids

    def test_put_creates_override(self, client, db_session):
        project, venue, _ = self._setup_project_with_seed(db_session)

        resp = client.put(
            f"/api/projects/{project.id}/venue-tiers/{venue.id}",
            json={"tier": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["local_tier"] == 1
        assert data["effective_tier"] == 1
        assert data["global_tier"] == 2

    def test_put_updates_existing_override(self, client, db_session):
        project, venue, _ = self._setup_project_with_seed(db_session)

        client.put(f"/api/projects/{project.id}/venue-tiers/{venue.id}", json={"tier": 1})
        resp = client.put(f"/api/projects/{project.id}/venue-tiers/{venue.id}", json={"tier": 3})
        assert resp.status_code == 200
        assert resp.json()["local_tier"] == 3
        assert resp.json()["effective_tier"] == 3

    def test_put_nonexistent_venue_returns_404(self, client, db_session):
        project = _make_project(db_session)
        db_session.commit()

        resp = client.put(
            f"/api/projects/{project.id}/venue-tiers/99999",
            json={"tier": 1},
        )
        assert resp.status_code == 404

    def test_delete_removes_override(self, client, db_session):
        project, venue, _ = self._setup_project_with_seed(db_session)
        client.put(f"/api/projects/{project.id}/venue-tiers/{venue.id}", json={"tier": 1})

        resp = client.delete(f"/api/projects/{project.id}/venue-tiers/{venue.id}")
        assert resp.status_code == 204

        # Verify override is gone — list should show global tier
        list_resp = client.get(f"/api/projects/{project.id}/venue-tiers")
        row = next(v for v in list_resp.json() if v["venue_id"] == venue.id)
        assert row["local_tier"] is None
        assert row["effective_tier"] == 2

    def test_delete_nonexistent_override_returns_404(self, client, db_session):
        project, venue, _ = self._setup_project_with_seed(db_session)

        resp = client.delete(f"/api/projects/{project.id}/venue-tiers/{venue.id}")
        assert resp.status_code == 404

    def test_global_tier_change_does_not_affect_local_override_via_api(self, client, db_session):
        project, venue, _ = self._setup_project_with_seed(db_session)

        # Set local override to tier 1
        client.put(f"/api/projects/{project.id}/venue-tiers/{venue.id}", json={"tier": 1})

        # Change global tier
        venue.tier = 3
        db_session.commit()

        list_resp = client.get(f"/api/projects/{project.id}/venue-tiers")
        row = next(v for v in list_resp.json() if v["venue_id"] == venue.id)
        assert row["local_tier"] == 1      # override unchanged
        assert row["effective_tier"] == 1  # still 1
        assert row["global_tier"] == 3     # global updated

    def test_override_isolated_to_project(self, client, db_session):
        """Override in project A must not affect project B's effective tier."""
        venue = _make_venue(db_session, "ICLR", tier=2)
        work = _make_work(db_session, "Paper", venue)
        proj_a = _make_project(db_session, "A")
        proj_b = _make_project(db_session, "B")
        tl_a = _make_topic_list(db_session, proj_a)
        tl_b = _make_topic_list(db_session, proj_b)
        _add_seed(db_session, tl_a, work)
        _add_seed(db_session, tl_b, work)
        db_session.commit()

        client.put(f"/api/projects/{proj_a.id}/venue-tiers/{venue.id}", json={"tier": 3})

        resp_b = client.get(f"/api/projects/{proj_b.id}/venue-tiers")
        row = next(v for v in resp_b.json() if v["venue_id"] == venue.id)
        assert row["effective_tier"] == 2  # global, not overridden by A
        assert row["local_tier"] is None

    def test_bulk_delete_removes_all_overrides(self, client, db_session):
        """DELETE /venue-tiers removes all overrides and returns correct count."""
        venue1 = _make_venue(db_session, "NeurIPS", tier=2)
        venue2 = _make_venue(db_session, "ICML", tier=1)
        venue3 = _make_venue(db_session, "CVPR", tier=2)
        work1 = _make_work(db_session, "Paper 1", venue1)
        work2 = _make_work(db_session, "Paper 2", venue2)
        work3 = _make_work(db_session, "Paper 3", venue3)
        project = _make_project(db_session)
        tl = _make_topic_list(db_session, project)
        _add_seed(db_session, tl, work1)
        _add_seed(db_session, tl, work2)
        _add_seed(db_session, tl, work3)
        db_session.add(ProjectVenueTier(project_id=project.id, venue_id=venue1.id, tier=1))
        db_session.add(ProjectVenueTier(project_id=project.id, venue_id=venue2.id, tier=3))
        db_session.add(ProjectVenueTier(project_id=project.id, venue_id=venue3.id, tier=3))
        db_session.commit()

        resp = client.delete(f"/api/projects/{project.id}/venue-tiers")
        assert resp.status_code == 200
        assert resp.json() == {"deleted_count": 3}

        # Verify all overrides are gone
        remaining = db_session.query(ProjectVenueTier).filter_by(
            project_id=project.id
        ).all()
        assert remaining == []

        # List endpoint should show all venues as global
        list_resp = client.get(f"/api/projects/{project.id}/venue-tiers")
        for row in list_resp.json():
            assert row["local_tier"] is None

    def test_bulk_delete_no_overrides_returns_zero(self, client, db_session):
        """Bulk delete on a project with no overrides returns deleted_count=0, not an error."""
        project, _, _ = self._setup_project_with_seed(db_session)

        resp = client.delete(f"/api/projects/{project.id}/venue-tiers")
        assert resp.status_code == 200
        assert resp.json() == {"deleted_count": 0}

    def test_bulk_delete_only_affects_target_project(self, client, db_session):
        """Bulk delete must not remove overrides belonging to other projects."""
        venue = _make_venue(db_session, "ICLR", tier=2)
        work = _make_work(db_session, "Shared Paper", venue)
        proj_a = _make_project(db_session, "A")
        proj_b = _make_project(db_session, "B")
        tl_a = _make_topic_list(db_session, proj_a)
        tl_b = _make_topic_list(db_session, proj_b)
        _add_seed(db_session, tl_a, work)
        _add_seed(db_session, tl_b, work)
        db_session.add(ProjectVenueTier(project_id=proj_a.id, venue_id=venue.id, tier=1))
        db_session.add(ProjectVenueTier(project_id=proj_b.id, venue_id=venue.id, tier=3))
        db_session.commit()

        resp = client.delete(f"/api/projects/{proj_a.id}/venue-tiers")
        assert resp.status_code == 200
        assert resp.json() == {"deleted_count": 1}

        # proj_b's override must still exist
        remaining = db_session.query(ProjectVenueTier).filter_by(
            project_id=proj_b.id
        ).all()
        assert len(remaining) == 1
        assert remaining[0].tier == 3

    def test_bulk_delete_nonexistent_project_returns_404(self, client, db_session):
        resp = client.delete("/api/projects/99999/venue-tiers")
        assert resp.status_code == 404

    def test_project_deletion_cascades_overrides(self, client, db_session):
        project, venue, _ = self._setup_project_with_seed(db_session)
        db_session.add(
            ProjectVenueTier(project_id=project.id, venue_id=venue.id, tier=1)
        )
        db_session.commit()

        resp = client.delete(f"/api/projects/{project.id}")
        assert resp.status_code == 204

        remaining = db_session.query(ProjectVenueTier).filter_by(
            project_id=project.id
        ).all()
        assert remaining == []


class TestTimelineUsesProjectTiers:
    def test_timeline_reflects_local_override(self, client, db_session):
        """tier1_venue_ids / ignored_venue_ids must use the project-local effective tier."""
        venue = _make_venue(db_session, "ICML", tier=2)  # global = regular
        work = _make_work(db_session, "Top Paper", venue)
        project = _make_project(db_session)
        tl = _make_topic_list(db_session, project)
        _add_seed(db_session, tl, work)
        db_session.commit()

        # Without override: venue is tier 2 → not in tier1_venue_ids
        resp = client.get(f"/api/projects/{project.id}/timeline")
        assert resp.status_code == 200
        data = resp.json()
        assert venue.id not in data["tier1_venue_ids"]

        # Set local override to tier 1
        client.put(f"/api/projects/{project.id}/venue-tiers/{venue.id}", json={"tier": 1})

        resp2 = client.get(f"/api/projects/{project.id}/timeline")
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert venue.id in data2["tier1_venue_ids"]

    def test_timeline_ignored_venue_uses_project_tier(self, client, db_session):
        venue = _make_venue(db_session, "Workshop", tier=2)
        work = _make_work(db_session, "Workshop Paper", venue)
        project = _make_project(db_session)
        tl = _make_topic_list(db_session, project)
        _add_seed(db_session, tl, work)
        db_session.commit()

        # Set local tier to 3 (ignore)
        client.put(f"/api/projects/{project.id}/venue-tiers/{venue.id}", json={"tier": 3})

        resp = client.get(f"/api/projects/{project.id}/timeline")
        assert resp.status_code == 200
        assert venue.id in resp.json()["ignored_venue_ids"]

        # Reset to global (tier 2) → no longer in ignored
        client.delete(f"/api/projects/{project.id}/venue-tiers/{venue.id}")
        resp2 = client.get(f"/api/projects/{project.id}/timeline")
        assert venue.id not in resp2.json()["ignored_venue_ids"]
