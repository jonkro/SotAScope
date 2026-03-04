"""Tests for the structured extraction API and service."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from litexplorer.models.extraction import ExtractionColumn, ExtractionSchema
from litexplorer.models.library import Work, WorkNote
from litexplorer.models.project import Project
from litexplorer.services.extraction import (
    assemble_extraction_prompt,
    parse_extraction_response,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def project(db_session):
    p = Project(name="Test Project")
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture()
def work(db_session):
    w = Work(title="A Survey of Deep Learning", doi="10.1234/dl", publication_year=2023)
    db_session.add(w)
    db_session.commit()
    return w


@pytest.fixture()
def schema(db_session, project):
    s = ExtractionSchema(
        title="ML Survey Table",
        description="Classify learning paradigm and dataset",
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
        description="e.g. supervised, unsupervised, or other",
        allowed_values=["supervised", "unsupervised", "other"],
        sort_order=0,
    )
    col2 = ExtractionColumn(
        schema_id=schema.id,
        name="Dataset",
        prompt="What dataset(s) are used for evaluation?",
        sort_order=1,
    )
    db_session.add_all([col1, col2])
    db_session.commit()
    return [col1, col2]


# ---------------------------------------------------------------------------
# Schema CRUD tests
# ---------------------------------------------------------------------------


def test_create_schema(client, project):
    resp = client.post("/api/extraction/schemas", json={
        "title": "Table 1",
        "description": "Overview table",
        "project_id": project.id,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Table 1"
    assert data["description"] == "Overview table"
    assert data["project_id"] == project.id
    assert data["columns"] == []


def test_create_schema_global(client):
    resp = client.post("/api/extraction/schemas", json={"title": "Global Table"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["project_id"] is None


def test_list_schemas_by_project(client, project, schema, db_session):
    # Add a second schema with no project
    db_session.add(ExtractionSchema(title="Other Schema"))
    db_session.commit()

    resp = client.get("/api/extraction/schemas", params={"project_id": project.id})
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["title"] == "ML Survey Table"


def test_list_schemas_no_filter(client, project, schema, db_session):
    db_session.add(ExtractionSchema(title="Other Schema"))
    db_session.commit()

    resp = client.get("/api/extraction/schemas")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_get_schema_with_columns(client, schema, columns):
    resp = client.get(f"/api/extraction/schemas/{schema.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "ML Survey Table"
    assert len(data["columns"]) == 2
    col_names = {c["name"] for c in data["columns"]}
    assert col_names == {"Learning paradigm", "Dataset"}


def test_get_schema_not_found(client):
    resp = client.get("/api/extraction/schemas/99999")
    assert resp.status_code == 404


def test_update_schema(client, schema):
    resp = client.put(f"/api/extraction/schemas/{schema.id}", json={
        "title": "Updated Title",
        "description": "New description",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Updated Title"
    assert data["description"] == "New description"


def test_delete_schema(client, schema, columns, db_session):
    schema_id = schema.id
    col_ids = [c.id for c in columns]

    resp = client.delete(f"/api/extraction/schemas/{schema_id}")
    assert resp.status_code == 204

    # Schema and columns should be gone
    assert db_session.get(ExtractionSchema, schema_id) is None
    for cid in col_ids:
        assert db_session.get(ExtractionColumn, cid) is None


def test_delete_schema_not_found(client):
    resp = client.delete("/api/extraction/schemas/99999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Column CRUD tests
# ---------------------------------------------------------------------------


def test_create_column(client, schema):
    resp = client.post(f"/api/extraction/schemas/{schema.id}/columns", json={
        "name": "Method",
        "prompt": "What method does the paper propose?",
        "sort_order": 2,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Method"
    assert data["schema_id"] == schema.id
    assert data["allowed_values"] is None


def test_create_column_with_allowed_values(client, schema):
    resp = client.post(f"/api/extraction/schemas/{schema.id}/columns", json={
        "name": "Task type",
        "prompt": "What task does the paper address?",
        "allowed_values": ["classification", "regression", "other"],
        "sort_order": 3,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["allowed_values"] == ["classification", "regression", "other"]


def test_create_column_schema_not_found(client):
    resp = client.post("/api/extraction/schemas/99999/columns", json={
        "name": "X",
        "prompt": "Y",
    })
    assert resp.status_code == 404


def test_update_column(client, columns):
    col = columns[0]
    resp = client.put(f"/api/extraction/columns/{col.id}", json={
        "name": "Learning type",
        "allowed_values": ["supervised", "self-supervised", "unsupervised"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Learning type"
    assert "self-supervised" in data["allowed_values"]


def test_update_column_not_found(client):
    resp = client.put("/api/extraction/columns/99999", json={"name": "x"})
    assert resp.status_code == 404


def test_delete_column(client, columns, db_session):
    col_id = columns[0].id
    resp = client.delete(f"/api/extraction/columns/{col_id}")
    assert resp.status_code == 204
    assert db_session.get(ExtractionColumn, col_id) is None


def test_delete_column_not_found(client):
    resp = client.delete("/api/extraction/columns/99999")
    assert resp.status_code == 404


def test_reorder_columns(client, schema, columns):
    col1, col2 = columns
    resp = client.put(f"/api/extraction/schemas/{schema.id}/columns/reorder", json={
        "columns": [
            {"column_id": col1.id, "sort_order": 10},
            {"column_id": col2.id, "sort_order": 5},
        ]
    })
    assert resp.status_code == 200
    result = resp.json()
    # Returned in sort_order ascending order (col2 first now)
    assert result[0]["id"] == col2.id
    assert result[0]["sort_order"] == 5
    assert result[1]["id"] == col1.id
    assert result[1]["sort_order"] == 10


# ---------------------------------------------------------------------------
# assemble_extraction_prompt tests
# ---------------------------------------------------------------------------


def test_assemble_prompt_structure(schema, columns):
    system_text, user_message = assemble_extraction_prompt(
        schema=schema,
        columns=columns,
        paper_text="Full text here.",
        paper_title="Deep Learning Survey",
        paper_year=2023,
        system_prefix="",
    )

    # System prompt includes table title and output format
    assert "ML Survey Table" in system_text
    assert "JSON" in system_text
    assert "columns" in system_text
    # Allowed values instruction present because col1 has them
    assert "allowed values" in system_text.lower()

    # User message includes title and year
    assert "Deep Learning Survey" in user_message
    assert "2023" in user_message
    # Paper text included
    assert "Full text here." in user_message
    # Column names and prompts present
    assert "Learning paradigm" in user_message
    assert "Dataset" in user_message
    assert "supervised" in user_message  # from allowed_values


def test_assemble_prompt_no_text(schema, columns):
    system_text, user_message = assemble_extraction_prompt(
        schema=schema,
        columns=columns,
        paper_text=None,
        paper_title="Title Only",
        paper_year=None,
        system_prefix="",
    )
    assert "No full text available" in user_message
    assert "n.d." in user_message


def test_assemble_prompt_with_system_prefix(schema, columns):
    system_text, user_message = assemble_extraction_prompt(
        schema=schema,
        columns=columns,
        paper_text=None,
        paper_title="Title",
        paper_year=2020,
        system_prefix="IMPORTANT: focus on networking papers.",
    )
    assert system_text.startswith("IMPORTANT: focus on networking papers.")
    # Fixed frame follows the prefix
    assert "research assistant" in system_text


def test_assemble_prompt_schema_description(schema, columns):
    system_text, _ = assemble_extraction_prompt(
        schema=schema,
        columns=columns,
        paper_text=None,
        paper_title="T",
        paper_year=2020,
    )
    assert "Classify learning paradigm and dataset" in system_text


def test_assemble_prompt_column_description_and_allowed_values(schema, columns):
    _, user_message = assemble_extraction_prompt(
        schema=schema,
        columns=columns,
        paper_text="text",
        paper_title="T",
        paper_year=2020,
    )
    # Column description included
    assert "supervised, unsupervised, or other" in user_message
    # Allowed values listed
    assert "supervised" in user_message
    assert "unsupervised" in user_message
    assert "other" in user_message


# ---------------------------------------------------------------------------
# parse_extraction_response tests
# ---------------------------------------------------------------------------


def _make_columns():
    col1 = MagicMock(spec=ExtractionColumn)
    col1.id = 1
    col1.name = "Learning paradigm"
    col1.allowed_values = ["supervised", "unsupervised", "other"]

    col2 = MagicMock(spec=ExtractionColumn)
    col2.id = 2
    col2.name = "Dataset"
    col2.allowed_values = None

    return [col1, col2]


def test_parse_valid_json():
    columns = _make_columns()
    response = json.dumps({
        "columns": {
            "Learning paradigm": {"answer": "supervised", "reasoning": "The paper trains on labeled data."},
            "Dataset": {"answer": "ImageNet", "reasoning": "Mentioned in section 4."},
        }
    })
    result = parse_extraction_response(response, columns)
    assert result[1]["answer"] == "supervised"
    assert result[1]["reasoning"] == "The paper trains on labeled data."
    assert result[2]["answer"] == "ImageNet"


def test_parse_invalid_json_fallback():
    columns = _make_columns()
    response = "Sorry, I cannot answer that."
    result = parse_extraction_response(response, columns)
    # Both columns get the raw text as answer
    assert result[1]["answer"] == response
    assert result[2]["answer"] == response
    assert result[1]["reasoning"] == ""


def test_parse_allowed_values_case_insensitive():
    columns = _make_columns()
    response = json.dumps({
        "columns": {
            "Learning paradigm": {"answer": "SUPERVISED", "reasoning": "x"},
            "Dataset": {"answer": "CIFAR-10", "reasoning": "y"},
        }
    })
    result = parse_extraction_response(response, columns)
    # Should match and return canonical spelling
    assert result[1]["answer"] == "supervised"
    # No allowed_values constraint → raw answer preserved
    assert result[2]["answer"] == "CIFAR-10"


def test_parse_allowed_values_no_match_keeps_raw():
    columns = _make_columns()
    response = json.dumps({
        "columns": {
            "Learning paradigm": {"answer": "reinforcement", "reasoning": "x"},
            "Dataset": {"answer": "MNIST", "reasoning": "y"},
        }
    })
    result = parse_extraction_response(response, columns)
    # "reinforcement" not in allowed_values — keep raw
    assert result[1]["answer"] == "reinforcement"


def test_parse_fenced_code_block():
    columns = _make_columns()
    inner = json.dumps({
        "columns": {
            "Learning paradigm": {"answer": "unsupervised", "reasoning": "No labels used."},
            "Dataset": {"answer": "STL-10", "reasoning": "Section 5."},
        }
    })
    response = f"```json\n{inner}\n```"
    result = parse_extraction_response(response, columns)
    assert result[1]["answer"] == "unsupervised"
    assert result[2]["answer"] == "STL-10"


def test_parse_missing_column_in_response():
    columns = _make_columns()
    response = json.dumps({
        "columns": {
            "Learning paradigm": {"answer": "supervised", "reasoning": "yes"},
            # "Dataset" is missing
        }
    })
    result = parse_extraction_response(response, columns)
    assert result[1]["answer"] == "supervised"
    assert result[2]["answer"] == ""
    assert result[2]["reasoning"] == ""


# ---------------------------------------------------------------------------
# Extract endpoint tests
# ---------------------------------------------------------------------------


def _llm_response_for(col1_answer: str, col1_reasoning: str, col2_answer: str, col2_reasoning: str) -> str:
    return json.dumps({
        "columns": {
            "Learning paradigm": {"answer": col1_answer, "reasoning": col1_reasoning},
            "Dataset": {"answer": col2_answer, "reasoning": col2_reasoning},
        }
    })


def _mock_llm_client(reply: str) -> MagicMock:
    mock = MagicMock()
    mock.chat.return_value = reply
    return mock


def test_extract_for_work_creates_notes(client, db_session, schema, columns, work):
    """Extract endpoint creates WorkNotes with correct note_type and provenance."""
    llm_reply = _llm_response_for("supervised", "Uses labeled data.", "ImageNet", "Mentioned in text.")

    mock_client = _mock_llm_client(llm_reply)

    with patch("litexplorer.api.extraction.make_llm_client", return_value=mock_client), \
         patch("litexplorer.api.extraction._get_pdf_root", return_value=Path("/tmp/fake")):

        # Set required settings so _build_llm_client succeeds
        from litexplorer.models.settings import Setting
        db_session.merge(Setting(key="llm_provider", value="openai"))
        db_session.merge(Setting(key="llm_model_id", value="gpt-4o"))
        db_session.merge(Setting(key="llm_api_key", value="sk-test"))
        db_session.merge(Setting(key="llm_base_url", value=""))
        db_session.merge(Setting(key="llm_system_prompt_prefix", value=""))
        db_session.commit()

        resp = client.post(f"/api/extraction/schemas/{schema.id}/extract/{work.id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["work_id"] == work.id
    assert len(data["columns"]) == 2

    col_names = {c["column_name"] for c in data["columns"]}
    assert col_names == {"Learning paradigm", "Dataset"}

    paradigm_result = next(c for c in data["columns"] if c["column_name"] == "Learning paradigm")
    assert paradigm_result["answer"] == "supervised"
    assert paradigm_result["reasoning"] == "Uses labeled data."
    assert paradigm_result["note"]["provenance"] == "ai"
    assert "ML Survey Table / Learning paradigm" in paradigm_result["note"]["note_type"]

    # Verify notes were persisted in DB (answer + reasoning = 2 notes per column = 4 total)
    notes = db_session.query(WorkNote).filter_by(work_id=work.id).all()
    assert len(notes) == 4  # 2 columns × (answer + reasoning)

    # Check provenances
    assert all(n.provenance == "ai" for n in notes)

    # Check note_types contain schema title
    note_types = {n.note_type for n in notes}
    assert any("ML Survey Table / Learning paradigm" in nt for nt in note_types)
    assert any("ML Survey Table / Dataset" in nt for nt in note_types)
    assert any("reasoning" in nt for nt in note_types)


def test_extract_no_llm_configured(client, db_session, schema, work):
    """Returns 400 when no LLM provider is configured."""
    from litexplorer.models.settings import Setting
    db_session.merge(Setting(key="llm_provider", value=""))
    db_session.merge(Setting(key="llm_model_id", value=""))
    db_session.commit()

    resp = client.post(f"/api/extraction/schemas/{schema.id}/extract/{work.id}")
    assert resp.status_code == 400
    assert "provider" in resp.json()["detail"].lower()


def test_extract_no_model_configured(client, db_session, schema, work):
    """Returns 400 when provider is set but model is not."""
    from litexplorer.models.settings import Setting
    db_session.merge(Setting(key="llm_provider", value="openai"))
    db_session.merge(Setting(key="llm_model_id", value=""))
    db_session.commit()

    resp = client.post(f"/api/extraction/schemas/{schema.id}/extract/{work.id}")
    assert resp.status_code == 400
    assert "model" in resp.json()["detail"].lower()


def test_extract_schema_not_found(client, db_session, work):
    from litexplorer.models.settings import Setting
    db_session.merge(Setting(key="llm_provider", value="openai"))
    db_session.merge(Setting(key="llm_model_id", value="gpt-4o"))
    db_session.commit()

    resp = client.post(f"/api/extraction/schemas/99999/extract/{work.id}")
    assert resp.status_code == 404


def test_extract_batch(client, db_session, schema, columns, work):
    """Batch extraction processes multiple works and collects errors for missing ones."""
    llm_reply = _llm_response_for("unsupervised", "No labels.", "CIFAR-10", "Used in eval.")

    mock_client = _mock_llm_client(llm_reply)

    with patch("litexplorer.api.extraction.make_llm_client", return_value=mock_client), \
         patch("litexplorer.api.extraction._get_pdf_root", return_value=Path("/tmp/fake")):

        from litexplorer.models.settings import Setting
        db_session.merge(Setting(key="llm_provider", value="openai"))
        db_session.merge(Setting(key="llm_model_id", value="gpt-4o"))
        db_session.merge(Setting(key="llm_api_key", value="sk-test"))
        db_session.merge(Setting(key="llm_base_url", value=""))
        db_session.merge(Setting(key="llm_system_prompt_prefix", value=""))
        db_session.commit()

        resp = client.post(
            f"/api/extraction/schemas/{schema.id}/extract",
            json={"work_ids": [work.id, 99999]},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 1
    assert data["results"][0]["work_id"] == work.id
    assert len(data["errors"]) == 1
    assert data["errors"][0]["work_id"] == 99999


def test_extract_llm_error_returns_502(client, db_session, schema, columns, work):
    """When the LLM call raises, the endpoint returns 502."""
    mock_client = MagicMock()
    mock_client.chat.side_effect = RuntimeError("LLM unavailable")

    with patch("litexplorer.api.extraction.make_llm_client", return_value=mock_client), \
         patch("litexplorer.api.extraction._get_pdf_root", return_value=Path("/tmp/fake")):

        from litexplorer.models.settings import Setting
        db_session.merge(Setting(key="llm_provider", value="openai"))
        db_session.merge(Setting(key="llm_model_id", value="gpt-4o"))
        db_session.merge(Setting(key="llm_api_key", value="sk-test"))
        db_session.merge(Setting(key="llm_base_url", value=""))
        db_session.merge(Setting(key="llm_system_prompt_prefix", value=""))
        db_session.commit()

        resp = client.post(f"/api/extraction/schemas/{schema.id}/extract/{work.id}")

    assert resp.status_code == 502
