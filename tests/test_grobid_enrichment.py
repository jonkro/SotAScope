"""Tests for the GROBID enrichment endpoint (POST /api/enrich/works/{id}/grobid)
and the health-check endpoint (GET /api/grobid/status).

Mock strategy
-------------
* ``litexplorer.external.grobid.GrobidClient`` is patched per-test with a
  mock class whose constructor returns a mock instance.  Because the service
  imports the class with ``from litexplorer.external.grobid import GrobidClient``
  *inside* the function, patching the attribute on the source module is the
  correct approach.
* ``pathlib.Path.exists`` and ``pathlib.Path.read_bytes`` are patched to avoid
  needing real PDF files on disk.
* OpenAlex and Crossref clients are injected via the existing ``_get_client``
  and ``_get_crossref_client`` factory-function patches (same pattern as
  ``test_enrichment_api.py`` and ``test_semantic_scholar_enrichment.py``).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import httpx
from litexplorer.api.deps import get_db
from litexplorer.external.grobid import GrobidReference
from litexplorer.models.base import Base
from litexplorer.models.library import Citation, Venue, VenueAlias, Work, WorkLocation, WorkPDF
from litexplorer.models.project import Project, TopicList, TopicListWork
from litexplorer.models.settings import Setting
from litexplorer.schemas.enrichment import SearchImportCandidate
from litexplorer.services.enrichment import EnrichmentService
from tests.fixtures.openalex_responses import SAMPLE_WORK_RAW


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def mock_oa_client():
    """Mock OpenAlex client; returns nothing by default."""
    mock = MagicMock()
    mock.get_work_by_doi_raw.return_value = None
    mock.get_work_by_id_raw.return_value = None
    return mock


@pytest.fixture()
def mock_cr_client():
    """Mock Crossref client; returns nothing by default."""
    mock = MagicMock()
    mock.get_work_by_doi_raw.return_value = None
    return mock


@pytest.fixture()
def client(db_session, mock_oa_client, mock_cr_client):
    """FastAPI TestClient with in-memory DB + mocked OA/Crossref clients."""
    from litexplorer.app import app

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db

    with (
        patch("litexplorer.api.enrichment._get_client") as mock_get_oa,
        patch("litexplorer.api.enrichment._get_crossref_client") as mock_get_cr,
    ):
        mock_get_oa.return_value = mock_oa_client
        mock_get_cr.return_value = mock_cr_client
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _make_work(db_session, **kwargs) -> Work:
    """Create and persist a minimal Work record."""
    defaults = {"title": "Test Paper", "citation_count": 0}
    defaults.update(kwargs)
    work = Work(**defaults)
    db_session.add(work)
    db_session.commit()
    db_session.refresh(work)
    return work


def _make_work_pdf(
    db_session,
    work_id: int,
    filename: str = "paper.pdf",
    is_primary: bool = True,
) -> WorkPDF:
    """Attach a WorkPDF record to *work_id*."""
    pdf = WorkPDF(work_id=work_id, filename=filename, is_primary=is_primary)
    db_session.add(pdf)
    db_session.commit()
    db_session.refresh(pdf)
    return pdf


def _seed_setting(db_session, key: str, value: str) -> None:
    """Insert or update a Setting row."""
    existing = db_session.execute(
        select(Setting).where(Setting.key == key)
    ).scalar_one_or_none()
    if existing:
        existing.value = value
    else:
        db_session.add(Setting(key=key, value=value))
    db_session.commit()


def _make_grobid_ref(**kwargs) -> GrobidReference:
    """Build a GrobidReference with sensible defaults for unspecified fields."""
    defaults = dict(
        title=None,
        authors=[],
        doi=None,
        arxiv_id=None,
        journal=None,
        volume=None,
        pages=None,
        year=None,
        raw_string=None,
        url=None,
        venue_name=None,
    )
    defaults.update(kwargs)
    return GrobidReference(**defaults)


def _mock_grobid_cls(references: list) -> MagicMock:
    """Return a mock GrobidClient *class* whose instances return *references*."""
    instance = MagicMock()
    instance.extract_references.return_value = references
    return MagicMock(return_value=instance)


# ---------------------------------------------------------------------------
# POST /api/enrich/works/{id}/grobid — happy paths
# ---------------------------------------------------------------------------


def test_grobid_enrich_with_doi_reference(db_session, client, mock_oa_client):
    """GROBID enrichment is accepted and scheduled (returns 202 immediately).

    The actual reference extraction and import happens in the background task.
    """
    work = _make_work(db_session, title="Seed Paper")
    _make_work_pdf(db_session, work.id)
    _seed_setting(db_session, "grobid_url", "http://localhost:8070")

    refs = [_make_grobid_ref(doi="10.1145/3230543.3230563", title="Referenced Work")]
    mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW

    with (
        patch("litexplorer.external.grobid.GrobidClient", _mock_grobid_cls(refs)),
        patch.object(Path, "exists", return_value=True),
        patch.object(Path, "read_bytes", return_value=b"fake pdf bytes"),
    ):
        resp = client.post(f"/api/enrich/works/{work.id}/grobid")

    assert resp.status_code == 202, resp.text
    assert resp.json()["work_id"] == work.id


def test_grobid_enrich_with_arxiv_reference(db_session, client):
    """GROBID enrichment with an arXiv reference is accepted and returns 202."""
    seed_work = _make_work(db_session, title="Seed Paper")
    _make_work_pdf(db_session, seed_work.id)
    _seed_setting(db_session, "grobid_url", "http://localhost:8070")

    _make_work(db_session, title="ArXiv Preprint", arxiv_id="1234.5678")
    refs = [_make_grobid_ref(arxiv_id="1234.5678")]

    with (
        patch("litexplorer.external.grobid.GrobidClient", _mock_grobid_cls(refs)),
        patch.object(Path, "exists", return_value=True),
        patch.object(Path, "read_bytes", return_value=b"fake pdf bytes"),
    ):
        resp = client.post(f"/api/enrich/works/{seed_work.id}/grobid")

    assert resp.status_code == 202, resp.text
    assert resp.json()["work_id"] == seed_work.id


def test_grobid_enrich_title_only(db_session, client, mock_oa_client):
    """Title-only GROBID reference: endpoint accepts the request and returns 202."""
    work = _make_work(db_session, title="Seed Paper")
    _make_work_pdf(db_session, work.id)
    _seed_setting(db_session, "grobid_url", "http://localhost:8070")

    ref_title = "Deep Residual Learning for Image Recognition"
    refs = [_make_grobid_ref(title=ref_title, authors=["He Kaiming"], year="2016")]

    candidate = SearchImportCandidate(
        title=ref_title,
        authors=["He Kaiming"],
        year=2016,
        doi="10.1109/cvpr.2016.90",
        venue="CVPR",
        semantic_scholar_id=None,
        source="crossref",
        score=95.0,
    )
    mock_oa_client.get_work_by_doi_raw.return_value = SAMPLE_WORK_RAW

    with (
        patch("litexplorer.external.grobid.GrobidClient", _mock_grobid_cls(refs)),
        patch.object(Path, "exists", return_value=True),
        patch.object(Path, "read_bytes", return_value=b"fake pdf bytes"),
        patch(
            "litexplorer.services.enrichment.EnrichmentService.search_import_candidates",
            return_value=[candidate],
        ),
    ):
        resp = client.post(f"/api/enrich/works/{work.id}/grobid")

    assert resp.status_code == 202, resp.text
    assert resp.json()["work_id"] == work.id


def test_grobid_enrich_dedup(db_session, client):
    """GROBID enrichment with an already-known DOI: accepted and returns 202."""
    work = _make_work(db_session, title="Seed Paper")
    _make_work_pdf(db_session, work.id)
    _seed_setting(db_session, "grobid_url", "http://localhost:8070")

    _make_work(db_session, title="Already In Library", doi="10.1234/existing")
    refs = [_make_grobid_ref(doi="10.1234/existing", title="Already In Library")]

    with (
        patch("litexplorer.external.grobid.GrobidClient", _mock_grobid_cls(refs)),
        patch.object(Path, "exists", return_value=True),
        patch.object(Path, "read_bytes", return_value=b"fake pdf bytes"),
    ):
        resp = client.post(f"/api/enrich/works/{work.id}/grobid")

    assert resp.status_code == 202, resp.text
    assert resp.json()["work_id"] == work.id


# ---------------------------------------------------------------------------
# POST /api/enrich/works/{id}/grobid — error paths
# ---------------------------------------------------------------------------


def test_grobid_enrich_no_pdf(db_session, client):
    """A work with no attached PDF returns 404."""
    work = _make_work(db_session, title="No PDF Paper")
    # Deliberately no WorkPDF record

    resp = client.post(f"/api/enrich/works/{work.id}/grobid")

    assert resp.status_code == 404
    assert "pdf" in resp.json()["detail"].lower()


def test_grobid_enrich_not_configured(db_session, client):
    """When grobid_url is not set in Settings, the endpoint returns 400."""
    work = _make_work(db_session, title="Seed Paper")
    _make_work_pdf(db_session, work.id)
    # No grobid_url setting → service raises ValueError("...not configured...")

    with (
        patch.object(Path, "exists", return_value=True),
        patch.object(Path, "read_bytes", return_value=b"fake pdf bytes"),
    ):
        resp = client.post(f"/api/enrich/works/{work.id}/grobid")

    assert resp.status_code == 400
    assert "grobid" in resp.json()["detail"].lower()


def test_grobid_enrich_failed_resolution(db_session, client):
    """Unresolvable reference: endpoint accepts the request and returns 202.

    Resolution failure is handled inside the background task and logged.
    """
    work = _make_work(db_session, title="Seed Paper")
    _make_work_pdf(db_session, work.id)
    _seed_setting(db_session, "grobid_url", "http://localhost:8070")

    refs = [_make_grobid_ref(title="xkcd qwerty zork nonsense unresolvable 2099")]

    with (
        patch("litexplorer.external.grobid.GrobidClient", _mock_grobid_cls(refs)),
        patch.object(Path, "exists", return_value=True),
        patch.object(Path, "read_bytes", return_value=b"fake pdf bytes"),
        patch(
            "litexplorer.services.enrichment.EnrichmentService.search_import_candidates",
            return_value=[],
        ),
    ):
        resp = client.post(f"/api/enrich/works/{work.id}/grobid")

    assert resp.status_code == 202, resp.text
    assert resp.json()["work_id"] == work.id


# ---------------------------------------------------------------------------
# GET /api/grobid/status
# ---------------------------------------------------------------------------


def test_grobid_status_available(db_session, client):
    """When GROBID is reachable, available=True and the configured url is echoed."""
    _seed_setting(db_session, "grobid_url", "http://localhost:8070")

    mock_instance = MagicMock()
    mock_instance.check_health.return_value = True

    with patch("litexplorer.api.grobid.GrobidClient", MagicMock(return_value=mock_instance)):
        resp = client.get("/api/grobid/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert data["url"] == "http://localhost:8070"


def test_grobid_status_not_available(db_session, client):
    """When the GROBID service is unreachable, available=False."""
    _seed_setting(db_session, "grobid_url", "http://localhost:8070")

    mock_instance = MagicMock()
    mock_instance.check_health.return_value = False

    with patch("litexplorer.api.grobid.GrobidClient", MagicMock(return_value=mock_instance)):
        resp = client.get("/api/grobid/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is False
    assert data["url"] == "http://localhost:8070"


def test_grobid_status_not_configured(client):
    """When grobid_url is absent from Settings, available=False and url=''."""
    # No grobid_url setting seeded — the endpoint should short-circuit immediately
    resp = client.get("/api/grobid/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is False
    assert data["url"] == ""


# ---------------------------------------------------------------------------
# Service-level resolution chain tests (direct calls, no HTTP / background task)
# ---------------------------------------------------------------------------


class TestGrobidResolutionChain:
    """Direct EnrichmentService tests for the 4-step GROBID reference resolution chain.

    These bypass the HTTP layer and background task so the test DB (in-memory)
    is used directly, making it possible to assert on database state.
    """

    def _make_service(self, db_session, mock_oa=None, mock_cr=None) -> EnrichmentService:
        if mock_oa is None:
            mock_oa = MagicMock()
            mock_oa.get_work_by_doi_raw.return_value = None
            mock_oa.get_work_by_id_raw.return_value = None
            mock_oa.get_work_by_arxiv_id_raw.return_value = None
        if mock_cr is None:
            mock_cr = MagicMock()
            mock_cr.get_work_by_doi_raw.return_value = None
            mock_cr.search_works.return_value = []
        return EnrichmentService(db=db_session, client=mock_oa, crossref_client=mock_cr)

    def _seed_pdf_settings(self, db_session, work_id: int) -> None:
        """Create a WorkPDF record and seed grobid/pdf settings."""
        _make_work_pdf(db_session, work_id)
        _seed_setting(db_session, "grobid_url", "http://localhost:8070")
        _seed_setting(db_session, "pdf_storage_path", "/fake/pdfs")

    def _run(self, svc: EnrichmentService, work_id: int, refs: list, ss_client=None):
        """Invoke enrich_from_grobid with mocked GROBID extraction."""
        with (
            patch("litexplorer.external.grobid.GrobidClient", _mock_grobid_cls(refs)),
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "read_bytes", return_value=b"fake pdf"),
        ):
            return svc.enrich_from_grobid(work_id, ss_client=ss_client)

    # ------------------------------------------------------------------
    # PATH B: arXiv ID → import_by_arxiv_id()
    # ------------------------------------------------------------------

    def test_arxiv_id_resolved_via_arxiv_path(self, db_session):
        """PATH B: arXiv ID present → resolved via import_by_arxiv_id (OA lookup)."""
        work = _make_work(db_session, title="Seed Paper")
        self._seed_pdf_settings(db_session, work.id)

        mock_oa = MagicMock()
        mock_oa.get_work_by_doi_raw.return_value = None
        mock_oa.get_work_by_id_raw.return_value = None
        mock_oa.get_work_by_arxiv_id_raw.return_value = SAMPLE_WORK_RAW

        svc = self._make_service(db_session, mock_oa=mock_oa)
        refs = [_make_grobid_ref(title="Some ArXiv Paper", arxiv_id="2301.12345")]
        result = self._run(svc, work.id, refs)

        assert result.resolved_by_arxiv == 1
        assert result.resolved_by_doi == 0
        assert result.resolved_by_s2 == 0
        assert result.new_count == 1
        assert result.s2_rate_limited is False

    # ------------------------------------------------------------------
    # PATH C: S2 title search — successful match with author+year
    # ------------------------------------------------------------------

    def test_s2_title_search_resolves_by_author_year(self, db_session):
        """PATH C: no DOI, no arXiv → S2 title search finds a match (author+year).
        S2 DOI validated via Crossref (title substring match) → import_by_doi used.
        """
        work = _make_work(db_session, title="Seed Paper")
        self._seed_pdf_settings(db_session, work.id)

        mock_oa = MagicMock()
        mock_oa.get_work_by_doi_raw.return_value = None
        mock_oa.get_work_by_arxiv_id_raw.return_value = None

        # Crossref confirms the DOI by returning a matching title
        mock_cr = MagicMock()
        mock_cr.get_work_by_doi_raw.return_value = {
            "title": ["Deep Residual Learning for Image Recognition"],
        }
        mock_cr.search_works.return_value = []

        mock_ss = MagicMock()
        mock_ss.search_by_title.return_value = [
            {
                "paperId": "s2-resnet-001",
                "title": "Deep Residual Learning for Image Recognition",
                "year": 2016,
                "authors": [{"name": "Kaiming He"}],
                "externalIds": {"DOI": "10.1109/cvpr.2016.90"},
            }
        ]

        # A stub to be returned by import_by_doi (DOI not pre-seeded in DB)
        imported_work = Work(
            title="Deep Residual Learning for Image Recognition",
            citation_count=0,
        )
        db_session.add(imported_work)
        db_session.commit()
        db_session.refresh(imported_work)

        svc = self._make_service(db_session, mock_oa=mock_oa, mock_cr=mock_cr)

        with patch.object(svc, "import_by_doi", return_value=imported_work):
            refs = [_make_grobid_ref(
                title="Deep Residual Learning for Image Recognition",
                authors=["Kaiming He"],
                year="2016",
            )]
            result = self._run(svc, work.id, refs, ss_client=mock_ss)

        assert result.resolved_by_s2 == 1
        assert result.resolved_by_doi == 0
        assert result.resolved_by_arxiv == 0
        assert result.new_count == 1

    # ------------------------------------------------------------------
    # PATH C: DOI validation fails — DOI discarded, S2 ID kept
    # ------------------------------------------------------------------

    def test_s2_doi_validation_fails_doi_discarded_s2_id_kept(self, db_session):
        """PATH C: S2 provides a DOI but Crossref title mismatches → DOI discarded,
        import falls back to S2 paper ID via import_by_semantic_scholar_id.
        import_by_doi must NOT be called.
        """
        work = _make_work(db_session, title="Seed Paper")
        self._seed_pdf_settings(db_session, work.id)

        mock_oa = MagicMock()
        mock_oa.get_work_by_doi_raw.return_value = None
        mock_oa.get_work_by_arxiv_id_raw.return_value = None

        # Crossref: DOI resolves to a completely different title → validation fails
        mock_cr = MagicMock()
        mock_cr.get_work_by_doi_raw.return_value = {
            "title": ["A Completely Different Paper About Cats"],
        }
        mock_cr.search_works.return_value = []

        s2_paper_id = "s2-mismatch-abc123"
        mock_ss = MagicMock()
        mock_ss.search_by_title.return_value = [
            {
                "paperId": s2_paper_id,
                "title": "Real Paper Title",
                "year": 2020,
                "authors": [{"name": "John Smith"}],
                "externalIds": {"DOI": "10.9999/bad-doi"},
            }
        ]

        # The work that import_by_semantic_scholar_id will return
        imported_s2 = Work(title="Real Paper Title", citation_count=0)
        db_session.add(imported_s2)
        db_session.commit()
        db_session.refresh(imported_s2)

        svc = self._make_service(db_session, mock_oa=mock_oa, mock_cr=mock_cr)

        with patch.object(svc, "import_by_semantic_scholar_id", return_value=imported_s2):
            refs = [_make_grobid_ref(
                title="Real Paper Title",
                authors=["John Smith"],
                year="2020",
            )]
            result = self._run(svc, work.id, refs, ss_client=mock_ss)

        assert result.resolved_by_s2 == 1
        assert result.new_count == 1
        # DOI validation failed — get_work_by_doi_raw called for Crossref check, but
        # import_by_doi was NOT invoked (mock_oa.get_work_by_doi_raw stays at 0)
        assert mock_oa.get_work_by_doi_raw.call_count == 0

    # ------------------------------------------------------------------
    # PATH C: GROBID author in "Lastname, Firstname" comma format
    # ------------------------------------------------------------------

    def test_s2_title_search_comma_format_author_matches(self, db_session):
        """PATH C: GROBID produces author as 'Smith, John' (comma-separated) while
        S2 returns 'John M. Smith'. The surname extraction must use the part before
        the comma so 'smith' == 'smith' and the reference resolves correctly.
        """
        work = _make_work(db_session, title="Seed Paper")
        self._seed_pdf_settings(db_session, work.id)

        mock_oa = MagicMock()
        mock_oa.get_work_by_doi_raw.return_value = None
        mock_oa.get_work_by_arxiv_id_raw.return_value = None

        mock_cr = MagicMock()
        mock_cr.get_work_by_doi_raw.return_value = {
            "title": ["Attention Is All You Need"],
        }
        mock_cr.search_works.return_value = []

        mock_ss = MagicMock()
        mock_ss.search_by_title.return_value = [
            {
                "paperId": "s2-attention-sha",
                "title": "Attention Is All You Need",
                "year": 2017,
                # S2 has "Firstname [Middle] Lastname" format
                "authors": [{"name": "Ashish M. Vaswani"}],
                "externalIds": {"DOI": "10.5555/3295222.3295349"},
            }
        ]

        imported_work = Work(title="Attention Is All You Need", citation_count=0)
        db_session.add(imported_work)
        db_session.commit()
        db_session.refresh(imported_work)

        svc = self._make_service(db_session, mock_oa=mock_oa, mock_cr=mock_cr)

        with patch.object(svc, "import_by_doi", return_value=imported_work):
            # GROBID produces "Vaswani, Ashish" — surname before comma
            refs = [_make_grobid_ref(
                title="Attention Is All You Need",
                authors=["Vaswani, Ashish"],
                year="2017",
            )]
            result = self._run(svc, work.id, refs, ss_client=mock_ss)

        assert result.resolved_by_s2 == 1
        assert result.failed_count == 0

    # ------------------------------------------------------------------
    # S2 rate-limited: 429 → unresolved stub, no crash, skip_s2 for rest
    # ------------------------------------------------------------------

    def test_s2_rate_limited_ref_stored_as_unresolved(self, db_session):
        """PATH C 429: S2 returns 429 → first ref stored as unresolved, subsequent
        refs skip S2, s2_rate_limited=True, no crash.
        """
        work = _make_work(db_session, title="Seed Paper")
        self._seed_pdf_settings(db_session, work.id)

        mock_oa = MagicMock()
        mock_oa.get_work_by_doi_raw.return_value = None
        mock_oa.get_work_by_arxiv_id_raw.return_value = None

        fake_resp = MagicMock(spec=httpx.Response)
        fake_resp.status_code = 429
        mock_ss = MagicMock()
        mock_ss.search_by_title.side_effect = httpx.HTTPStatusError(
            "429 Too Many Requests", request=MagicMock(), response=fake_resp
        )

        svc = self._make_service(db_session, mock_oa=mock_oa)
        refs = [
            _make_grobid_ref(title="Paper A", authors=["Alice Author"], year="2021"),
            _make_grobid_ref(title="Paper B", authors=["Bob Builder"], year="2022"),
        ]
        result = self._run(svc, work.id, refs, ss_client=mock_ss)

        assert result.s2_rate_limited is True
        assert result.resolved_by_s2 == 0
        # S2 searched only once (for "Paper A"); "Paper B" skipped S2
        assert mock_ss.search_by_title.call_count == 1
        # Both refs stored as unresolved stub Works
        from sqlalchemy import select as _sel
        stubs = db_session.execute(
            _sel(Work).where(Work.title.in_(["Paper A", "Paper B"]))
        ).scalars().all()
        assert {w.title for w in stubs} == {"Paper A", "Paper B"}
        for stub in stubs:
            assert stub.doi is None
            assert stub.arxiv_id is None
            assert stub.openalex_id is None

    # ------------------------------------------------------------------
    # PATH D: no match anywhere → stub Work created
    # ------------------------------------------------------------------

    def test_no_match_anywhere_stored_as_unresolved_work(self, db_session):
        """PATH D: no DOI, no arXiv, S2 finds no match → stub Work created with
        GROBID metadata (title, year). arxiv_id, doi, openalex_id all None.
        """
        work = _make_work(db_session, title="Seed Paper")
        self._seed_pdf_settings(db_session, work.id)

        mock_oa = MagicMock()
        mock_oa.get_work_by_doi_raw.return_value = None
        mock_oa.get_work_by_arxiv_id_raw.return_value = None

        mock_ss = MagicMock()
        mock_ss.search_by_title.return_value = []  # no S2 results

        svc = self._make_service(db_session, mock_oa=mock_oa)
        refs = [_make_grobid_ref(
            title="Completely Unknown Paper",
            authors=["Unknown Author"],
            year="2019",
        )]
        result = self._run(svc, work.id, refs, ss_client=mock_ss)

        assert result.resolved_by_doi == 0
        assert result.resolved_by_arxiv == 0
        assert result.resolved_by_s2 == 0
        # Unresolved stub counts as new_count (a Work record was created)
        assert result.new_count == 1

        from sqlalchemy import select as _sel
        stub = db_session.execute(
            _sel(Work).where(Work.title == "Completely Unknown Paper")
        ).scalar_one_or_none()
        assert stub is not None
        assert stub.doi is None
        assert stub.arxiv_id is None
        assert stub.openalex_id is None
        assert stub.publication_year == 2019


class TestStoreUnresolvedGrobidWork:
    """Tests for _store_unresolved_grobid_work(): URL → WorkLocation, venue_name → Venue."""

    def _make_service(self, db_session) -> EnrichmentService:
        mock_oa = MagicMock()
        mock_oa.get_work_by_doi_raw.return_value = None
        mock_cr = MagicMock()
        return EnrichmentService(db=db_session, client=mock_oa, crossref_client=mock_cr)

    def _seed_pdf_settings(self, db_session, work_id: int) -> None:
        _make_work_pdf(db_session, work_id)
        _seed_setting(db_session, "grobid_url", "http://localhost:8070")
        _seed_setting(db_session, "pdf_storage_path", "/fake/pdfs")

    def _run_unresolved(self, svc, work_id, refs):
        with (
            patch("litexplorer.external.grobid.GrobidClient", _mock_grobid_cls(refs)),
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "read_bytes", return_value=b"fake pdf"),
        ):
            mock_ss = MagicMock()
            mock_ss.search_by_title.return_value = []
            return svc.enrich_from_grobid(work_id, ss_client=mock_ss)

    def test_url_creates_work_location(self, db_session):
        """A ref with a URL → WorkLocation row for the stub Work."""
        seed = _make_work(db_session, title="Seed Paper")
        self._seed_pdf_settings(db_session, seed.id)
        svc = self._make_service(db_session)

        refs = [_make_grobid_ref(
            title="Unknown Paper With URL",
            year="2020",
            url="https://proceedings.mlr.press/v97/paper.html",
        )]
        self._run_unresolved(svc, seed.id, refs)

        stub = db_session.execute(
            select(Work).where(Work.title == "Unknown Paper With URL")
        ).scalar_one_or_none()
        assert stub is not None

        loc = db_session.execute(
            select(WorkLocation).where(WorkLocation.work_id == stub.id)
        ).scalar_one_or_none()
        assert loc is not None
        assert loc.url == "https://proceedings.mlr.press/v97/paper.html"
        assert loc.location_type == "venue"

    def test_arxiv_url_creates_preprint_location(self, db_session):
        """An arXiv URL → WorkLocation with location_type='preprint'."""
        seed = _make_work(db_session, title="Seed Paper")
        self._seed_pdf_settings(db_session, seed.id)
        svc = self._make_service(db_session)

        refs = [_make_grobid_ref(
            title="ArXiv Only Paper",
            year="2021",
            url="https://arxiv.org/abs/2101.00001",
            arxiv_id="2101.00001",  # known arXiv ID prevents PATH B resolution
        )]
        # PATH B will try import_by_arxiv_id → OA returns None → S2 called → no results → unresolved
        mock_ss = MagicMock()
        mock_ss.search_by_title.return_value = []
        mock_oa = MagicMock()
        mock_oa.get_work_by_doi_raw.return_value = None
        mock_oa.get_work_by_arxiv_id_raw.return_value = None
        mock_cr = MagicMock()
        mock_cr.get_work_by_doi_raw.return_value = None
        svc2 = EnrichmentService(db=db_session, client=mock_oa, crossref_client=mock_cr)

        with (
            patch("litexplorer.external.grobid.GrobidClient", _mock_grobid_cls(refs)),
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "read_bytes", return_value=b"fake pdf"),
        ):
            svc2.enrich_from_grobid(seed.id, ss_client=mock_ss)

        stub = db_session.execute(
            select(Work).where(Work.title == "ArXiv Only Paper")
        ).scalar_one_or_none()
        assert stub is not None
        loc = db_session.execute(
            select(WorkLocation).where(WorkLocation.work_id == stub.id)
        ).scalar_one_or_none()
        assert loc is not None
        assert loc.location_type == "preprint"

    def test_venue_name_creates_new_venue(self, db_session):
        """A ref with venue_name → new Venue and VenueAlias created."""
        seed = _make_work(db_session, title="Seed Paper")
        self._seed_pdf_settings(db_session, seed.id)
        svc = self._make_service(db_session)

        refs = [_make_grobid_ref(
            title="Paper With Venue",
            year="2022",
            venue_name="International Conference on Machine Learning",
        )]
        self._run_unresolved(svc, seed.id, refs)

        alias = db_session.execute(
            select(VenueAlias).where(VenueAlias.alias == "International Conference on Machine Learning")
        ).scalar_one_or_none()
        assert alias is not None
        stub = db_session.execute(
            select(Work).where(Work.title == "Paper With Venue")
        ).scalar_one_or_none()
        assert stub is not None
        assert stub.venue_id == alias.venue_id

    def test_venue_name_matches_existing_venue(self, db_session):
        """venue_name matching an existing alias (case-insensitive) reuses that Venue."""
        # Pre-create a venue with an alias
        venue = Venue(name="NeurIPS")
        db_session.add(venue)
        db_session.flush()
        db_session.add(VenueAlias(venue_id=venue.id, alias="NeurIPS", sort_order=0))
        db_session.commit()

        seed = _make_work(db_session, title="Seed Paper")
        self._seed_pdf_settings(db_session, seed.id)
        svc = self._make_service(db_session)

        refs = [_make_grobid_ref(
            title="Paper In NeurIPS",
            year="2023",
            venue_name="neurips",  # matches existing alias case-insensitively
        )]
        self._run_unresolved(svc, seed.id, refs)

        stub = db_session.execute(
            select(Work).where(Work.title == "Paper In NeurIPS")
        ).scalar_one_or_none()
        assert stub is not None
        assert stub.venue_id == venue.id

        # No new Venue should have been created
        venue_count = db_session.query(Venue).count()
        assert venue_count == 1

    def test_no_venue_name_no_venue_assigned(self, db_session):
        """Pattern B ref (no venue_name) → stub Work has venue_id=None."""
        seed = _make_work(db_session, title="Seed Paper")
        self._seed_pdf_settings(db_session, seed.id)
        svc = self._make_service(db_session)

        refs = [_make_grobid_ref(title="No Venue Paper", year="2020")]
        self._run_unresolved(svc, seed.id, refs)

        stub = db_session.execute(
            select(Work).where(Work.title == "No Venue Paper")
        ).scalar_one_or_none()
        assert stub is not None
        assert stub.venue_id is None

    def test_citation_count_zero_on_unresolved(self, db_session):
        """Unresolved stub Work must have citation_count=0."""
        seed = _make_work(db_session, title="Seed Paper")
        self._seed_pdf_settings(db_session, seed.id)
        svc = self._make_service(db_session)

        refs = [_make_grobid_ref(title="Stub Work", year="2018")]
        self._run_unresolved(svc, seed.id, refs)

        stub = db_session.execute(
            select(Work).where(Work.title == "Stub Work")
        ).scalar_one_or_none()
        assert stub is not None
        assert stub.citation_count == 0

    def test_citation_edge_created_between_seed_and_stub(self, db_session):
        """A Citation row must connect the seed Work to the unresolved stub."""
        seed = _make_work(db_session, title="Seed Paper")
        self._seed_pdf_settings(db_session, seed.id)
        svc = self._make_service(db_session)

        refs = [_make_grobid_ref(title="Stub Target", year="2017")]
        self._run_unresolved(svc, seed.id, refs)

        stub = db_session.execute(
            select(Work).where(Work.title == "Stub Target")
        ).scalar_one_or_none()
        assert stub is not None

        cit = db_session.execute(
            select(Citation).where(
                Citation.citing_work_id == seed.id,
                Citation.cited_work_id == stub.id,
            )
        ).scalar_one_or_none()
        assert cit is not None
        assert cit.source == "grobid"


# ---------------------------------------------------------------------------
# GROBID re-run cleanup
# ---------------------------------------------------------------------------


class TestGrobidCleanup:
    """Tests for _cleanup_grobid_citations: re-run dedup via provenance-aware cleanup."""

    def _make_service(self, db_session) -> EnrichmentService:
        mock_oa = MagicMock()
        mock_oa.get_work_by_doi_raw.return_value = None
        mock_oa.get_work_by_id_raw.return_value = None
        mock_oa.get_work_by_arxiv_id_raw.return_value = None
        mock_cr = MagicMock()
        mock_cr.get_work_by_doi_raw.return_value = None
        mock_cr.search_works.return_value = []
        return EnrichmentService(db=db_session, client=mock_oa, crossref_client=mock_cr)

    def _seed_pdf_settings(self, db_session, work_id: int) -> None:
        _make_work_pdf(db_session, work_id)
        _seed_setting(db_session, "grobid_url", "http://localhost:8070")
        _seed_setting(db_session, "pdf_storage_path", "/fake/pdfs")

    def _run(self, svc, work_id, refs):
        """Run enrich_from_grobid with S2 returning no results (all refs → unresolved)."""
        mock_ss = MagicMock()
        mock_ss.search_by_title.return_value = []
        with (
            patch("litexplorer.external.grobid.GrobidClient", _mock_grobid_cls(refs)),
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "read_bytes", return_value=b"fake pdf"),
        ):
            return svc.enrich_from_grobid(work_id, ss_client=mock_ss)

    def test_rerun_deletes_old_dud_and_creates_new_one(self, db_session):
        """Re-running GROBID on a seed: the old unresolved work is deleted and
        a fresh stub is created — no duplicate works accumulate."""
        seed = _make_work(db_session, title="Seed Paper")
        self._seed_pdf_settings(db_session, seed.id)
        svc = self._make_service(db_session)

        ref = _make_grobid_ref(title="Unresolved Dud", year="2018")

        # First run — creates one stub Work
        self._run(svc, seed.id, [ref])

        dud_count_after_first = db_session.query(Work).filter(
            Work.title == "Unresolved Dud"
        ).count()
        assert dud_count_after_first == 1

        first_dud = db_session.execute(
            select(Work).where(Work.title == "Unresolved Dud")
        ).scalar_one()
        first_dud_id = first_dud.id

        # Second run — cleanup deletes old stub, resolution creates a new one
        self._run(svc, seed.id, [ref])

        dud_count_after_second = db_session.query(Work).filter(
            Work.title == "Unresolved Dud"
        ).count()
        assert dud_count_after_second == 1  # still exactly one (not two)

        new_dud = db_session.execute(
            select(Work).where(Work.title == "Unresolved Dud")
        ).scalar_one()
        # The old stub was deleted and a new one was created
        assert new_dud.id != first_dud_id

    def test_rerun_preserves_manually_enriched_work(self, db_session):
        """If a previously-unresolved stub is later given a DOI (manually enriched),
        re-running GROBID must NOT delete it."""
        seed = _make_work(db_session, title="Seed Paper")
        self._seed_pdf_settings(db_session, seed.id)
        svc = self._make_service(db_session)

        ref = _make_grobid_ref(title="Will Be Enriched", year="2019")
        self._run(svc, seed.id, [ref])

        # Simulate manual enrichment: give the stub a DOI
        stub = db_session.execute(
            select(Work).where(Work.title == "Will Be Enriched")
        ).scalar_one()
        stub.doi = "10.1234/enriched"
        db_session.commit()
        stub_id = stub.id

        # Re-run GROBID
        self._run(svc, seed.id, [ref])

        # The enriched work must still exist
        still_there = db_session.get(Work, stub_id)
        assert still_there is not None
        assert still_there.doi == "10.1234/enriched"

    def test_rerun_preserves_work_in_topic_list(self, db_session):
        """A stub work added to a topic list must not be deleted on re-run."""
        seed = _make_work(db_session, title="Seed Paper")
        self._seed_pdf_settings(db_session, seed.id)
        svc = self._make_service(db_session)

        ref = _make_grobid_ref(title="Added To Topic List", year="2020")
        self._run(svc, seed.id, [ref])

        stub = db_session.execute(
            select(Work).where(Work.title == "Added To Topic List")
        ).scalar_one()
        stub_id = stub.id

        # Add stub to a topic list
        project = Project(name="Test Project")
        db_session.add(project)
        db_session.flush()
        tl = TopicList(project_id=project.id, name="TL", color="#aabbcc")
        db_session.add(tl)
        db_session.flush()
        db_session.add(TopicListWork(topic_list_id=tl.id, work_id=stub_id))
        db_session.commit()

        # Re-run GROBID
        self._run(svc, seed.id, [ref])

        # Stub must still exist
        assert db_session.get(Work, stub_id) is not None

    def test_rerun_preserves_openalex_citation(self, db_session):
        """A Citation with source='openalex' from the same seed must not be deleted."""
        seed = _make_work(db_session, title="Seed Paper")
        self._seed_pdf_settings(db_session, seed.id)

        oa_ref = _make_work(db_session, title="OA Reference", openalex_id="W_OA_REF")
        db_session.add(Citation(
            citing_work_id=seed.id,
            cited_work_id=oa_ref.id,
            source="openalex",
        ))
        db_session.commit()

        svc = self._make_service(db_session)

        # Run GROBID with a different ref (unrelated to the OA citation)
        ref = _make_grobid_ref(title="Grobid Only Ref", year="2021")
        self._run(svc, seed.id, [ref])

        # The OA citation must still exist
        oa_cit = db_session.execute(
            select(Citation).where(
                Citation.citing_work_id == seed.id,
                Citation.cited_work_id == oa_ref.id,
                Citation.source == "openalex",
            )
        ).scalar_one_or_none()
        assert oa_cit is not None

    def test_rerun_preserves_semantic_scholar_citation(self, db_session):
        """Citations with source='semantic_scholar' must not be deleted by GROBID cleanup."""
        seed = _make_work(db_session, title="Seed Paper")
        self._seed_pdf_settings(db_session, seed.id)

        s2_ref = _make_work(db_session, title="S2 Reference", openalex_id="W_S2_REF")
        db_session.add(Citation(
            citing_work_id=seed.id,
            cited_work_id=s2_ref.id,
            source="semantic_scholar",
        ))
        db_session.commit()

        svc = self._make_service(db_session)
        ref = _make_grobid_ref(title="New Grobid Ref", year="2022")
        self._run(svc, seed.id, [ref])

        # S2 citation must survive — only grobid-source citations are cleaned up
        cit = db_session.execute(
            select(Citation).where(
                Citation.citing_work_id == seed.id,
                Citation.cited_work_id == s2_ref.id,
                Citation.source == "semantic_scholar",
            )
        ).scalar_one_or_none()
        assert cit is not None

    def test_source_set_to_grobid_after_enrichment(self, db_session):
        """All Citations created by GROBID enrichment must have source='grobid'."""
        seed = _make_work(db_session, title="Seed Paper")
        self._seed_pdf_settings(db_session, seed.id)
        svc = self._make_service(db_session)

        refs = [
            _make_grobid_ref(title="Ref One", year="2019"),
            _make_grobid_ref(title="Ref Two", year="2020"),
        ]
        self._run(svc, seed.id, refs)

        grobid_cits = db_session.execute(
            select(Citation).where(Citation.citing_work_id == seed.id)
        ).scalars().all()
        assert len(grobid_cits) == 2
        assert all(c.source == "grobid" for c in grobid_cits)

    def test_orphaned_dud_with_cross_citations_not_deleted(self, db_session):
        """A dud work that cites another work must not be deleted (it still has citation edges)."""
        seed = _make_work(db_session, title="Seed Paper")
        self._seed_pdf_settings(db_session, seed.id)
        svc = self._make_service(db_session)

        ref = _make_grobid_ref(title="Dud With Citations", year="2018")
        self._run(svc, seed.id, [ref])

        stub = db_session.execute(
            select(Work).where(Work.title == "Dud With Citations")
        ).scalar_one()
        stub_id = stub.id

        # Give the stub an outgoing citation to another work (different source)
        other = _make_work(db_session, title="Other Work")
        db_session.add(Citation(
            citing_work_id=stub_id,
            cited_work_id=other.id,
            source="openalex",
        ))
        db_session.commit()

        # Re-run — cleanup deletes the GROBID citation from seed→stub, but
        # stub still has an outgoing citation so it must NOT be deleted
        self._run(svc, seed.id, [ref])

        assert db_session.get(Work, stub_id) is not None
