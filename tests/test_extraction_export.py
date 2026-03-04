"""Tests for CSV and LaTeX export of extraction schemas."""

from __future__ import annotations

import csv
import io
import re

import pytest

from litexplorer.models.extraction import ExtractionColumn, ExtractionSchema
from litexplorer.models.library import Work, WorkNote
from litexplorer.models.project import Project
from litexplorer.services.extraction_export import (
    _escape_latex,
    export_as_csv,
    export_as_latex,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def project(db_session):
    p = Project(name="Export Test Project")
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture()
def schema(db_session, project):
    s = ExtractionSchema(
        title="My Survey Table",
        description="Test schema for export",
        project_id=project.id,
    )
    db_session.add(s)
    db_session.commit()
    return s


@pytest.fixture()
def columns(db_session, schema):
    col1 = ExtractionColumn(
        schema_id=schema.id,
        name="Learning paradigm",
        prompt="What learning paradigm does the paper use?",
        allowed_values=["supervised", "unsupervised", "other"],
        sort_order=0,
    )
    col2 = ExtractionColumn(
        schema_id=schema.id,
        name="Dataset",
        prompt="What dataset(s) are used?",
        sort_order=1,
    )
    db_session.add_all([col1, col2])
    db_session.commit()
    return [col1, col2]


@pytest.fixture()
def works(db_session):
    w1 = Work(title="Deep Learning Survey", doi="10.1/dl", publication_year=2023)
    w2 = Work(title="RL Basics & Beyond", doi="10.2/rl", publication_year=2021)
    db_session.add_all([w1, w2])
    db_session.commit()
    return [w1, w2]


def _make_note(db_session, work, col, schema, content):
    note = WorkNote(
        work_id=work.id,
        project_id=schema.project_id,
        content=content,
        note_type=f"{schema.title} / {col.name}"[:64],
        provenance="ai",
    )
    db_session.add(note)
    db_session.commit()
    return note


# ---------------------------------------------------------------------------
# Unit tests: _escape_latex
# ---------------------------------------------------------------------------


def test_escape_latex_special_chars():
    assert _escape_latex("10% & more") == r"10\% \& more"
    # ^ maps to \textasciicircum{}, _ maps to \_
    assert _escape_latex("x_1 + y^2") == r"x\_1 + y\textasciicircum{}2"
    # Backslash
    assert _escape_latex("a\\b") == r"a\textbackslash{}b"
    # Dollar and hash
    assert _escape_latex("$100 #1") == r"\$100 \#1"


def test_escape_latex_no_double_escape():
    # Backslash must be handled first; already-replaced text shouldn't be re-escaped
    result = _escape_latex("a & b")
    assert result == r"a \& b"
    assert result.count("\\") == 1


def test_escape_latex_braces():
    assert _escape_latex("{x}") == r"\{x\}"


def test_escape_latex_tilde_caret():
    assert _escape_latex("~foo^bar") == r"\textasciitilde{}foo\textasciicircum{}bar"


# ---------------------------------------------------------------------------
# Unit tests: export_as_csv
# ---------------------------------------------------------------------------


def test_csv_header_row(schema, columns, works):
    result = export_as_csv(schema, columns, works, {})
    reader = csv.reader(io.StringIO(result))
    header = next(reader)
    assert header == ["Paper", "Learning paradigm", "Dataset"]


def test_csv_paper_cell_includes_year(schema, columns, works):
    result = export_as_csv(schema, columns, works, {})
    reader = csv.reader(io.StringIO(result))
    next(reader)  # skip header
    row1 = next(reader)
    assert "Deep Learning Survey" in row1[0]
    assert "2023" in row1[0]


def test_csv_missing_note_produces_empty_cell(schema, columns, works):
    result = export_as_csv(schema, columns, works, {})
    reader = csv.reader(io.StringIO(result))
    next(reader)
    row = next(reader)
    assert row[1] == ""  # no note for col1
    assert row[2] == ""  # no note for col2


def test_csv_cell_content(db_session, schema, columns, works):
    notes = {
        (works[0].id, columns[0].id): _make_note(db_session, works[0], columns[0], schema, "supervised"),
        (works[0].id, columns[1].id): _make_note(db_session, works[0], columns[1], schema, "ImageNet"),
    }
    result = export_as_csv(schema, columns, works, notes)
    reader = csv.reader(io.StringIO(result))
    next(reader)
    row1 = next(reader)
    assert row1[1] == "supervised"
    assert row1[2] == "ImageNet"
    # Second work has no notes
    row2 = next(reader)
    assert row2[1] == ""
    assert row2[2] == ""


def test_csv_special_chars_preserved(schema, columns):
    """CSV must preserve special characters verbatim (the csv module handles quoting)."""
    work = Work(title='Survey & "Analysis"', doi="10.3/x", publication_year=2022)
    work.id = 99
    result = export_as_csv(schema, columns, [work], {})
    # Parse the CSV to get the actual cell value (csv module handles quote escaping)
    reader = csv.reader(io.StringIO(result))
    next(reader)  # skip header
    row = next(reader)
    assert 'Survey & "Analysis"' in row[0]


def test_csv_empty_works_header_only(schema, columns):
    result = export_as_csv(schema, columns, [], {})
    rows = list(csv.reader(io.StringIO(result)))
    assert len(rows) == 1  # header only
    assert rows[0][0] == "Paper"


def test_csv_work_without_year(schema, columns):
    work = Work(title="No Year Paper", doi="10.4/ny", publication_year=None)
    work.id = 100
    result = export_as_csv(schema, columns, [work], {})
    reader = csv.reader(io.StringIO(result))
    next(reader)
    row = next(reader)
    assert row[0] == "No Year Paper"  # no parentheses


# ---------------------------------------------------------------------------
# Unit tests: export_as_latex
# ---------------------------------------------------------------------------


def test_latex_structure(schema, columns, works):
    result = export_as_latex(schema, columns, works, {})
    assert r"\begin{table}" in result
    assert r"\end{table}" in result
    assert r"\begin{tabular}" in result
    assert r"\end{tabular}" in result
    assert r"\toprule" in result
    assert r"\midrule" in result
    assert r"\bottomrule" in result


def test_latex_caption_and_label(schema, columns, works):
    result = export_as_latex(schema, columns, works, {})
    assert r"\caption{My Survey Table}" in result
    assert r"\label{tab:my_survey_table}" in result


def test_latex_column_spec(schema, columns, works):
    # col1 has allowed_values → 'c'; col2 has none → 'l'
    result = export_as_latex(schema, columns, works, {})
    # Find tabular spec
    m = re.search(r'\\begin\{tabular\}\{([^}]+)\}', result)
    assert m is not None
    spec = m.group(1)
    assert spec == "lcl"  # Paper=l, col1(constrained)=c, col2(free)=l


def test_latex_constrained_header_rotated(schema, columns, works):
    result = export_as_latex(schema, columns, works, {})
    # col1 is constrained → header should be \rotatebox{90}{...}
    assert r"\rotatebox{90}{Learning paradigm}" in result
    # col2 is free-text → plain header (not rotated)
    assert "Dataset" in result
    assert r"\rotatebox{90}{Dataset}" not in result


def test_latex_special_chars_escaped(schema, columns):
    """Special chars in work title and cell content must be escaped."""
    work = Work(title="A & B: 50% discount", doi="10.5/s", publication_year=2020)
    work.id = 101
    result = export_as_latex(schema, columns, [work], {})
    assert r"A \& B: 50\% discount" in result


def test_latex_cell_content_escaped(db_session, schema, columns, works):
    note = _make_note(db_session, works[0], columns[0], schema, "semi-supervised & active")
    notes = {(works[0].id, columns[0].id): note}
    result = export_as_latex(schema, columns, works, notes)
    assert r"semi-supervised \& active" in result


def test_latex_empty_cell_for_missing_note(schema, columns):
    """Each row must have N column separators regardless of missing notes."""
    work = Work(title="Simple Paper", doi="10.6/sp", publication_year=2019)
    work.id = 102
    result = export_as_latex(schema, columns, [work], {})
    lines = result.splitlines()
    data_lines = [
        l for l in lines
        if r' \\' in l
        and not any(cmd in l for cmd in [r'\toprule', r'\midrule', r'\bottomrule', r'\begin', r'\end'])
    ]
    # header row + 1 work row
    assert len(data_lines) == 2
    # Each data row: Paper & col1 & col2 → 2 '&' separators
    for line in data_lines:
        assert line.count('&') == 2


def test_latex_empty_works(schema, columns):
    result = export_as_latex(schema, columns, [], {})
    assert r"\toprule" in result
    assert r"\bottomrule" in result
    # No data rows between midrule and bottomrule
    lines = result.splitlines()
    midrule_idx = next(i for i, l in enumerate(lines) if r'\midrule' in l)
    bottomrule_idx = next(i for i, l in enumerate(lines) if r'\bottomrule' in l)
    assert bottomrule_idx == midrule_idx + 1


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


def test_export_csv_basic(client, db_session, schema, columns, works):
    # Add a note for works[0], col[0]
    note = _make_note(db_session, works[0], columns[0], schema, "supervised")
    work_ids = ",".join(str(w.id) for w in works)
    resp = client.get(f"/api/extraction/schemas/{schema.id}/export?format=csv&work_ids={work_ids}")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "attachment" in resp.headers.get("content-disposition", "")
    assert ".csv" in resp.headers.get("content-disposition", "")
    # Check content
    reader = csv.reader(io.StringIO(resp.text))
    header = next(reader)
    assert header[0] == "Paper"
    assert "Learning paradigm" in header


def test_export_latex_basic(client, db_session, schema, columns, works):
    work_ids = ",".join(str(w.id) for w in works)
    resp = client.get(f"/api/extraction/schemas/{schema.id}/export?format=latex&work_ids={work_ids}")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert ".tex" in resp.headers.get("content-disposition", "")
    assert r"\begin{table}" in resp.text
    assert r"\toprule" in resp.text


def test_export_invalid_format(client, schema, works):
    work_ids = str(works[0].id)
    resp = client.get(f"/api/extraction/schemas/{schema.id}/export?format=pdf&work_ids={work_ids}")
    assert resp.status_code == 422


def test_export_schema_not_found(client):
    resp = client.get("/api/extraction/schemas/9999/export?format=csv")
    assert resp.status_code == 404


def test_export_work_ids_filter(client, db_session, schema, columns, works):
    """Only the specified work_ids should appear in the export."""
    _make_note(db_session, works[0], columns[0], schema, "supervised")
    _make_note(db_session, works[1], columns[0], schema, "unsupervised")
    # Export only works[0]
    resp = client.get(
        f"/api/extraction/schemas/{schema.id}/export?format=csv&work_ids={works[0].id}"
    )
    assert resp.status_code == 200
    text = resp.text
    assert works[0].title in text
    assert works[1].title not in text


def test_export_column_ids_filter(client, db_session, schema, columns, works):
    """Only the specified column_ids should appear in the export."""
    work_ids = ",".join(str(w.id) for w in works)
    resp = client.get(
        f"/api/extraction/schemas/{schema.id}/export"
        f"?format=csv&work_ids={work_ids}&column_ids={columns[0].id}"
    )
    assert resp.status_code == 200
    reader = csv.reader(io.StringIO(resp.text))
    header = next(reader)
    assert "Learning paradigm" in header
    assert "Dataset" not in header


def test_export_omit_work_ids_uses_all_with_notes(client, db_session, schema, columns, works):
    """When work_ids is omitted, all works with notes for the schema are exported."""
    _make_note(db_session, works[0], columns[0], schema, "supervised")
    # works[1] has no notes — it should NOT appear
    resp = client.get(f"/api/extraction/schemas/{schema.id}/export?format=csv")
    assert resp.status_code == 200
    text = resp.text
    assert works[0].title in text
    assert works[1].title not in text


def test_export_empty_no_notes_csv(client, db_session, schema, columns):
    """Export with no notes and no work_ids → header-only CSV."""
    resp = client.get(f"/api/extraction/schemas/{schema.id}/export?format=csv")
    assert resp.status_code == 200
    rows = list(csv.reader(io.StringIO(resp.text)))
    assert len(rows) == 1  # header only
    assert rows[0][0] == "Paper"


def test_export_empty_no_notes_latex(client, db_session, schema, columns):
    """Export with no notes and no work_ids → empty LaTeX table."""
    resp = client.get(f"/api/extraction/schemas/{schema.id}/export?format=latex")
    assert resp.status_code == 200
    assert r"\begin{table}" in resp.text
    assert r"\bottomrule" in resp.text
