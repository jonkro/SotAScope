"""Tests for BibTeX export service and API endpoints."""

from __future__ import annotations

import pytest

from sotascope.models.library import Author, Venue, VenueAlias, Work, WorkAuthor, WorkLocation
from sotascope.models.project import Project, TopicList, TopicListWork
from sotascope.services.bibtex_export import _bibtex_key, _generate_entry, works_to_bibtex


# ---------------------------------------------------------------------------
# Helpers: create fully-loaded Work objects (with relationships)
# ---------------------------------------------------------------------------


def _make_work(
    db_session,
    *,
    title: str = "A Great Paper",
    year: int | None = 2023,
    doi: str | None = None,
    arxiv_id: str | None = None,
    bibtex_key: str | None = None,
    bibtex_entry: str | None = None,
    venue: Venue | None = None,
    authors: list[str] | None = None,
    location_url: str | None = None,
) -> Work:
    work = Work(
        title=title,
        publication_year=year,
        doi=doi,
        arxiv_id=arxiv_id,
        bibtex_key=bibtex_key,
        bibtex_entry=bibtex_entry,
        venue=venue,
    )
    db_session.add(work)
    db_session.flush()

    for pos, name in enumerate(authors or []):
        author = Author(name=name)
        db_session.add(author)
        db_session.flush()
        db_session.add(WorkAuthor(work_id=work.id, author_id=author.id, position=pos))

    if location_url:
        db_session.add(
            WorkLocation(work_id=work.id, location_type="venue", url=location_url, is_primary=True)
        )

    db_session.commit()
    db_session.refresh(work)
    return work


def _make_journal(db_session, name: str = "Nature") -> Venue:
    venue = Venue(name=name, venue_type="journal")
    db_session.add(venue)
    db_session.flush()
    db_session.add(VenueAlias(venue_id=venue.id, alias=name, sort_order=0))
    db_session.commit()
    db_session.refresh(venue)
    return venue


def _make_conference(db_session, name: str = "ICML") -> Venue:
    venue = Venue(name=name, venue_type="conference")
    db_session.add(venue)
    db_session.flush()
    db_session.add(VenueAlias(venue_id=venue.id, alias=name, sort_order=0))
    db_session.commit()
    db_session.refresh(venue)
    return venue


# ---------------------------------------------------------------------------
# _bibtex_key tests
# ---------------------------------------------------------------------------


class TestBibtexKey:
    def test_uses_stored_key(self, db_session):
        work = _make_work(db_session, bibtex_key="Smith2023Foo")
        assert _bibtex_key(work) == "Smith2023Foo"

    def test_generates_from_author_year_title(self, db_session):
        work = _make_work(db_session, year=2021, authors=["Alice Smith"], title="The Best Paper")
        key = _bibtex_key(work)
        assert "Smith" in key
        assert "2021" in key
        # "Best" is the first non-stopword
        assert "Best" in key

    def test_handles_no_author(self, db_session):
        work = _make_work(db_session, year=2020, title="Something Novel")
        key = _bibtex_key(work)
        assert "2020" in key
        assert "Something" in key

    def test_handles_no_year(self, db_session):
        work = _make_work(db_session, year=None, authors=["Bob Jones"], title="Neural Networks")
        key = _bibtex_key(work)
        assert "Jones" in key
        assert "Neural" in key

    def test_fallback_to_work_id(self, db_session):
        """Works with no usable data fall back to 'work{id}'."""
        work = Work(title="")
        db_session.add(work)
        db_session.commit()
        db_session.refresh(work)
        key = _bibtex_key(work)
        assert key == f"work{work.id}"


# ---------------------------------------------------------------------------
# _generate_entry tests
# ---------------------------------------------------------------------------


class TestGenerateEntry:
    def test_journal_entry(self, db_session):
        venue = _make_journal(db_session, "Science")
        work = _make_work(
            db_session,
            title="Great Discovery",
            year=2022,
            doi="10.1000/test",
            venue=venue,
            authors=["Jane Doe", "John Smith"],
        )
        entry = _generate_entry(work)
        assert entry.startswith("@article{")
        assert "journal = {Science}" in entry
        assert "title = {Great Discovery}" in entry
        assert "author = {Jane Doe and John Smith}" in entry
        assert "year = {2022}" in entry
        assert "doi = {10.1000/test}" in entry

    def test_conference_entry(self, db_session):
        venue = _make_conference(db_session, "NeurIPS")
        work = _make_work(db_session, venue=venue, authors=["Alice B"])
        entry = _generate_entry(work)
        assert entry.startswith("@inproceedings{")
        assert "booktitle = {NeurIPS}" in entry

    def test_arxiv_only_entry(self, db_session):
        work = _make_work(db_session, arxiv_id="2301.00001", title="Preprint Paper")
        entry = _generate_entry(work)
        assert entry.startswith("@misc{")
        assert "eprint = {2301.00001}" in entry
        assert "archivePrefix = {arXiv}" in entry

    def test_url_included(self, db_session):
        work = _make_work(
            db_session, location_url="https://example.com/paper.pdf"
        )
        entry = _generate_entry(work)
        assert "url = {https://example.com/paper.pdf}" in entry

    def test_no_venue_no_arxiv_defaults_to_article(self, db_session):
        work = _make_work(db_session, doi="10.9999/x")
        entry = _generate_entry(work)
        assert entry.startswith("@article{")

    def test_venue_preferred_alias_used(self, db_session):
        """First alias by sort_order should be used as venue display name."""
        venue = Venue(name="International Conference on Machine Learning", venue_type="conference")
        db_session.add(venue)
        db_session.flush()
        # Add two aliases; preferred is the one with lower sort_order
        db_session.add(VenueAlias(venue_id=venue.id, alias="ICML", sort_order=0))
        db_session.add(VenueAlias(venue_id=venue.id, alias="International Conference on Machine Learning", sort_order=1))
        db_session.commit()
        db_session.refresh(venue)
        work = _make_work(db_session, venue=venue)
        entry = _generate_entry(work)
        assert "booktitle = {ICML}" in entry


# ---------------------------------------------------------------------------
# works_to_bibtex tests
# ---------------------------------------------------------------------------


class TestWorksToBibtex:
    def test_empty_list(self):
        assert works_to_bibtex([]) == ""

    def test_uses_stored_bibtex_entry(self, db_session):
        raw = "@article{key2023,\n  title = {Stored},\n}"
        work = _make_work(db_session, bibtex_entry=raw)
        result = works_to_bibtex([work])
        assert raw in result

    def test_generates_when_no_stored_entry(self, db_session):
        work = _make_work(db_session, title="Generated Paper", year=2024)
        result = works_to_bibtex([work])
        assert "@" in result
        assert "Generated Paper" in result

    def test_multiple_works_separated_by_blank_line(self, db_session):
        w1 = _make_work(db_session, title="First Work", doi="10.1/a")
        w2 = _make_work(db_session, title="Second Work", doi="10.1/b")
        result = works_to_bibtex([w1, w2])
        assert "\n\n" in result
        assert "First Work" in result
        assert "Second Work" in result

    def test_ends_with_newline(self, db_session):
        work = _make_work(db_session, title="Test")
        result = works_to_bibtex([work])
        assert result.endswith("\n")


# ---------------------------------------------------------------------------
# Round-trip: BibTeX import → export
# ---------------------------------------------------------------------------


class TestBibtexRoundTrip:
    def test_stored_entry_roundtrip(self, db_session):
        """A work imported from BibTeX gets its entry back verbatim."""
        raw = (
            "@article{Smith2020Deep,\n"
            "  title = {Deep Learning for Everyone},\n"
            "  author = {Smith, John and Doe, Jane},\n"
            "  year = {2020},\n"
            "  journal = {Nature},\n"
            "  doi = {10.1000/nature2020},\n"
            "}"
        )
        work = _make_work(db_session, bibtex_entry=raw, bibtex_key="Smith2020Deep")
        result = works_to_bibtex([work])
        assert raw in result

    def test_arxiv_only_work_has_eprint_field(self, db_session):
        """Works without DOI but with arxiv_id export with eprint field."""
        work = _make_work(
            db_session,
            title="Preprint Paper",
            year=2023,
            arxiv_id="2301.00001",
            doi=None,
        )
        result = works_to_bibtex([work])
        assert "eprint = {2301.00001}" in result
        assert "archivePrefix = {arXiv}" in result
        assert "@misc{" in result

    def test_special_characters_in_title(self, db_session):
        """Works with special characters in title/author are included faithfully."""
        work = _make_work(
            db_session,
            title="Über-efficient: A Survey on Résumé Parsing & NLP",
            year=2022,
            doi="10.1/special",
            authors=["Müller, Hans", "Lefèvre, André"],
        )
        result = works_to_bibtex([work])
        assert "Über-efficient" in result
        assert "Müller, Hans and Lefèvre, André" in result

    def test_bibtex_key_in_output(self, db_session):
        """Explicit bibtex_key is preserved in the generated entry."""
        work = _make_work(
            db_session,
            title="Attention Is All You Need",
            year=2017,
            doi="10.1000/attention",
            bibtex_key="Vaswani2017Attention",
        )
        result = works_to_bibtex([work])
        assert "Vaswani2017Attention" in result

    def test_work_with_only_title_and_year(self, db_session):
        """Works with no DOI, no arXiv, no authors still produce valid output."""
        work = _make_work(db_session, title="Minimal Work", year=2019)
        result = works_to_bibtex([work])
        assert "@" in result
        assert "Minimal Work" in result
        assert "year = {2019}" in result


# ---------------------------------------------------------------------------
# API: GET /api/works/export/bibtex
# ---------------------------------------------------------------------------


class TestLibraryExportEndpoint:
    def test_export_all(self, client, db_session):
        _make_work(db_session, title="Paper One", doi="10.1/one")
        _make_work(db_session, title="Paper Two", doi="10.1/two")
        resp = client.get("/api/works/export/bibtex")
        assert resp.status_code == 200
        assert "Paper One" in resp.text
        assert "Paper Two" in resp.text
        assert "attachment" in resp.headers["content-disposition"]
        assert resp.headers["content-disposition"].endswith('.bib"')

    def test_export_selected(self, client, db_session):
        w1 = _make_work(db_session, title="Keep This", doi="10.1/keep")
        _make_work(db_session, title="Skip This", doi="10.1/skip")
        resp = client.get(f"/api/works/export/bibtex?work_ids={w1.id}")
        assert resp.status_code == 200
        assert "Keep This" in resp.text
        assert "Skip This" not in resp.text

    def test_export_invalid_work_ids(self, client):
        resp = client.get("/api/works/export/bibtex?work_ids=not-a-number")
        assert resp.status_code == 422

    def test_export_empty_library(self, client):
        resp = client.get("/api/works/export/bibtex")
        assert resp.status_code == 200
        assert resp.text == ""


# ---------------------------------------------------------------------------
# API: GET /api/projects/{id}/export/bibtex
# ---------------------------------------------------------------------------


class TestProjectExportEndpoint:
    @pytest.fixture()
    def project_with_seeds(self, db_session):
        project = Project(name="My Project")
        db_session.add(project)
        db_session.flush()

        tl = TopicList(project_id=project.id, name="Main", color="#3b82f6")
        db_session.add(tl)
        db_session.flush()

        w1 = _make_work(db_session, title="Seed One", doi="10.1/s1")
        w2 = _make_work(db_session, title="Seed Two", doi="10.1/s2")
        db_session.add(TopicListWork(topic_list_id=tl.id, work_id=w1.id))
        db_session.add(TopicListWork(topic_list_id=tl.id, work_id=w2.id))
        db_session.commit()
        return project, w1, w2

    def test_export_all_seeds(self, client, project_with_seeds):
        project, w1, w2 = project_with_seeds
        resp = client.get(f"/api/projects/{project.id}/export/bibtex")
        assert resp.status_code == 200
        assert "Seed One" in resp.text
        assert "Seed Two" in resp.text
        assert "My_Project.bib" in resp.headers["content-disposition"]

    def test_export_selected_seeds(self, client, project_with_seeds):
        project, w1, w2 = project_with_seeds
        resp = client.get(f"/api/projects/{project.id}/export/bibtex?work_ids={w1.id}")
        assert resp.status_code == 200
        assert "Seed One" in resp.text
        assert "Seed Two" not in resp.text

    def test_project_not_found(self, client):
        resp = client.get("/api/projects/9999/export/bibtex")
        assert resp.status_code == 404

    def test_project_no_seeds(self, client, db_session):
        project = Project(name="Empty")
        db_session.add(project)
        db_session.commit()
        resp = client.get(f"/api/projects/{project.id}/export/bibtex")
        assert resp.status_code == 200
        assert resp.text == ""

    def test_invalid_work_ids(self, client, db_session):
        project = Project(name="P")
        db_session.add(project)
        db_session.commit()
        resp = client.get(f"/api/projects/{project.id}/export/bibtex?work_ids=abc")
        assert resp.status_code == 422
