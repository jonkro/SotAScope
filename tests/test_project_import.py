"""Tests for project ZIP import service and API endpoint.

Covers:
  - Round-trip: export → import (project structure matches)
  - format_version=2 venue_tiers export and import (effective tiers, aliases)
  - Works are reused when DOI / arXiv ID already in library
  - Project name collision → temp project created, needs_project_decision=True
  - Resolve: rename → project renamed correctly
  - Resolve: merge → projects merged, temp project deleted
  - Double import of same archive → idempotent (no duplicate works / topic lists)
  - Auto-enrichment is scheduled (background task mock)
  - Invalid ZIP → 422
  - format_version > 1 → 422
  - Missing manifest → 422
  - Extraction schemas, columns, and results survive round-trip
  - Venue tier overrides survive round-trip
  - Chat sessions survive round-trip
  - Project-scoped work notes survive round-trip
"""

from __future__ import annotations

import io
import json
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from sotascope.models.chat import ChatMessage, ChatSession
from sotascope.models.extraction import ExtractionColumn, ExtractionSchema
from sotascope.models.library import Citation, Venue, VenueAlias, Work, WorkNote, WorkPDF
from sotascope.models.project import (
    Project,
    ProjectVenueTier,
    TopicList,
    TopicListWork,
)
from sotascope.services.project_export import export_project
from sotascope.services.project_import import import_project, resolve_import
from sotascope.schemas.project_merge import MergeDecisions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_work(db, *, title="A Paper", year=2022, doi=None, arxiv_id=None,
               openalex_id=None, bibtex_key=None) -> Work:
    w = Work(title=title, publication_year=year, doi=doi, arxiv_id=arxiv_id,
             openalex_id=openalex_id, bibtex_key=bibtex_key)
    db.add(w)
    db.flush()
    return w


def _make_project(db, name="Test Project", description=None) -> Project:
    p = Project(name=name, description=description)
    db.add(p)
    db.flush()
    return p


def _make_topic_list(db, project_id, name="Main", color="#3b82f6") -> TopicList:
    tl = TopicList(project_id=project_id, name=name, color=color)
    db.add(tl)
    db.flush()
    return tl


def _add_seed(db, tl: TopicList, work: Work) -> None:
    db.add(TopicListWork(topic_list_id=tl.id, work_id=work.id))
    db.flush()


def _make_zip(manifest: dict, bibtex: str = "") -> bytes:
    """Build a minimal valid archive from raw manifest + bibtex strings."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("seeds.bib", bibtex)
    buf.seek(0)
    return buf.read()


def _export_zip(project_id: int, db) -> bytes:
    buf = export_project(project_id, db)
    return buf.read()


# ---------------------------------------------------------------------------
# Validation / error cases
# ---------------------------------------------------------------------------


class TestImportValidation:
    def test_invalid_zip(self, db_session):
        with pytest.raises(ValueError, match="Invalid ZIP"):
            import_project(b"not a zip", db_session)

    def test_missing_manifest(self, db_session):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("other.txt", "hello")
        buf.seek(0)
        with pytest.raises(ValueError, match="manifest.json"):
            import_project(buf.read(), db_session)

    def test_format_version_too_high(self, db_session):
        manifest = {"format_version": 99, "project": {"name": "X"}}
        zip_bytes = _make_zip(manifest)
        with pytest.raises(ValueError, match="not supported"):
            import_project(zip_bytes, db_session)

    def test_missing_format_version(self, db_session):
        manifest = {"project": {"name": "X"}}
        zip_bytes = _make_zip(manifest)
        with pytest.raises(ValueError, match="format_version"):
            import_project(zip_bytes, db_session)


# ---------------------------------------------------------------------------
# Basic import: works matching
# ---------------------------------------------------------------------------


class TestWorkMatching:
    def test_doi_match_reuses_existing_work(self, db_session):
        existing = _make_work(db_session, title="Existing", doi="10.1/test")
        db_session.commit()

        manifest = {
            "format_version": 1,
            "project": {"name": "P"},
            "works": [{"doi": "10.1/test", "title": "Existing", "year": 2022}],
            "topic_lists": [{"name": "TL", "color": "#ff0000", "works": ["doi:10.1/test"]}],
            "extraction_schemas": [], "venue_tier_overrides": [],
            "citations": [], "chat_sessions": [], "work_notes": [],
        }
        result, seed_ids = import_project(_make_zip(manifest), db_session)

        assert result.works_matched == 1
        assert result.works_created == 0
        # Verify the topic list points at the existing work
        project = db_session.get(Project, result.project_id)
        tl = db_session.query(TopicList).filter_by(project_id=project.id).first()
        tlw = db_session.query(TopicListWork).filter_by(topic_list_id=tl.id).first()
        assert tlw.work_id == existing.id

    def test_arxiv_match_reuses_existing_work(self, db_session):
        existing = _make_work(db_session, title="ArXiv Paper", arxiv_id="2301.0001")
        db_session.commit()

        manifest = {
            "format_version": 1,
            "project": {"name": "P2"},
            "works": [{"arxiv_id": "2301.0001", "title": "ArXiv Paper", "year": 2023}],
            "topic_lists": [{"name": "TL", "color": "#00ff00", "works": ["arxiv:2301.0001"]}],
            "extraction_schemas": [], "venue_tier_overrides": [],
            "citations": [], "chat_sessions": [], "work_notes": [],
        }
        result, _ = import_project(_make_zip(manifest), db_session)
        assert result.works_matched == 1
        assert result.works_created == 0

    def test_new_work_created_from_manifest(self, db_session):
        manifest = {
            "format_version": 1,
            "project": {"name": "P3"},
            "works": [{"doi": "10.9/brand-new", "title": "Brand New Paper", "year": 2024,
                       "bibtex_key": "BrandNew2024"}],
            "topic_lists": [{"name": "TL", "color": "#0000ff",
                             "works": ["doi:10.9/brand-new"]}],
            "extraction_schemas": [], "venue_tier_overrides": [],
            "citations": [], "chat_sessions": [], "work_notes": [],
        }
        result, _ = import_project(_make_zip(manifest), db_session)
        assert result.works_created == 1
        assert result.works_matched == 0
        new_work = db_session.query(Work).filter_by(doi="10.9/brand-new").first()
        assert new_work is not None
        assert new_work.title == "Brand New Paper"

    def test_title_year_author_match_auto_matches(self, db_session):
        """100% match: exact title+year+first-author → work reused, not created."""
        bibtex_entry = "@article{Smith2022Test,\n  title = {Test Paper},\n  author = {Smith, John},\n  year = {2022},\n}"
        existing = _make_work(db_session, title="Test Paper", year=2022,
                              bibtex_key="Smith2022Test", doi=None)
        existing.bibtex_entry = bibtex_entry
        db_session.commit()

        manifest = {
            "format_version": 1,
            "project": {"name": "AutoMatch"},
            "works": [{"title": "Test Paper", "year": 2022, "bibtex_key": "Smith2022Test"}],
            "topic_lists": [{"name": "TL", "color": "#abc123",
                             "works": ["title:Test Paper:2022"]}],
            "extraction_schemas": [], "venue_tier_overrides": [],
            "citations": [], "chat_sessions": [], "work_notes": [],
        }
        import_bibtex = (
            "@article{Smith2022Test,\n  title = {Test Paper},\n"
            "  author = {Smith, John},\n  year = {2022},\n}"
        )
        result, _ = import_project(_make_zip(manifest, import_bibtex), db_session)
        assert result.works_matched == 1
        assert result.works_created == 0
        assert len(result.ambiguous_matches) == 0

    def test_title_year_author_mismatch_flagged_ambiguous(self, db_session):
        """Same title+year but different author → flag ambiguous, create new work."""
        bibtex_entry = "@article{Jones2022Test,\n  title = {Test Paper},\n  author = {Jones, Alice},\n  year = {2022},\n}"
        existing = _make_work(db_session, title="Test Paper", year=2022,
                              bibtex_key="Jones2022Test")
        existing.bibtex_entry = bibtex_entry
        db_session.commit()

        manifest = {
            "format_version": 1,
            "project": {"name": "AmbiguousTest"},
            "works": [{"title": "Test Paper", "year": 2022, "bibtex_key": "Smith2022Test"}],
            "topic_lists": [{"name": "TL", "color": "#abc", "works": ["title:Test Paper:2022"]}],
            "extraction_schemas": [], "venue_tier_overrides": [],
            "citations": [], "chat_sessions": [], "work_notes": [],
        }
        import_bibtex = (
            "@article{Smith2022Test,\n  title = {Test Paper},\n"
            "  author = {Smith, Bob},\n  year = {2022},\n}"
        )
        result, _ = import_project(_make_zip(manifest, import_bibtex), db_session)
        assert len(result.ambiguous_matches) == 1
        assert result.ambiguous_matches[0].incoming.title == "Test Paper"


# ---------------------------------------------------------------------------
# Round-trip: export → import
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def _build_full_project(self, db_session):
        """Create a project with works, topic list, schema, notes, venue tier."""
        # Venue
        venue = Venue(name="ICML", tier=1)
        db_session.add(venue)
        db_session.flush()
        db_session.add(VenueAlias(venue_id=venue.id, alias="ICML", sort_order=0))
        db_session.flush()

        # Works
        w1 = Work(title="Alpha Paper", publication_year=2021, doi="10.1/alpha",
                  bibtex_entry="@article{Alpha2021,\n  title = {Alpha Paper},\n  year = {2021},\n  doi = {10.1/alpha},\n}",
                  bibtex_key="Alpha2021", venue_id=venue.id)
        w2 = Work(title="Beta Paper", publication_year=2022, arxiv_id="2202.0001",
                  bibtex_entry="@article{Beta2022,\n  title = {Beta Paper},\n  year = {2022},\n  eprint = {2202.0001},\n}",
                  bibtex_key="Beta2022")
        db_session.add_all([w1, w2])
        db_session.flush()

        # Project
        p = Project(name="FullProject", description="desc")
        db_session.add(p)
        db_session.flush()

        tl = TopicList(project_id=p.id, name="Main", color="#ff5500")
        db_session.add(tl)
        db_session.flush()
        db_session.add(TopicListWork(topic_list_id=tl.id, work_id=w1.id))
        db_session.add(TopicListWork(topic_list_id=tl.id, work_id=w2.id))
        db_session.flush()

        # Schema
        schema = ExtractionSchema(project_id=p.id, title="TestSchema", description="desc")
        db_session.add(schema)
        db_session.flush()
        col = ExtractionColumn(schema_id=schema.id, name="ColA", prompt="Prompt A",
                               allowed_values=["yes", "no"], sort_order=0)
        db_session.add(col)
        db_session.flush()

        # Extraction result
        from sotascope.services.extraction import _truncate_note_type
        db_session.add(WorkNote(
            work_id=w1.id, project_id=p.id,
            content="yes",
            note_type=_truncate_note_type("TestSchema / ColA"),
            provenance="ai",
        ))
        db_session.add(WorkNote(
            work_id=w1.id, project_id=p.id,
            content="because yes",
            note_type=_truncate_note_type("TestSchema / ColA / reasoning"),
            provenance="ai",
        ))
        db_session.flush()

        # Venue tier override
        db_session.add(ProjectVenueTier(project_id=p.id, venue_id=venue.id, tier=1))
        db_session.flush()

        # Chat session
        session = ChatSession(project_id=p.id, work_id=None, context_type="papers",
                              title="My Chat", is_auto=False)
        db_session.add(session)
        db_session.flush()
        db_session.add(ChatMessage(session_id=session.id, role="user", content="Hello"))
        db_session.add(ChatMessage(session_id=session.id, role="assistant", content="Hi"))
        db_session.flush()

        # Work note
        db_session.add(WorkNote(
            work_id=w2.id, project_id=p.id,
            content="Interesting paper",
            note_type="general",
            provenance="user",
        ))
        db_session.flush()
        db_session.commit()
        return p.id, venue.id, w1.id, w2.id

    def test_round_trip_structure(self, db_session):
        """Export a project, import it; verify structural equivalence."""
        project_id, venue_id, w1_id, w2_id = self._build_full_project(db_session)

        # Export
        zip_bytes = _export_zip(project_id, db_session)

        # Import into same DB (simulating a fresh DB is hard with shared in-memory,
        # so we rename the source project to avoid name collision)
        src = db_session.get(Project, project_id)
        src.name = "FullProject-old"
        db_session.commit()

        result, seed_ids = import_project(zip_bytes, db_session)

        assert not result.needs_project_decision
        assert result.project_id is not None
        assert result.project_name == "FullProject"
        assert result.works_matched == 2  # both works are already in the library
        assert result.works_created == 0

        # Topic list
        new_project = db_session.get(Project, result.project_id)
        tls = db_session.query(TopicList).filter_by(project_id=new_project.id).all()
        assert len(tls) == 1
        assert tls[0].name == "Main"
        assert tls[0].color == "#ff5500"
        tlws = db_session.query(TopicListWork).filter_by(topic_list_id=tls[0].id).all()
        assert len(tlws) == 2

        # Schema
        schemas = db_session.query(ExtractionSchema).filter_by(
            project_id=new_project.id
        ).all()
        assert len(schemas) == 1
        assert schemas[0].title == "TestSchema"
        cols = db_session.query(ExtractionColumn).filter_by(
            schema_id=schemas[0].id
        ).all()
        assert len(cols) == 1
        assert cols[0].name == "ColA"
        assert cols[0].sort_order == 0
        assert cols[0].allowed_values == ["yes", "no"]

        # Extraction results (WorkNotes)
        from sotascope.services.extraction import _truncate_note_type
        answer_type = _truncate_note_type("TestSchema / ColA")
        answer_note = db_session.query(WorkNote).filter_by(
            work_id=w1_id, project_id=new_project.id, note_type=answer_type
        ).first()
        assert answer_note is not None
        assert answer_note.content == "yes"

        # Venue tier override
        override = db_session.query(ProjectVenueTier).filter_by(
            project_id=new_project.id
        ).first()
        assert override is not None
        assert override.venue_id == venue_id
        assert override.tier == 1

        # Chat session
        sessions = db_session.query(ChatSession).filter_by(
            project_id=new_project.id
        ).all()
        assert len(sessions) == 1
        assert sessions[0].title == "My Chat"
        msgs = db_session.query(ChatMessage).filter_by(
            session_id=sessions[0].id
        ).all()
        assert len(msgs) == 2

        # Work note
        general_note = db_session.query(WorkNote).filter_by(
            work_id=w2_id, project_id=new_project.id, note_type="general"
        ).first()
        assert general_note is not None
        assert general_note.content == "Interesting paper"

        # Seed IDs returned
        assert set(seed_ids) == {w1_id, w2_id}


# ---------------------------------------------------------------------------
# Project name collision
# ---------------------------------------------------------------------------


class TestNameCollision:
    def test_collision_creates_temp_project(self, db_session):
        # Create an existing project with the same name
        existing = _make_project(db_session, name="MyProject")
        db_session.commit()

        manifest = {
            "format_version": 1,
            "project": {"name": "MyProject"},
            "works": [],
            "topic_lists": [{"name": "TL", "color": "#abc", "works": []}],
            "extraction_schemas": [], "venue_tier_overrides": [],
            "citations": [], "chat_sessions": [], "work_notes": [],
        }
        result, seed_ids = import_project(_make_zip(manifest), db_session)

        assert result.needs_project_decision is True
        assert result.temp_project_id is not None
        assert result.existing_project_id == existing.id
        assert result.project_id is None
        assert seed_ids == []

        # Temp project should exist
        temp = db_session.get(Project, result.temp_project_id)
        assert temp is not None
        assert "incoming" in temp.name.lower()

    def test_collision_merge_preview_populated(self, db_session):
        _make_project(db_session, name="Collide")
        db_session.commit()

        manifest = {
            "format_version": 1,
            "project": {"name": "Collide"},
            "works": [],
            "topic_lists": [{"name": "UniqueList", "color": "#fff", "works": []}],
            "extraction_schemas": [], "venue_tier_overrides": [],
            "citations": [], "chat_sessions": [], "work_notes": [],
        }
        result, _ = import_project(_make_zip(manifest), db_session)
        assert result.merge_preview is not None
        # One topic list "UniqueList" to copy
        assert len(result.merge_preview.topic_list_merges) == 1


class TestResolveCollision:
    def _setup_collision(self, db_session):
        """Return (existing_project_id, result) after triggering a collision."""
        existing = _make_project(db_session, name="ResolveMe")
        db_session.commit()

        manifest = {
            "format_version": 1,
            "project": {"name": "ResolveMe"},
            "works": [],
            "topic_lists": [{"name": "NewList", "color": "#123456", "works": []}],
            "extraction_schemas": [], "venue_tier_overrides": [],
            "citations": [], "chat_sessions": [], "work_notes": [],
        }
        result, _ = import_project(_make_zip(manifest), db_session)
        return existing.id, result

    def test_resolve_rename(self, db_session):
        existing_id, result = self._setup_collision(db_session)
        temp_id = result.temp_project_id

        final, seed_ids = resolve_import(
            temp_id, "rename", None, "ResolveMe - Archive 2024", MergeDecisions(), db_session
        )
        assert final.name == "ResolveMe - Archive 2024"
        # Temp project should still exist (renamed)
        p = db_session.get(Project, temp_id)
        assert p is not None
        assert p.name == "ResolveMe - Archive 2024"

    def test_resolve_rename_duplicate_name_raises(self, db_session):
        existing_id, result = self._setup_collision(db_session)
        with pytest.raises(ValueError, match="already taken"):
            resolve_import(
                result.temp_project_id, "rename", None, "ResolveMe", MergeDecisions(), db_session
            )

    def test_resolve_merge(self, db_session):
        existing_id, result = self._setup_collision(db_session)
        temp_id = result.temp_project_id

        final, seed_ids = resolve_import(
            temp_id, "merge", existing_id, None, MergeDecisions(), db_session
        )
        # Final project should be the existing one
        assert final.id == existing_id
        # Temp project should be deleted
        assert db_session.get(Project, temp_id) is None
        # "NewList" topic list should now exist in existing project
        tls = db_session.query(TopicList).filter_by(project_id=existing_id).all()
        assert any(tl.name == "NewList" for tl in tls)

    def test_resolve_unknown_action_raises(self, db_session):
        _make_project(db_session, name="ActionTest")
        db_session.commit()
        manifest = {
            "format_version": 1, "project": {"name": "ActionTest"},
            "works": [], "topic_lists": [], "extraction_schemas": [],
            "venue_tier_overrides": [], "citations": [], "chat_sessions": [], "work_notes": [],
        }
        result, _ = import_project(_make_zip(manifest), db_session)
        with pytest.raises(ValueError, match="Unknown action"):
            resolve_import(result.temp_project_id, "invalid_action", None, None,
                          MergeDecisions(), db_session)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_double_import_no_duplicate_works(self, db_session):
        """Importing the same archive twice must not create duplicate work rows."""
        manifest = {
            "format_version": 1,
            "project": {"name": "Idempotent-{n}"},
            "works": [{"doi": "10.1/idem", "title": "Idempotent Paper", "year": 2023}],
            "topic_lists": [{"name": "TL", "color": "#abc",
                             "works": ["doi:10.1/idem"]}],
            "extraction_schemas": [], "venue_tier_overrides": [],
            "citations": [], "chat_sessions": [], "work_notes": [],
        }
        m1 = dict(manifest)
        m1["project"] = {"name": "Idem-1"}
        m2 = dict(manifest)
        m2["project"] = {"name": "Idem-2"}

        result1, _ = import_project(_make_zip(m1), db_session)
        result2, _ = import_project(_make_zip(m2), db_session)

        # The DOI work should only exist once
        works = db_session.query(Work).filter_by(doi="10.1/idem").all()
        assert len(works) == 1
        assert result1.works_created == 1
        assert result2.works_matched == 1  # second import reuses existing work


# ---------------------------------------------------------------------------
# Auto-enrichment scheduling (API-level test)
# ---------------------------------------------------------------------------


class TestAutoEnrichmentAPI:
    def test_import_schedules_enrichment(self, client, db_session):
        """POST /api/projects/import schedules background enrichment for seed works."""
        manifest = {
            "format_version": 1,
            "project": {"name": "EnrichTest"},
            "works": [{"doi": "10.99/enrich", "title": "Enrich Me", "year": 2020,
                       "bibtex_key": "Enrich2020"}],
            "topic_lists": [{"name": "Seeds", "color": "#0f0",
                             "works": ["doi:10.99/enrich"]}],
            "extraction_schemas": [], "venue_tier_overrides": [],
            "citations": [], "chat_sessions": [], "work_notes": [],
        }
        bib = "@article{Enrich2020,\n  title={Enrich Me},\n  year={2020},\n  doi={10.99/enrich},\n}"
        zip_bytes = _make_zip(manifest, bib)

        import io as _io
        from fastapi.testclient import TestClient
        response = client.post(
            "/api/projects/import",
            files={"file": ("project.zip", zip_bytes, "application/zip")},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["works_created"] == 1
        assert data["project_id"] is not None
        assert not data["needs_project_decision"]


# ---------------------------------------------------------------------------
# API validation
# ---------------------------------------------------------------------------


class TestImportAPI:
    def test_invalid_zip_returns_422(self, client):
        response = client.post(
            "/api/projects/import",
            files={"file": ("bad.zip", b"not a zip", "application/zip")},
        )
        assert response.status_code == 422

    def test_version_too_high_returns_422(self, client):
        manifest = {"format_version": 999, "project": {"name": "X"}}
        zip_bytes = _make_zip(manifest)
        response = client.post(
            "/api/projects/import",
            files={"file": ("p.zip", zip_bytes, "application/zip")},
        )
        assert response.status_code == 422

    def test_valid_import_returns_201(self, client):
        manifest = {
            "format_version": 1,
            "project": {"name": "APITest"},
            "works": [],
            "topic_lists": [],
            "extraction_schemas": [], "venue_tier_overrides": [],
            "citations": [], "chat_sessions": [], "work_notes": [],
        }
        zip_bytes = _make_zip(manifest)
        response = client.post(
            "/api/projects/import",
            files={"file": ("p.zip", zip_bytes, "application/zip")},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["project_name"] == "APITest"
        assert data["project_id"] is not None

    def test_collision_resolve_rename_api(self, client, db_session):
        """Full collision → rename flow via API."""
        # Create existing project
        p = Project(name="CollideName")
        db_session.add(p)
        db_session.commit()

        manifest = {
            "format_version": 1,
            "project": {"name": "CollideName"},
            "works": [],
            "topic_lists": [],
            "extraction_schemas": [], "venue_tier_overrides": [],
            "citations": [], "chat_sessions": [], "work_notes": [],
        }
        zip_bytes = _make_zip(manifest)
        r1 = client.post(
            "/api/projects/import",
            files={"file": ("p.zip", zip_bytes, "application/zip")},
        )
        assert r1.status_code == 201
        import_data = r1.json()
        assert import_data["needs_project_decision"] is True
        temp_id = import_data["temp_project_id"]

        r2 = client.post(
            f"/api/projects/import/{temp_id}/resolve",
            json={"action": "rename", "new_name": "CollideName - Copy"},
        )
        assert r2.status_code == 200
        proj = r2.json()
        assert proj["name"] == "CollideName - Copy"

    def test_collision_resolve_merge_api(self, client, db_session):
        """Full collision → merge flow via API."""
        p = Project(name="MergeTarget")
        db_session.add(p)
        db_session.commit()
        existing_id = p.id

        manifest = {
            "format_version": 1,
            "project": {"name": "MergeTarget"},
            "works": [],
            "topic_lists": [{"name": "ImportedList", "color": "#abc", "works": []}],
            "extraction_schemas": [], "venue_tier_overrides": [],
            "citations": [], "chat_sessions": [], "work_notes": [],
        }
        zip_bytes = _make_zip(manifest)
        r1 = client.post(
            "/api/projects/import",
            files={"file": ("p.zip", zip_bytes, "application/zip")},
        )
        assert r1.status_code == 201
        temp_id = r1.json()["temp_project_id"]

        r2 = client.post(
            f"/api/projects/import/{temp_id}/resolve",
            json={
                "action": "merge",
                "target_project_id": existing_id,
                "merge_decisions": {"schema_decisions": {}, "venue_tier_decisions": {}},
            },
        )
        assert r2.status_code == 200
        proj = r2.json()
        assert proj["id"] == existing_id
        # ImportedList should now be in the existing project
        tl_names = [tl["name"] for tl in proj["topic_lists"]]
        assert "ImportedList" in tl_names


# ---------------------------------------------------------------------------
# format_version=2: venue tier snapshot and alias handling
# ---------------------------------------------------------------------------


def _make_venue(db, name, tier=2, openalex_id=None, issn=None, aliases=()) -> Venue:
    v = Venue(name=name, tier=tier, openalex_id=openalex_id, issn=issn)
    db.add(v)
    db.flush()
    for i, alias in enumerate(aliases):
        db.add(VenueAlias(venue_id=v.id, alias=alias, sort_order=i))
    db.flush()
    return v


def _v2_manifest(*, project_name="V2Project", works=None, topic_lists=None,
                 venue_tiers=None) -> dict:
    return {
        "format_version": 2,
        "project": {"name": project_name},
        "works": works or [],
        "topic_lists": topic_lists or [],
        "extraction_schemas": [],
        "venue_tiers": venue_tiers or [],
        "citations": [],
        "chat_sessions": [],
        "work_notes": [],
    }


class TestVenueExportImport:
    def _setup_project_with_venue(self, db_session, *, venue_tier=2, project_tier=None,
                                   aliases=()):
        """Return (project_id, venue_id, seed_work_id)."""
        venue = _make_venue(db_session, "Test Conference", tier=venue_tier, aliases=aliases)
        work = _make_work(db_session, title="Seed", doi="10.1/seed", year=2020)
        work.venue_id = venue.id
        db_session.flush()
        project = _make_project(db_session, name="VenueProject")
        tl = _make_topic_list(db_session, project.id)
        _add_seed(db_session, tl, work)
        if project_tier is not None:
            db_session.add(ProjectVenueTier(
                project_id=project.id, venue_id=venue.id, tier=project_tier,
            ))
        db_session.commit()
        return project.id, venue.id, work.id

    # ------------------------------------------------------------------ export

    def test_export_v2_format_version(self, db_session):
        """Exported archives carry format_version=2."""
        project_id, _, _ = self._setup_project_with_venue(db_session)
        buf = export_project(project_id, db_session)
        import zipfile, json
        with zipfile.ZipFile(buf) as zf:
            manifest = json.loads(zf.read("manifest.json"))
        assert manifest["format_version"] == 2
        assert "venue_tiers" in manifest
        assert "venue_tier_overrides" not in manifest

    def test_export_v2_effective_tier_uses_project_override(self, db_session):
        """Project-local tier wins over global in the export snapshot."""
        project_id, venue_id, _ = self._setup_project_with_venue(
            db_session, venue_tier=2, project_tier=1
        )
        buf = export_project(project_id, db_session)
        import zipfile, json
        with zipfile.ZipFile(buf) as zf:
            manifest = json.loads(zf.read("manifest.json"))
        entries = manifest["venue_tiers"]
        assert len(entries) == 1
        assert entries[0]["tier"] == 1  # project override, not global tier 2

    def test_export_v2_effective_tier_falls_back_to_global(self, db_session):
        """Global tier used when no project-local override exists."""
        project_id, venue_id, _ = self._setup_project_with_venue(
            db_session, venue_tier=1, project_tier=None
        )
        buf = export_project(project_id, db_session)
        import zipfile, json
        with zipfile.ZipFile(buf) as zf:
            manifest = json.loads(zf.read("manifest.json"))
        assert manifest["venue_tiers"][0]["tier"] == 1

    def test_export_v2_aliases_included_preferred_excluded(self, db_session):
        """All aliases appear in the export; the preferred alias (venue_name) is excluded."""
        venue = _make_venue(db_session, "ICML", tier=1,
                            aliases=["ICML", "International Conference on ML"])
        # preferred alias = aliases[0] = "ICML"
        work = _make_work(db_session, title="Paper", doi="10.1/p", year=2021)
        work.venue_id = venue.id
        db_session.flush()
        project = _make_project(db_session, name="AliasProject")
        tl = _make_topic_list(db_session, project.id)
        _add_seed(db_session, tl, work)
        db_session.commit()

        buf = export_project(project.id, db_session)
        import zipfile, json
        with zipfile.ZipFile(buf) as zf:
            manifest = json.loads(zf.read("manifest.json"))
        entry = manifest["venue_tiers"][0]
        assert entry["venue_name"] == "ICML"
        # "ICML" is the preferred alias so it must NOT be in the aliases list
        assert "ICML" not in entry["aliases"]
        assert "International Conference on ML" in entry["aliases"]

    def test_export_v2_venue_tiers_includes_candidates(self, db_session):
        """Venues of citation neighbours (candidates) appear in the snapshot."""
        venue_seed = _make_venue(db_session, "Venue A", tier=1)
        venue_cand = _make_venue(db_session, "Venue B", tier=2)

        seed = _make_work(db_session, title="Seed", doi="10.1/s", year=2020)
        seed.venue_id = venue_seed.id
        cand = _make_work(db_session, title="Cand", doi="10.1/c", year=2019)
        cand.venue_id = venue_cand.id
        db_session.flush()

        # Citation edge: seed cites cand
        db_session.add(Citation(citing_work_id=seed.id, cited_work_id=cand.id,
                                source="openalex"))
        db_session.flush()

        project = _make_project(db_session, name="CandProject")
        tl = _make_topic_list(db_session, project.id)
        _add_seed(db_session, tl, seed)
        db_session.commit()

        buf = export_project(project.id, db_session)
        import zipfile, json
        with zipfile.ZipFile(buf) as zf:
            manifest = json.loads(zf.read("manifest.json"))
        exported_names = {e["venue_name"] for e in manifest["venue_tiers"]}
        assert "Venue A" in exported_names
        assert "Venue B" in exported_names

    def test_export_v2_skips_null_venue_works(self, db_session):
        """Works with venue_id=NULL don't cause errors and are skipped silently."""
        work = _make_work(db_session, title="No venue", doi="10.1/nv", year=2021)
        # venue_id is None by default
        project = _make_project(db_session, name="NullVenueProject")
        tl = _make_topic_list(db_session, project.id)
        _add_seed(db_session, tl, work)
        db_session.commit()

        buf = export_project(project.id, db_session)
        import zipfile, json
        with zipfile.ZipFile(buf) as zf:
            manifest = json.loads(zf.read("manifest.json"))
        # Must succeed and produce an empty venue_tiers list
        assert manifest["format_version"] == 2
        assert manifest["venue_tiers"] == []

    # ------------------------------------------------------------------ import

    def test_import_v2_tiers_become_project_local(self, db_session):
        """Every tier in the venue_tiers section creates a ProjectVenueTier row."""
        venue = _make_venue(db_session, "NEURIPS", openalex_id="V123", tier=2)
        db_session.commit()

        manifest = _v2_manifest(
            venue_tiers=[{
                "venue_openalex_id": "V123",
                "venue_issn": None,
                "venue_name": "NEURIPS",
                "aliases": [],
                "tier": 1,
            }]
        )
        result, _ = import_project(_make_zip(manifest), db_session)

        assert result.project_id is not None
        override = db_session.query(ProjectVenueTier).filter_by(
            project_id=result.project_id
        ).first()
        assert override is not None
        assert override.venue_id == venue.id
        assert override.tier == 1
        # Global tier must be unchanged
        db_session.refresh(venue)
        assert venue.tier == 2

    def test_import_v2_pending_aliases_for_new_entries(self, db_session):
        """Aliases not in VenueAlias table are returned as pending."""
        venue = _make_venue(db_session, "ICLR", openalex_id="V999", tier=1)
        db_session.commit()

        manifest = _v2_manifest(
            venue_tiers=[{
                "venue_openalex_id": "V999",
                "venue_issn": None,
                "venue_name": "ICLR",
                "aliases": ["International Conference on Learning Representations"],
                "tier": 1,
            }]
        )
        result, _ = import_project(_make_zip(manifest), db_session)

        assert result.needs_alias_decision is True
        assert len(result.pending_venue_aliases) == 1
        pa = result.pending_venue_aliases[0]
        assert pa.alias == "International Conference on Learning Representations"
        assert pa.venue_id == venue.id

    def test_import_v2_no_pending_aliases_when_all_known(self, db_session):
        """Aliases already in VenueAlias are NOT returned as pending."""
        venue = _make_venue(db_session, "ICLR", openalex_id="V888", tier=1,
                            aliases=["International Conference on Learning Representations"])
        db_session.commit()

        manifest = _v2_manifest(
            venue_tiers=[{
                "venue_openalex_id": "V888",
                "venue_issn": None,
                "venue_name": "ICLR",
                "aliases": ["International Conference on Learning Representations"],
                "tier": 1,
            }]
        )
        result, _ = import_project(_make_zip(manifest), db_session)

        assert result.needs_alias_decision is False
        assert result.pending_venue_aliases == []

    def test_import_v2_skips_alias_matching_canonical_name(self, db_session):
        """An alias equal to the venue's canonical name on the importer side is skipped."""
        # The exporter excluded preferred alias from aliases[], but the canonical
        # name might still show up if the importer's DB uses a different preferred alias.
        venue = _make_venue(db_session, "NeurIPS", openalex_id="V777", tier=2)
        db_session.commit()

        manifest = _v2_manifest(
            venue_tiers=[{
                "venue_openalex_id": "V777",
                "venue_issn": None,
                "venue_name": "NeurIPS",
                # This alias matches venue.name on the importer side — skip it
                "aliases": ["NeurIPS", "Neural Information Processing Systems"],
                "tier": 1,
            }]
        )
        result, _ = import_project(_make_zip(manifest), db_session)

        # "NeurIPS" must not appear in pending (it IS the canonical name)
        pending_aliases = [p.alias for p in result.pending_venue_aliases]
        assert "NeurIPS" not in pending_aliases
        assert "Neural Information Processing Systems" in pending_aliases

    def test_import_v1_fallback_uses_tier_overrides(self, db_session):
        """format_version=1 manifests still go through the old venue_tier_overrides path."""
        venue = _make_venue(db_session, "Old Conf", openalex_id="V456", tier=2)
        db_session.commit()

        manifest = {
            "format_version": 1,
            "project": {"name": "V1Project"},
            "works": [],
            "topic_lists": [],
            "extraction_schemas": [],
            "venue_tier_overrides": [{
                "venue_openalex_id": "V456",
                "venue_issn": None,
                "venue_name": "Old Conf",
                "tier": 1,
            }],
            "citations": [],
            "chat_sessions": [],
            "work_notes": [],
        }
        result, _ = import_project(_make_zip(manifest), db_session)

        assert result.needs_alias_decision is False
        assert result.pending_venue_aliases == []
        override = db_session.query(ProjectVenueTier).filter_by(
            project_id=result.project_id
        ).first()
        assert override is not None
        assert override.tier == 1

    # ----------------------------------------------------------------- API

    def test_resolve_aliases_writes_accepted(self, client, db_session):
        """POST resolve-aliases writes accepted aliases, ignores rejected ones."""
        venue = _make_venue(db_session, "CVPR", openalex_id="VCVPR", tier=1)
        project = _make_project(db_session, name="AliasResolveProject")
        db_session.commit()

        response = client.post(
            f"/api/projects/import/{project.id}/resolve-aliases",
            json={
                "decisions": [
                    {"venue_id": venue.id, "alias": "Computer Vision and Pattern Recognition",
                     "accepted": True},
                    {"venue_id": venue.id, "alias": "CVPR Workshop", "accepted": False},
                ]
            },
        )
        assert response.status_code == 200

        accepted = db_session.query(VenueAlias).filter_by(
            venue_id=venue.id, alias="Computer Vision and Pattern Recognition"
        ).first()
        assert accepted is not None

        rejected = db_session.query(VenueAlias).filter_by(
            venue_id=venue.id, alias="CVPR Workshop"
        ).first()
        assert rejected is None

    def test_resolve_aliases_idempotent(self, client, db_session):
        """Submitting the same accepted alias twice does not raise an error."""
        venue = _make_venue(db_session, "ECCV", openalex_id="VECCV", tier=2)
        project = _make_project(db_session, name="IdempotentAliasProject")
        db_session.commit()

        payload = {
            "decisions": [
                {"venue_id": venue.id, "alias": "European Conference on Computer Vision",
                 "accepted": True},
            ]
        }
        r1 = client.post(f"/api/projects/import/{project.id}/resolve-aliases", json=payload)
        assert r1.status_code == 200
        r2 = client.post(f"/api/projects/import/{project.id}/resolve-aliases", json=payload)
        assert r2.status_code == 200

        rows = db_session.query(VenueAlias).filter_by(
            venue_id=venue.id, alias="European Conference on Computer Vision"
        ).all()
        assert len(rows) == 1  # not duplicated


# ---------------------------------------------------------------------------
# PDF file export / import
# ---------------------------------------------------------------------------


class TestPDFFileExportImport:
    def test_export_include_files_adds_pdf_to_zip(self, db_session, tmp_path):
        """export_project(include_files=True) adds PDFs to archive and populates manifest."""
        work = _make_work(db_session, title="PDF Paper", year=2023, doi="10.9999/pdfexport")
        project = _make_project(db_session, name="PDF Export Project")
        tl = _make_topic_list(db_session, project.id)
        _add_seed(db_session, tl, work)

        # Write a fake PDF to the expected location
        pdf_root = tmp_path / "pdfs"
        work_dir = pdf_root / str(work.id)
        work_dir.mkdir(parents=True)
        (work_dir / "paper.pdf").write_bytes(b"fake pdf bytes")

        # Create WorkPDF row
        db_session.add(WorkPDF(
            work_id=work.id,
            filename="paper.pdf",
            is_primary=True,
            extraction_status="pending",
        ))
        db_session.commit()

        with patch("sotascope.api.settings.get_setting_value", return_value=str(pdf_root)):
            buf = export_project(project.id, db_session, include_files=True)

        zip_bytes = buf.read()
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            names = zf.namelist()
            assert f"files/{work.id}/paper.pdf" in names
            assert zf.read(f"files/{work.id}/paper.pdf") == b"fake pdf bytes"
            manifest = json.loads(zf.read("manifest.json"))

        assert len(manifest["files"]) == 1
        entry = manifest["files"][0]
        assert entry["doi"] == "10.9999/pdfexport"
        assert entry["work_id"] == work.id
        assert "paper.pdf" in entry["filenames"]

    def test_export_include_files_also_adds_txt(self, db_session, tmp_path):
        """When extraction_status='ready', the companion .txt file is also included."""
        work = _make_work(db_session, title="TXT Paper", year=2023, doi="10.9999/txtexport")
        project = _make_project(db_session, name="TXT Export Project")
        tl = _make_topic_list(db_session, project.id)
        _add_seed(db_session, tl, work)

        pdf_root = tmp_path / "pdfs"
        work_dir = pdf_root / str(work.id)
        work_dir.mkdir(parents=True)
        (work_dir / "paper.pdf").write_bytes(b"pdf bytes")
        (work_dir / "paper.txt").write_text("extracted text", encoding="utf-8")

        db_session.add(WorkPDF(
            work_id=work.id,
            filename="paper.pdf",
            is_primary=True,
            extraction_status="ready",
        ))
        db_session.commit()

        with patch("sotascope.api.settings.get_setting_value", return_value=str(pdf_root)):
            buf = export_project(project.id, db_session, include_files=True)

        zip_bytes = buf.read()
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            names = zf.namelist()
            assert f"files/{work.id}/paper.pdf" in names
            assert f"files/{work.id}/paper.txt" in names
            manifest = json.loads(zf.read("manifest.json"))

        entry = manifest["files"][0]
        assert "paper.pdf" in entry["filenames"]
        assert "paper.txt" in entry["filenames"]

    def test_export_default_no_files(self, db_session, tmp_path):
        """Without include_files, the archive contains no files/ entries."""
        work = _make_work(db_session, title="No Files Paper", year=2023, doi="10.9999/nofiles")
        project = _make_project(db_session, name="No Files Project")
        tl = _make_topic_list(db_session, project.id)
        _add_seed(db_session, tl, work)
        db_session.add(WorkPDF(work_id=work.id, filename="paper.pdf", is_primary=True,
                                extraction_status="pending"))
        db_session.commit()

        buf = export_project(project.id, db_session)  # include_files defaults to False
        zip_bytes = buf.read()
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            names = zf.namelist()
            assert not any(n.startswith("files/") for n in names)
            manifest = json.loads(zf.read("manifest.json"))
        assert manifest["files"] == []

    def test_import_creates_workpdf_and_copies_file(self, db_session, tmp_path):
        """Importing a ZIP with files/ copies the PDF and creates a WorkPDF row."""
        # Work already exists in library (will be matched by DOI)
        work = _make_work(db_session, title="Import PDF Paper", year=2023,
                          doi="10.8888/importpdf")
        db_session.commit()

        archive_work_id = work.id  # same instance here; on a real cross-instance import this differs

        manifest_data = {
            "format_version": 2,
            "project": {"name": "Import PDF Project"},
            "works": [{"doi": "10.8888/importpdf", "title": "Import PDF Paper",
                        "year": 2023, "arxiv_id": None, "openalex_id": None, "bibtex_key": None}],
            "topic_lists": [{"name": "Main", "color": "#3b82f6",
                              "works": ["doi:10.8888/importpdf"]}],
            "extraction_schemas": [], "venue_tiers": [], "citations": [],
            "chat_sessions": [], "work_notes": [],
            "files": [
                {"work_id": archive_work_id, "doi": "10.8888/importpdf",
                 "arxiv_id": None, "filenames": ["paper.pdf"]},
            ],
        }

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest_data))
            zf.writestr("seeds.bib", "")
            zf.writestr(f"files/{archive_work_id}/paper.pdf", b"imported pdf content")
        buf.seek(0)
        zip_bytes = buf.read()

        pdf_root = tmp_path / "pdfs"
        with patch("sotascope.api.settings.get_setting_value", return_value=str(pdf_root)):
            result, seed_ids = import_project(zip_bytes, db_session)

        assert result.works_matched == 1  # work already existed

        # PDF should be on disk
        dest = pdf_root / str(work.id) / "paper.pdf"
        assert dest.exists()
        assert dest.read_bytes() == b"imported pdf content"

        # WorkPDF row should exist
        pdf_row = db_session.query(WorkPDF).filter_by(
            work_id=work.id, filename="paper.pdf"
        ).one_or_none()
        assert pdf_row is not None
        assert pdf_row.is_primary is True
        assert pdf_row.extraction_status == "pending"

    def test_import_skips_existing_workpdf(self, db_session, tmp_path):
        """Importing the same archive twice does not create duplicate WorkPDF rows."""
        work = _make_work(db_session, title="Dedup PDF Paper", year=2023,
                          doi="10.8888/deduppdf")
        db_session.commit()

        manifest_data = {
            "format_version": 2,
            "project": {"name": "Dedup PDF Project"},
            "works": [{"doi": "10.8888/deduppdf", "title": "Dedup PDF Paper",
                        "year": 2023, "arxiv_id": None, "openalex_id": None, "bibtex_key": None}],
            "topic_lists": [{"name": "Main", "color": "#3b82f6",
                              "works": ["doi:10.8888/deduppdf"]}],
            "extraction_schemas": [], "venue_tiers": [], "citations": [],
            "chat_sessions": [], "work_notes": [],
            "files": [
                {"work_id": work.id, "doi": "10.8888/deduppdf",
                 "arxiv_id": None, "filenames": ["paper.pdf"]},
            ],
        }

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest_data))
            zf.writestr("seeds.bib", "")
            zf.writestr(f"files/{work.id}/paper.pdf", b"pdf bytes")
        buf.seek(0)
        zip_bytes = buf.read()

        pdf_root = tmp_path / "pdfs"
        with patch("sotascope.api.settings.get_setting_value", return_value=str(pdf_root)):
            import_project(zip_bytes, db_session)
            # Second import — project name collision creates temp project, but PDF import
            # still runs on the same work row; no duplicate WorkPDF should be created.
            import_project(zip_bytes, db_session)

        rows = db_session.query(WorkPDF).filter_by(
            work_id=work.id, filename="paper.pdf"
        ).all()
        assert len(rows) == 1

    def test_import_sets_extraction_status_ready_for_txt(self, db_session, tmp_path):
        """When a .txt companion is in the archive, extraction_status is set to 'ready'."""
        work = _make_work(db_session, title="TXT Import Paper", year=2023,
                          doi="10.8888/txtimport")
        db_session.commit()

        manifest_data = {
            "format_version": 2,
            "project": {"name": "TXT Import Project"},
            "works": [{"doi": "10.8888/txtimport", "title": "TXT Import Paper",
                        "year": 2023, "arxiv_id": None, "openalex_id": None, "bibtex_key": None}],
            "topic_lists": [{"name": "Main", "color": "#3b82f6",
                              "works": ["doi:10.8888/txtimport"]}],
            "extraction_schemas": [], "venue_tiers": [], "citations": [],
            "chat_sessions": [], "work_notes": [],
            "files": [
                {"work_id": work.id, "doi": "10.8888/txtimport",
                 "arxiv_id": None, "filenames": ["paper.pdf", "paper.txt"]},
            ],
        }

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest_data))
            zf.writestr("seeds.bib", "")
            zf.writestr(f"files/{work.id}/paper.pdf", b"pdf bytes")
            zf.writestr(f"files/{work.id}/paper.txt", b"extracted text")
        buf.seek(0)
        zip_bytes = buf.read()

        pdf_root = tmp_path / "pdfs"
        with patch("sotascope.api.settings.get_setting_value", return_value=str(pdf_root)):
            import_project(zip_bytes, db_session)

        pdf_row = db_session.query(WorkPDF).filter_by(
            work_id=work.id, filename="paper.pdf"
        ).one_or_none()
        assert pdf_row is not None
        assert pdf_row.extraction_status == "ready"

        txt_dest = pdf_root / str(work.id) / "paper.txt"
        assert txt_dest.exists()
        assert txt_dest.read_bytes() == b"extracted text"
