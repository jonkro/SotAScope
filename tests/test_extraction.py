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
    run_extraction_for_work,
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
    result, method = parse_extraction_response(response, columns)
    assert method == "json"
    assert result[1]["answer"] == "supervised"
    assert result[1]["reasoning"] == "The paper trains on labeled data."
    assert result[2]["answer"] == "ImageNet"


def test_parse_invalid_json_fallback():
    """Completely unparseable response returns empty dict with method='failed'."""
    columns = _make_columns()
    response = "Sorry, I cannot answer that."
    result, method = parse_extraction_response(response, columns)
    # Failed parse: empty dict, raw text is NOT stored in columns
    assert result == {}
    assert method == "failed"


def test_parse_allowed_values_case_insensitive():
    columns = _make_columns()
    response = json.dumps({
        "columns": {
            "Learning paradigm": {"answer": "SUPERVISED", "reasoning": "x"},
            "Dataset": {"answer": "CIFAR-10", "reasoning": "y"},
        }
    })
    result, method = parse_extraction_response(response, columns)
    assert method == "json"
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
    result, method = parse_extraction_response(response, columns)
    assert method == "json"
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
    result, method = parse_extraction_response(response, columns)
    assert method == "json_extracted"
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
    result, method = parse_extraction_response(response, columns)
    assert method == "json"
    assert result[1]["answer"] == "supervised"
    assert result[2]["answer"] == ""
    assert result[2]["reasoning"] == ""


# ---------------------------------------------------------------------------
# New parse_extraction_response tests (robust parsing strategies)
# ---------------------------------------------------------------------------


def test_parse_json_with_preamble():
    """Strategy 2: JSON embedded in prose — regex extracts the JSON block."""
    columns = _make_columns()
    inner = json.dumps({
        "columns": {
            "Learning paradigm": {"answer": "supervised", "reasoning": "Uses labelled data."},
            "Dataset": {"answer": "CIFAR-10", "reasoning": "Evaluation on CIFAR-10."},
        }
    })
    response = f"Let me analyze this paper. After careful consideration:\n{inner}"
    result, method = parse_extraction_response(response, columns)
    assert method == "json_extracted"
    assert result[1]["answer"] == "supervised"
    assert result[2]["answer"] == "CIFAR-10"


def test_parse_markdown_table_exact_headers():
    """Strategy 3: Markdown table with exact column name matches."""
    columns = _make_columns()
    response = (
        "**Extracted information**\n\n"
        "| # | Learning paradigm | Dataset |\n"
        "|---|-------------------|---------|\n"
        "| 1 | supervised | ImageNet |\n"
    )
    result, method = parse_extraction_response(response, columns)
    assert method == "markdown_table"
    assert result[1]["answer"] == "supervised"
    assert result[2]["answer"] == "ImageNet"


def test_parse_markdown_table_fuzzy_headers():
    """Strategy 3: Markdown table with fuzzy/variant column name headers."""
    columns = _make_columns()
    response = (
        "| # | Learning paradigm (type) | Dataset used |\n"
        "|---|--------------------------|------------------|\n"
        "| 1 | Unsupervised | CIFAR-10 |\n"
    )
    result, method = parse_extraction_response(response, columns)
    assert method == "markdown_table"
    # Allowed-values normalisation: "Unsupervised" → "unsupervised"
    assert result[1]["answer"] == "unsupervised"
    assert result[2]["answer"] == "CIFAR-10"


def test_parse_key_value_format():
    """Strategy 4: Key: value pattern extraction."""
    columns = _make_columns()
    response = "Learning paradigm: supervised\nDataset: ImageNet"
    result, method = parse_extraction_response(response, columns)
    assert method == "key_value"
    assert result[1]["answer"] == "supervised"
    assert result[2]["answer"] == "ImageNet"


def test_parse_key_value_bold_format():
    """Strategy 4: **Key**: value markdown-bold variant."""
    columns = _make_columns()
    response = "**Learning paradigm**: other\n**Dataset**: MNIST"
    result, method = parse_extraction_response(response, columns)
    assert method == "key_value"
    assert result[1]["answer"] == "other"
    assert result[2]["answer"] == "MNIST"


def test_parse_thinking_model_think_tags():
    """Thinking model <think>...</think> wrapper is stripped before parsing."""
    columns = _make_columns()
    inner = json.dumps({
        "columns": {
            "Learning paradigm": {"answer": "supervised", "reasoning": "Labeled data used."},
            "Dataset": {"answer": "MNIST", "reasoning": "See evaluation section."},
        }
    })
    response = f"<think>Let me analyze this paper carefully...</think>\n{inner}"
    result, method = parse_extraction_response(response, columns)
    assert result[1]["answer"] == "supervised"
    assert result[2]["answer"] == "MNIST"
    assert method in ("json", "json_extracted")


def test_parse_garbage_returns_failed():
    """Strategy 5: completely unparseable content returns empty dict, method='failed'."""
    columns = _make_columns()
    result, method = parse_extraction_response("🤖🤖🤖 ¯_(ツ)_/¯ random gibberish", columns)
    assert result == {}
    assert method == "failed"


def test_parse_markdown_table_description_as_headers():
    """Strategy 3: LLM used question-style text as headers — fuzzy match still works."""
    columns = _make_columns()
    # "Dataset for evaluation" should fuzzy-match "Dataset" via containment
    response = (
        "| # | Learning paradigm (supervised/unsupervised) | Dataset for evaluation |\n"
        "|---|----------------------------------------------|------------------------|\n"
        "| 1 | supervised | MNIST |\n"
    )
    result, method = parse_extraction_response(response, columns)
    assert method == "markdown_table"
    assert result[2]["answer"] == "MNIST"


def test_parse_markdown_table_repeated_table():
    """Strategy 3: LLM returned the same table twice — first data row is used correctly."""
    columns = _make_columns()
    response = (
        "| # | Learning paradigm | Dataset |\n"
        "|---|-------------------|---------|\n"
        "| 1 | supervised | ImageNet |\n"
        "\n"
        "| # | Learning paradigm | Dataset |\n"
        "|---|-------------------|---------|\n"
        "| 1 | supervised | ImageNet |\n"
    )
    result, method = parse_extraction_response(response, columns)
    assert method == "markdown_table"
    assert result[1]["answer"] == "supervised"
    assert result[2]["answer"] == "ImageNet"


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
    """Extract endpoint starts background extraction and returns 202 with a job_id."""
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

    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data
    assert "message" in data
    assert "1 work" in data["message"]

    # Job status endpoint should reflect completed state (TestClient runs BG tasks synchronously).
    job_resp = client.get(f"/api/extraction/jobs/{data['job_id']}")
    assert job_resp.status_code == 200
    job = job_resp.json()
    assert job["schema_id"] == schema.id
    assert "progress" in job
    assert job["progress"]["total"] == 1


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
    """Batch extraction starts background job and returns 202 with a job_id."""
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

    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data
    assert "message" in data
    # Job tracker records both works (success/failure determined by background task)
    job_resp = client.get(f"/api/extraction/jobs/{data['job_id']}")
    assert job_resp.status_code == 200
    job = job_resp.json()
    assert job["progress"]["total"] == 2
    assert str(work.id) in job["works"]
    assert "99999" in job["works"]


def test_extract_for_work_409_when_locked(client, db_session, schema, columns, work):
    """Returns 409 if the work already has an active lock."""
    from litexplorer.services.work_lock import work_lock

    _setup_llm_settings(db_session)

    work_lock.acquire(work.id, "test lock")
    try:
        with patch("litexplorer.api.extraction.make_llm_client", return_value=MagicMock()), \
             patch("litexplorer.api.extraction._get_pdf_root", return_value=Path("/tmp/fake")):
            resp = client.post(f"/api/extraction/schemas/{schema.id}/extract/{work.id}")
        assert resp.status_code == 409
        assert str(work.id) in resp.json()["detail"]
    finally:
        work_lock.release(work.id)


def test_extract_batch_409_when_locked(client, db_session, schema, columns, work):
    """Batch endpoint returns 409 if any requested work is already locked."""
    from litexplorer.services.work_lock import work_lock

    _setup_llm_settings(db_session)

    work_lock.acquire(work.id, "test lock")
    try:
        with patch("litexplorer.api.extraction.make_llm_client", return_value=MagicMock()), \
             patch("litexplorer.api.extraction._get_pdf_root", return_value=Path("/tmp/fake")):
            resp = client.post(
                f"/api/extraction/schemas/{schema.id}/extract",
                json={"work_ids": [work.id]},
            )
        assert resp.status_code == 409
    finally:
        work_lock.release(work.id)


# ---------------------------------------------------------------------------
# run_extraction_for_work service-level test (failed parse)
# ---------------------------------------------------------------------------


def test_run_extraction_failed_parse_no_raw_in_columns(db_session, schema, columns, work):
    """A failed parse creates a single _parse_error note and no per-column notes."""
    mock_client = _mock_llm_client("I cannot process this request in any structured way.")

    items, parsing_method = run_extraction_for_work(
        db=db_session,
        work_id=work.id,
        schema_id=schema.id,
        llm_client=mock_client,
        model_id="test-model",
        pdf_root=Path("/tmp/fake"),
    )

    assert parsing_method == "failed"
    assert items == []

    notes = db_session.query(WorkNote).filter_by(work_id=work.id).all()

    # No column notes should contain the raw response
    col_notes = [n for n in notes if "_parse_error" not in (n.note_type or "")]
    assert not any(
        "I cannot process this request" in (n.content or "") for n in col_notes
    )

    # Exactly one _parse_error note should exist
    parse_error_notes = [n for n in notes if "_parse_error" in (n.note_type or "")]
    assert len(parse_error_notes) == 1
    assert "I cannot process this request" in parse_error_notes[0].content


def test_run_extraction_non_json_response_uses_parsing_method(db_session, schema, columns, work):
    """A markdown-table LLM response is parsed, notes created, method != 'json'."""
    table_reply = (
        "| # | Learning paradigm | Dataset |\n"
        "|---|-------------------|---------|\n"
        "| 1 | supervised | MNIST |\n"
    )
    mock_client = _mock_llm_client(table_reply)

    items, parsing_method = run_extraction_for_work(
        db=db_session,
        work_id=work.id,
        schema_id=schema.id,
        llm_client=mock_client,
        model_id="test-model",
        pdf_root=Path("/tmp/fake"),
    )

    assert parsing_method == "markdown_table"
    assert len(items) == 2
    paradigm = next(i for i in items if i["column_name"] == "Learning paradigm")
    assert paradigm["answer"] == "supervised"


# ---------------------------------------------------------------------------
# ai_proposal provenance / re_evaluate_edited tests
# ---------------------------------------------------------------------------


def _setup_llm_settings(db_session):
    """Helper: seed the LLM settings needed by _build_llm_client."""
    from litexplorer.models.settings import Setting
    db_session.merge(Setting(key="llm_provider", value="openai"))
    db_session.merge(Setting(key="llm_model_id", value="gpt-4o"))
    db_session.merge(Setting(key="llm_api_key", value="sk-test"))
    db_session.merge(Setting(key="llm_base_url", value=""))
    db_session.merge(Setting(key="llm_system_prompt_prefix", value=""))
    db_session.commit()


def test_re_evaluate_edited_false_skips_user_notes(db_session, schema, columns, work):
    """Default (re_evaluate_edited=False) skips user-provenance cells; no proposal created."""
    col1 = columns[0]
    # Pre-create a user note for col1.
    user_note = WorkNote(
        work_id=work.id,
        project_id=schema.project_id,
        content="my manual answer",
        note_type=f"{schema.title} / {col1.name}",
        provenance="user",
        model_id=None,
    )
    db_session.add(user_note)
    db_session.commit()

    llm_reply = _llm_response_for("supervised", "AI says supervised.", "ImageNet", "AI says ImageNet.")
    mock_client = _mock_llm_client(llm_reply)

    items, _ = run_extraction_for_work(
        db=db_session,
        work_id=work.id,
        schema_id=schema.id,
        llm_client=mock_client,
        model_id="test-model",
        pdf_root=Path("/tmp/fake"),
        re_evaluate_edited=False,
    )

    # col1 (user note) is skipped; only col2 is extracted.
    assert len(items) == 1
    assert items[0]["column_name"] == "Dataset"

    # No ai_proposal created.
    all_notes = db_session.query(WorkNote).filter_by(work_id=work.id).all()
    proposal_notes = [n for n in all_notes if n.provenance == "ai_proposal"]
    assert proposal_notes == []

    # User note is still intact.
    db_session.refresh(user_note)
    assert user_note.content == "my manual answer"
    assert user_note.provenance == "user"


def test_re_evaluate_edited_true_creates_proposal_for_user_notes(db_session, schema, columns, work):
    """re_evaluate_edited=True creates ai_proposal notes alongside user-provenance notes."""
    col1 = columns[0]
    user_note = WorkNote(
        work_id=work.id,
        project_id=schema.project_id,
        content="my manual answer",
        note_type=f"{schema.title} / {col1.name}",
        provenance="user",
        model_id=None,
    )
    db_session.add(user_note)
    db_session.commit()

    llm_reply = _llm_response_for("supervised", "AI says supervised.", "ImageNet", "AI says ImageNet.")
    mock_client = _mock_llm_client(llm_reply)

    items, _ = run_extraction_for_work(
        db=db_session,
        work_id=work.id,
        schema_id=schema.id,
        llm_client=mock_client,
        model_id="test-model",
        pdf_root=Path("/tmp/fake"),
        re_evaluate_edited=True,
    )

    # Items only includes the "ai" note (col2); col1 goes to proposal.
    assert len(items) == 1
    assert items[0]["column_name"] == "Dataset"

    all_notes = db_session.query(WorkNote).filter_by(work_id=work.id).all()

    # User note for col1 is preserved.
    user_notes = [n for n in all_notes if n.provenance == "user"]
    assert len(user_notes) == 1
    assert user_notes[0].content == "my manual answer"

    # ai_proposal answer note created for col1.
    proposal_notes = [n for n in all_notes if n.provenance == "ai_proposal"]
    proposal_answer_types = [n.note_type for n in proposal_notes]
    assert any(col1.name in nt for nt in proposal_answer_types)

    # ai note for col2.
    ai_notes = [n for n in all_notes if n.provenance == "ai"]
    ai_answer_notes = [n for n in ai_notes if "reasoning" not in (n.note_type or "")]
    assert any("Dataset" in n.note_type for n in ai_answer_notes)


def test_re_evaluate_edited_true_replaces_stale_proposal(db_session, schema, columns, work):
    """Re-running with re_evaluate_edited=True deletes the stale ai_proposal and creates a fresh one."""
    col1 = columns[0]
    answer_note_type = f"{schema.title} / {col1.name}"

    # Pre-existing user note.
    user_note = WorkNote(
        work_id=work.id,
        project_id=schema.project_id,
        content="user answer",
        note_type=answer_note_type,
        provenance="user",
        model_id=None,
    )
    # Stale ai_proposal from a previous run.
    stale_proposal = WorkNote(
        work_id=work.id,
        project_id=schema.project_id,
        content="old proposal",
        note_type=answer_note_type,
        provenance="ai_proposal",
        model_id="old-model",
    )
    db_session.add_all([user_note, stale_proposal])
    db_session.commit()

    llm_reply = _llm_response_for("unsupervised", "AI now says unsupervised.", "MNIST", "MNIST used.")
    mock_client = _mock_llm_client(llm_reply)

    run_extraction_for_work(
        db=db_session,
        work_id=work.id,
        schema_id=schema.id,
        llm_client=mock_client,
        model_id="new-model",
        pdf_root=Path("/tmp/fake"),
        re_evaluate_edited=True,
    )

    # Stale proposal (old content, old model) is gone; verified by content+model, not id
    # (SQLite may recycle the same row id for new notes after deletion).
    stale_still_exists = db_session.query(WorkNote).filter_by(
        work_id=work.id,
        note_type=answer_note_type,
        provenance="ai_proposal",
        model_id="old-model",
    ).first()
    assert stale_still_exists is None

    # Fresh proposal created with new content and new model.
    all_notes = db_session.query(WorkNote).filter_by(work_id=work.id).all()
    new_proposals = [n for n in all_notes if n.provenance == "ai_proposal"
                     and n.note_type == answer_note_type]
    assert len(new_proposals) == 1
    assert new_proposals[0].content == "unsupervised"
    assert new_proposals[0].model_id == "new-model"


def test_re_evaluate_edited_false_cleans_stale_proposal_for_ai_column(db_session, schema, columns, work):
    """re_evaluate_edited=False cleans stale ai_proposal notes for columns it re-extracts (ai provenance)."""
    col2 = columns[1]
    answer_note_type = f"{schema.title} / {col2.name}"

    # A stale ai_proposal from a previous re_evaluate_edited=True run (col2 had no user note then).
    stale_proposal = WorkNote(
        work_id=work.id,
        project_id=schema.project_id,
        content="stale proposal",
        note_type=answer_note_type,
        provenance="ai_proposal",
        model_id="old-model",
    )
    db_session.add(stale_proposal)
    db_session.commit()

    llm_reply = _llm_response_for("supervised", "r1", "ImageNet", "r2")
    mock_client = _mock_llm_client(llm_reply)

    run_extraction_for_work(
        db=db_session,
        work_id=work.id,
        schema_id=schema.id,
        llm_client=mock_client,
        model_id="test-model",
        pdf_root=Path("/tmp/fake"),
        re_evaluate_edited=False,
    )

    # Stale ai_proposal for col2 was cleaned up; no ai_proposal notes should remain.
    # Note: we verify by content+model_id (not row id) because SQLite may recycle the same
    # auto-increment id for newly created notes after a deletion.
    stale_still_exists = db_session.query(WorkNote).filter_by(
        work_id=work.id,
        note_type=answer_note_type,
        provenance="ai_proposal",
        model_id="old-model",
    ).first()
    assert stale_still_exists is None

    # The column should now have a fresh "ai" note (not a proposal).
    ai_note = db_session.query(WorkNote).filter_by(
        work_id=work.id,
        note_type=answer_note_type,
        provenance="ai",
    ).first()
    assert ai_note is not None
    assert ai_note.content == "ImageNet"


# ---------------------------------------------------------------------------
# Manual fill endpoint tests
# ---------------------------------------------------------------------------


def test_manual_fill_creates_user_note(client, db_session, schema, columns, work):
    """Manual fill creates a user-provenance note when none exists."""
    col = columns[0]
    resp = client.post(
        f"/api/extraction/schemas/{schema.id}/columns/{col.id}/works/{work.id}/manual-fill",
        json={"content": "manually entered answer"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["work_id"] == work.id
    assert data["column_id"] == col.id
    assert data["answer_note"]["content"] == "manually entered answer"
    assert data["answer_note"]["provenance"] == "user"
    assert data["proposal"] is None

    # Verify in DB.
    notes = db_session.query(WorkNote).filter_by(work_id=work.id).all()
    user_notes = [n for n in notes if n.provenance == "user"]
    assert len(user_notes) == 1
    assert user_notes[0].content == "manually entered answer"


def test_manual_fill_updates_existing_ai_note(client, db_session, schema, columns, work):
    """Manual fill updates an existing ai-provenance note to user provenance."""
    col = columns[1]
    answer_note_type = f"{schema.title} / {col.name}"
    existing = WorkNote(
        work_id=work.id,
        project_id=schema.project_id,
        content="ai answer",
        note_type=answer_note_type,
        provenance="ai",
        model_id="gpt-4o",
    )
    db_session.add(existing)
    db_session.commit()

    resp = client.post(
        f"/api/extraction/schemas/{schema.id}/columns/{col.id}/works/{work.id}/manual-fill",
        json={"content": "corrected answer"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer_note"]["content"] == "corrected answer"
    assert data["answer_note"]["provenance"] == "user"


def test_manual_fill_deletes_proposal(client, db_session, schema, columns, work):
    """Manual fill removes the ai_proposal note if one exists."""
    col = columns[0]
    answer_note_type = f"{schema.title} / {col.name}"
    reasoning_note_type = f"{schema.title} / {col.name} / reasoning"

    # Existing user note + ai_proposal.
    user_note = WorkNote(
        work_id=work.id, project_id=schema.project_id,
        content="user answer", note_type=answer_note_type, provenance="user",
    )
    proposal = WorkNote(
        work_id=work.id, project_id=schema.project_id,
        content="ai proposal", note_type=answer_note_type, provenance="ai_proposal",
    )
    proposal_reasoning = WorkNote(
        work_id=work.id, project_id=schema.project_id,
        content="ai proposal reasoning", note_type=reasoning_note_type, provenance="ai_proposal",
    )
    db_session.add_all([user_note, proposal, proposal_reasoning])
    db_session.commit()
    proposal_id = proposal.id
    proposal_reasoning_id = proposal_reasoning.id

    resp = client.post(
        f"/api/extraction/schemas/{schema.id}/columns/{col.id}/works/{work.id}/manual-fill",
        json={"content": "updated user answer"},
    )
    assert resp.status_code == 200
    assert resp.json()["proposal"] is None

    # Both proposal notes are deleted.
    assert db_session.get(WorkNote, proposal_id) is None
    assert db_session.get(WorkNote, proposal_reasoning_id) is None


def test_manual_fill_schema_not_found(client, work, columns):
    col = columns[0]
    resp = client.post(
        f"/api/extraction/schemas/99999/columns/{col.id}/works/{work.id}/manual-fill",
        json={"content": "x"},
    )
    assert resp.status_code == 404


def test_manual_fill_column_not_found(client, schema, work):
    resp = client.post(
        f"/api/extraction/schemas/{schema.id}/columns/99999/works/{work.id}/manual-fill",
        json={"content": "x"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Dismiss proposal endpoint tests
# ---------------------------------------------------------------------------


def test_dismiss_proposal_deletes_notes(client, db_session, schema, columns, work):
    """Dismiss proposal endpoint deletes ai_proposal answer + reasoning notes."""
    col = columns[0]
    answer_note_type = f"{schema.title} / {col.name}"
    reasoning_note_type = f"{schema.title} / {col.name} / reasoning"

    user_note = WorkNote(
        work_id=work.id, project_id=schema.project_id,
        content="user answer", note_type=answer_note_type, provenance="user",
    )
    proposal = WorkNote(
        work_id=work.id, project_id=schema.project_id,
        content="ai proposal", note_type=answer_note_type, provenance="ai_proposal",
    )
    proposal_reasoning = WorkNote(
        work_id=work.id, project_id=schema.project_id,
        content="ai proposal reasoning", note_type=reasoning_note_type, provenance="ai_proposal",
    )
    db_session.add_all([user_note, proposal, proposal_reasoning])
    db_session.commit()
    proposal_id = proposal.id
    proposal_reasoning_id = proposal_reasoning.id

    resp = client.delete(
        f"/api/extraction/schemas/{schema.id}/columns/{col.id}/works/{work.id}/proposal"
    )
    assert resp.status_code == 204

    # Proposal notes are deleted.
    assert db_session.get(WorkNote, proposal_id) is None
    assert db_session.get(WorkNote, proposal_reasoning_id) is None

    # User note is untouched.
    db_session.refresh(user_note)
    assert user_note.content == "user answer"


def test_dismiss_proposal_no_op_when_no_proposal(client, db_session, schema, columns, work):
    """Dismiss proposal returns 204 even if no ai_proposal exists."""
    resp = client.delete(
        f"/api/extraction/schemas/{schema.id}/columns/{columns[0].id}/works/{work.id}/proposal"
    )
    assert resp.status_code == 204


def test_dismiss_proposal_schema_not_found(client, work, columns):
    col = columns[0]
    resp = client.delete(
        f"/api/extraction/schemas/99999/columns/{col.id}/works/{work.id}/proposal"
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Results endpoint returns proposal field
# ---------------------------------------------------------------------------


def test_results_endpoint_includes_proposal(client, db_session, schema, columns, work):
    """GET /schemas/{id}/results populates the proposal field when an ai_proposal note exists."""
    col = columns[0]
    answer_note_type = f"{schema.title} / {col.name}"

    # Primary user note.
    user_note = WorkNote(
        work_id=work.id, project_id=schema.project_id,
        content="user answer", note_type=answer_note_type, provenance="user",
    )
    # Parallel ai_proposal.
    proposal = WorkNote(
        work_id=work.id, project_id=schema.project_id,
        content="ai proposal content", note_type=answer_note_type, provenance="ai_proposal",
        model_id="gpt-4o",
    )
    db_session.add_all([user_note, proposal])
    db_session.commit()

    resp = client.get(
        f"/api/extraction/schemas/{schema.id}/results",
        params={"work_ids": str(work.id)},
    )
    assert resp.status_code == 200
    cells = resp.json()["cells"]
    assert len(cells) == 1

    cell = cells[0]
    assert cell["answer_note"]["provenance"] == "user"
    assert cell["answer_note"]["content"] == "user answer"
    assert cell["proposal"] is not None
    assert cell["proposal"]["provenance"] == "ai_proposal"
    assert cell["proposal"]["content"] == "ai proposal content"


def test_results_endpoint_no_proposal_when_absent(client, db_session, schema, columns, work):
    """GET /schemas/{id}/results returns proposal=null when no ai_proposal note exists."""
    col = columns[0]
    answer_note_type = f"{schema.title} / {col.name}"

    ai_note = WorkNote(
        work_id=work.id, project_id=schema.project_id,
        content="ai answer", note_type=answer_note_type, provenance="ai",
    )
    db_session.add(ai_note)
    db_session.commit()

    resp = client.get(
        f"/api/extraction/schemas/{schema.id}/results",
        params={"work_ids": str(work.id)},
    )
    assert resp.status_code == 200
    cell = resp.json()["cells"][0]
    assert cell["proposal"] is None


def test_batch_extraction_with_re_evaluate_edited_via_api(
    client, db_session, schema, columns, work
):
    """Batch endpoint with re_evaluate_edited=True starts a background job and returns 202."""
    col1 = columns[0]
    answer_note_type = f"{schema.title} / {col1.name}"

    # Pre-existing user note for col1.
    user_note = WorkNote(
        work_id=work.id, project_id=schema.project_id,
        content="user answer", note_type=answer_note_type, provenance="user",
    )
    db_session.add(user_note)
    db_session.commit()

    llm_reply = _llm_response_for("supervised", "AI reasoning.", "MNIST", "Dataset reasoning.")
    mock_client = _mock_llm_client(llm_reply)

    _setup_llm_settings(db_session)

    with patch("litexplorer.api.extraction.make_llm_client", return_value=mock_client), \
         patch("litexplorer.api.extraction._get_pdf_root", return_value=Path("/tmp/fake")):
        resp = client.post(
            f"/api/extraction/schemas/{schema.id}/extract",
            json={"work_ids": [work.id], "re_evaluate_edited": True},
        )

    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data
    assert "message" in data
