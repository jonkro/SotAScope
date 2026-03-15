"""Tests for project ZIP export service and API endpoint."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from litexplorer.models.chat import ChatMessage, ChatSession
from litexplorer.models.extraction import ExtractionColumn, ExtractionSchema
from litexplorer.models.library import (
    Author,
    Citation,
    Venue,
    VenueAlias,
    Work,
    WorkAuthor,
    WorkNote,
)
from litexplorer.models.project import (
    Project,
    ProjectVenueTier,
    TopicList,
    TopicListWork,
)
from litexplorer.services.project_export import _work_ref, export_project


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_work(
    db,
    *,
    title: str = "A Paper",
    year: int | None = 2022,
    doi: str | None = None,
    arxiv_id: str | None = None,
    openalex_id: str | None = None,
) -> Work:
    work = Work(title=title, publication_year=year, doi=doi, arxiv_id=arxiv_id, openalex_id=openalex_id)
    db.add(work)
    db.flush()
    return work


def _make_project(db, name: str = "Test Project") -> Project:
    p = Project(name=name, description="A test project")
    db.add(p)
    db.flush()
    return p


def _make_topic_list(db, project_id: int, name: str = "Main", color: str = "#3b82f6") -> TopicList:
    tl = TopicList(project_id=project_id, name=name, color=color)
    db.add(tl)
    db.flush()
    return tl


def _add_seed(db, tl: TopicList, work: Work) -> None:
    db.add(TopicListWork(topic_list_id=tl.id, work_id=work.id))
    db.flush()


def _unzip(buf: io.BytesIO) -> dict[str, bytes]:
    """Return {filename: bytes} from a ZIP buffer."""
    buf.seek(0)
    with zipfile.ZipFile(buf) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


# ---------------------------------------------------------------------------
# _work_ref helper
# ---------------------------------------------------------------------------


class TestWorkRef:
    def test_doi_preferred(self, db_session):
        w = _make_work(db_session, doi="10.1/x", arxiv_id="2301.0001")
        assert _work_ref(w) == "doi:10.1/x"

    def test_arxiv_fallback(self, db_session):
        w = _make_work(db_session, arxiv_id="2301.0001")
        assert _work_ref(w) == "arxiv:2301.0001"

    def test_openalex_fallback(self, db_session):
        w = _make_work(db_session, openalex_id="W123456")
        assert _work_ref(w) == "openalex:W123456"

    def test_id_last_resort(self, db_session):
        w = _make_work(db_session)
        ref = _work_ref(w)
        assert ref.startswith("id:")


# ---------------------------------------------------------------------------
# export_project service
# ---------------------------------------------------------------------------


class TestExportProject:
    def test_raises_on_missing_project(self, db_session):
        with pytest.raises(ValueError, match="not found"):
            export_project(9999, db_session)

    def test_empty_project_produces_valid_zip(self, db_session):
        project = _make_project(db_session)
        db_session.commit()
        buf = export_project(project.id, db_session)
        files = _unzip(buf)
        assert "manifest.json" in files
        assert "seeds.bib" in files

    def test_manifest_has_correct_structure(self, db_session):
        project = _make_project(db_session)
        db_session.commit()
        buf = export_project(project.id, db_session)
        manifest = json.loads(_unzip(buf)["manifest.json"])
        assert manifest["format_version"] == 2
        assert "exported_at" in manifest
        assert manifest["project"]["name"] == "Test Project"
        assert manifest["project"]["description"] == "A test project"
        assert manifest["works"] == []
        assert manifest["topic_lists"] == []
        assert manifest["extraction_schemas"] == []
        assert manifest["venue_tiers"] == []
        assert manifest["citations"] == []
        assert manifest["chat_sessions"] == []
        assert manifest["work_notes"] == []
        assert manifest["files"] == []

    def test_seeds_in_manifest_and_bib(self, db_session):
        project = _make_project(db_session)
        tl = _make_topic_list(db_session, project.id)
        w1 = _make_work(db_session, title="Paper One", doi="10.1/one", year=2021)
        w2 = _make_work(db_session, title="Paper Two", arxiv_id="2301.0002", year=2022)
        _add_seed(db_session, tl, w1)
        _add_seed(db_session, tl, w2)
        db_session.commit()

        buf = export_project(project.id, db_session)
        files = _unzip(buf)
        manifest = json.loads(files["manifest.json"])
        bib = files["seeds.bib"].decode()

        assert len(manifest["works"]) == 2
        dois = {w["doi"] for w in manifest["works"]}
        assert "10.1/one" in dois

        # arXiv work
        arxiv_works = [w for w in manifest["works"] if w["arxiv_id"] == "2301.0002"]
        assert len(arxiv_works) == 1

        # BibTeX includes both
        assert "Paper One" in bib
        assert "Paper Two" in bib

    def test_manifest_uses_stable_refs_not_db_ids(self, db_session):
        """work_refs in topic_lists must use doi:/arxiv: prefixes, not numeric IDs."""
        project = _make_project(db_session)
        tl = _make_topic_list(db_session, project.id)
        w = _make_work(db_session, doi="10.9/stable")
        _add_seed(db_session, tl, w)
        db_session.commit()

        manifest = json.loads(_unzip(export_project(project.id, db_session))["manifest.json"])
        tl_data = manifest["topic_lists"][0]
        assert tl_data["works"] == ["doi:10.9/stable"]

    def test_multiple_topic_lists(self, db_session):
        project = _make_project(db_session)
        tl1 = _make_topic_list(db_session, project.id, "List A", "#ff0000")
        tl2 = _make_topic_list(db_session, project.id, "List B", "#00ff00")
        w = _make_work(db_session, doi="10.1/shared")
        _add_seed(db_session, tl1, w)
        _add_seed(db_session, tl2, w)
        db_session.commit()

        manifest = json.loads(_unzip(export_project(project.id, db_session))["manifest.json"])
        assert len(manifest["topic_lists"]) == 2
        # work appears in both lists
        for tl_data in manifest["topic_lists"]:
            assert "doi:10.1/shared" in tl_data["works"]

    def test_extraction_schema_and_results(self, db_session):
        project = _make_project(db_session)
        tl = _make_topic_list(db_session, project.id)
        w = _make_work(db_session, doi="10.1/paper")
        _add_seed(db_session, tl, w)

        schema = ExtractionSchema(title="My Survey", project_id=project.id)
        db_session.add(schema)
        db_session.flush()
        col = ExtractionColumn(
            schema_id=schema.id, name="Method", prompt="What method?",
            allowed_values=["deep", "shallow"], sort_order=0,
        )
        db_session.add(col)
        db_session.flush()

        # Add an extraction answer note
        note_type = f"{schema.title} / {col.name}"[:64]
        answer_note = WorkNote(
            work_id=w.id, project_id=project.id,
            content="Deep learning", note_type=note_type,
            provenance="ai",
        )
        db_session.add(answer_note)
        db_session.commit()

        manifest = json.loads(_unzip(export_project(project.id, db_session))["manifest.json"])
        schemas = manifest["extraction_schemas"]
        assert len(schemas) == 1
        assert schemas[0]["title"] == "My Survey"
        assert len(schemas[0]["columns"]) == 1
        assert schemas[0]["columns"][0]["name"] == "Method"
        assert schemas[0]["columns"][0]["allowed_values"] == ["deep", "shallow"]

        results = schemas[0]["results"]
        assert len(results) == 1
        assert results[0]["work_ref"] == "doi:10.1/paper"
        assert results[0]["column_name"] == "Method"
        assert results[0]["answer"] == "Deep learning"
        assert results[0]["provenance"] == "ai"

    def test_ai_proposal_notes_excluded(self, db_session):
        project = _make_project(db_session)
        tl = _make_topic_list(db_session, project.id)
        w = _make_work(db_session, doi="10.1/p")
        _add_seed(db_session, tl, w)

        schema = ExtractionSchema(title="T", project_id=project.id)
        db_session.add(schema)
        db_session.flush()
        col = ExtractionColumn(schema_id=schema.id, name="C", prompt="Q?", sort_order=0)
        db_session.add(col)
        db_session.flush()

        note_type = "T / C"
        db_session.add(WorkNote(
            work_id=w.id, project_id=project.id,
            content="Proposal content", note_type=note_type,
            provenance="ai_proposal",
        ))
        db_session.commit()

        manifest = json.loads(_unzip(export_project(project.id, db_session))["manifest.json"])
        results = manifest["extraction_schemas"][0]["results"]
        assert results == []

    def test_venue_tiers(self, db_session):
        """Venue tier snapshot includes project-local override with correct fields."""
        project = _make_project(db_session)
        tl = _make_topic_list(db_session, project.id)

        venue = Venue(name="ICML", openalex_id="V123", issn="1234-5678", venue_type="conference")
        db_session.add(venue)
        db_session.flush()
        db_session.add(VenueAlias(venue_id=venue.id, alias="ICML", sort_order=0))
        db_session.add(VenueAlias(venue_id=venue.id, alias="Int. Conf. on Machine Learning",
                                  sort_order=1))
        # Seed work so the venue is in scope
        w = _make_work(db_session, doi="10.1/icml")
        w.venue_id = venue.id
        db_session.flush()
        _add_seed(db_session, tl, w)
        db_session.add(ProjectVenueTier(project_id=project.id, venue_id=venue.id, tier=1))
        db_session.commit()

        manifest = json.loads(_unzip(export_project(project.id, db_session))["manifest.json"])
        tiers = manifest["venue_tiers"]
        assert len(tiers) == 1
        assert tiers[0]["venue_openalex_id"] == "V123"
        assert tiers[0]["venue_issn"] == "1234-5678"
        assert tiers[0]["venue_name"] == "ICML"
        assert tiers[0]["tier"] == 1
        # Preferred alias "ICML" excluded; secondary alias present
        assert "ICML" not in tiers[0]["aliases"]
        assert "Int. Conf. on Machine Learning" in tiers[0]["aliases"]

    def test_citations_between_seeds_only(self, db_session):
        project = _make_project(db_session)
        tl = _make_topic_list(db_session, project.id)
        w1 = _make_work(db_session, doi="10.1/a")
        w2 = _make_work(db_session, doi="10.1/b")
        w3 = _make_work(db_session, doi="10.1/c")  # not a seed
        _add_seed(db_session, tl, w1)
        _add_seed(db_session, tl, w2)

        # w1 cites w2 (both seeds) — should appear
        db_session.add(Citation(citing_work_id=w1.id, cited_work_id=w2.id, source="openalex"))
        # w1 cites w3 (w3 not a seed) — should NOT appear
        db_session.add(Citation(citing_work_id=w1.id, cited_work_id=w3.id, source="openalex"))
        db_session.commit()

        manifest = json.loads(_unzip(export_project(project.id, db_session))["manifest.json"])
        citations = manifest["citations"]
        assert len(citations) == 1
        assert citations[0]["citing"] == "doi:10.1/a"
        assert citations[0]["cited"] == "doi:10.1/b"

    def test_chat_sessions_included(self, db_session):
        project = _make_project(db_session)
        session = ChatSession(project_id=project.id, context_type="papers", is_auto=True)
        db_session.add(session)
        db_session.flush()
        db_session.add(ChatMessage(session_id=session.id, role="user", content="Hello"))
        db_session.add(ChatMessage(session_id=session.id, role="assistant", content="Hi there"))
        db_session.commit()

        manifest = json.loads(_unzip(export_project(project.id, db_session))["manifest.json"])
        sessions = manifest["chat_sessions"]
        assert len(sessions) == 1
        assert sessions[0]["context_type"] == "papers"
        assert len(sessions[0]["messages"]) == 2
        assert sessions[0]["messages"][0]["role"] == "user"
        assert sessions[0]["messages"][0]["content"] == "Hello"

    def test_project_scoped_work_notes(self, db_session):
        project = _make_project(db_session)
        tl = _make_topic_list(db_session, project.id)
        w = _make_work(db_session, doi="10.1/n")
        _add_seed(db_session, tl, w)
        db_session.add(WorkNote(
            work_id=w.id, project_id=project.id,
            content="My annotation", note_type="Reading notes",
            provenance="user",
        ))
        db_session.commit()

        manifest = json.loads(_unzip(export_project(project.id, db_session))["manifest.json"])
        notes = manifest["work_notes"]
        assert len(notes) == 1
        assert notes[0]["work_ref"] == "doi:10.1/n"
        assert notes[0]["content"] == "My annotation"
        assert notes[0]["provenance"] == "user"

    def test_files_key_is_empty_list(self, db_session):
        project = _make_project(db_session)
        db_session.commit()
        manifest = json.loads(_unzip(export_project(project.id, db_session))["manifest.json"])
        assert manifest["files"] == []


# ---------------------------------------------------------------------------
# API: GET /api/projects/{id}/export
# ---------------------------------------------------------------------------


class TestProjectExportEndpoint:
    def test_returns_zip(self, client, db_session):
        project = _make_project(db_session)
        db_session.commit()
        resp = client.get(f"/api/projects/{project.id}/export")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert "attachment" in resp.headers["content-disposition"]
        assert ".zip" in resp.headers["content-disposition"]

    def test_zip_is_valid_and_contains_manifest(self, client, db_session):
        project = _make_project(db_session, "My Export Project")
        tl = _make_topic_list(db_session, project.id)
        w = _make_work(db_session, doi="10.1/x", title="Exported Paper")
        _add_seed(db_session, tl, w)
        db_session.commit()

        resp = client.get(f"/api/projects/{project.id}/export")
        assert resp.status_code == 200

        buf = io.BytesIO(resp.content)
        files = _unzip(buf)
        assert "manifest.json" in files
        assert "seeds.bib" in files

        manifest = json.loads(files["manifest.json"])
        assert manifest["project"]["name"] == "My Export Project"
        assert len(manifest["works"]) == 1

        bib = files["seeds.bib"].decode()
        assert "Exported Paper" in bib

    def test_project_not_found(self, client):
        resp = client.get("/api/projects/9999/export")
        assert resp.status_code == 404

    def test_filename_derived_from_project_name(self, client, db_session):
        project = _make_project(db_session, "My Cool Project")
        db_session.commit()
        resp = client.get(f"/api/projects/{project.id}/export")
        assert "My_Cool_Project.zip" in resp.headers["content-disposition"]
