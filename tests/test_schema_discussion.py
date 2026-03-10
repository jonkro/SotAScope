"""Tests for schema discussion: prompt builder, proposal parser, and chat/extraction endpoints.

Covers:
  1. build_schema_discussion_prompt — with existing schema (incl. columns & description)
  2. build_schema_discussion_prompt — schema=None (new schema)
  3. build_schema_discussion_prompt — system_prefix is prepended
  4. build_schema_discussion_prompt — schema with no columns
  5. build_schema_discussion_prompt — column-proposal JSON fields present in prompt
  6. POST /api/llm/chat — context_type="extraction_schema" routes to schema prompt, no docs
  7. POST /api/llm/chat — context_type="extraction_schema", context_id=None (new schema)
  8. POST /api/llm/chat — normal paper mode unaffected (no session)
  9. GET /api/extraction/schemas/{id}/summary — returns schema + columns
 10. GET /api/extraction/schemas/{id}/summary — 404 for unknown schema
 11. parse_column_proposals — single valid block → fenced_block strategy
 12. parse_column_proposals — multiple blocks in one response
 13. parse_column_proposals — no blocks → empty list, "failed"
 14. parse_column_proposals — malformed JSON → skipped
 15. parse_column_proposals — missing name/prompt → skipped; missing description/allowed_values → defaults
 16. parse_column_proposals — missing closing fence (lenient)
 17. POST /api/extraction/schemas/{id}/columns/from-proposal — success, appended at end
 18. POST /api/extraction/schemas/{id}/columns/from-proposal — sort_order = max + 1
 19. POST /api/extraction/schemas/{id}/columns/from-proposal — 404 for unknown schema
 20. POST /api/extraction/schemas/from-discussion — creates schema, returns it
 21. POST /api/extraction/schemas/from-discussion — with project_id
 22. POST /api/extraction/schemas/from-discussion — missing title → 422
 23. parse_column_proposals — thinking tags stripped; fenced block inside still parsed
 24. parse_column_proposals — ```json fenced block → json_fence strategy
 25. parse_column_proposals — bare JSON object in prose → bare_json strategy
 26. parse_column_proposals — JSON array of proposals → bare_json strategy
 27. parse_column_proposals — field name variants (question/values/title) mapped correctly
 28. parse_column_proposals — allowed_values as comma-separated string normalized to list
 29. parse_column_proposals — allowed_values null-like string normalized to None
 30. parse_column_proposals — markdown key-value list → markdown_list strategy
 31. parse_column_proposals — completely non-compliant response → empty list, "failed"
 32. parse_column_proposals — malformed JSON (missing closing brace) → no crash
 33. parse_column_proposals — returns tuple (list, str) — verify shape
 34. parse_column_proposals — wrapper key "proposals" in JSON object → multiple proposals
 35. build_schema_discussion_prompt — FORMAT CRITICAL added for local/non-GPT model
 36. build_schema_discussion_prompt — FORMAT CRITICAL not added for gpt- model
 37. build_schema_discussion_prompt — FORMAT CRITICAL not added for anthropic model
 38. build_schema_discussion_prompt — concrete realistic example present in prompt
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from litexplorer.models.chat import ChatSession  # registers chat_sessions table with Base.metadata
from litexplorer.models.extraction import ExtractionColumn, ExtractionSchema
from litexplorer.models.library import Work
from litexplorer.models.project import Project
from litexplorer.models.settings import Setting
from litexplorer.services.schema_discussion import (
    build_schema_discussion_prompt,
    parse_column_proposals,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _seed_llm_settings(db_session, provider="anthropic", model_id="claude-test"):
    for key, value in [
        ("llm_provider", provider),
        ("llm_api_key", "test-key"),
        ("llm_model_id", model_id),
        ("llm_base_url", ""),
    ]:
        db_session.add(Setting(key=key, value=value, description="test"))
    db_session.commit()


def _make_schema(db_session, title="My Schema", description=None, with_columns=True):
    schema = ExtractionSchema(title=title, description=description)
    db_session.add(schema)
    db_session.flush()
    if with_columns:
        db_session.add(ExtractionColumn(
            schema_id=schema.id,
            name="Method",
            prompt="What method does this paper use?",
            sort_order=0,
        ))
        db_session.add(ExtractionColumn(
            schema_id=schema.id,
            name="Dataset",
            prompt="What dataset is used for evaluation?",
            description="Training/test split dataset",
            allowed_values=["ImageNet", "COCO", "Other"],
            sort_order=1,
        ))
    db_session.commit()
    db_session.refresh(schema)
    return schema


# ---------------------------------------------------------------------------
# 1–5: Unit tests for build_schema_discussion_prompt
# ---------------------------------------------------------------------------


def test_prompt_with_existing_schema_includes_title_and_columns(db_session):
    schema = _make_schema(db_session, title="Benchmark Table", description="Compare benchmarks")
    prompt = build_schema_discussion_prompt(schema)

    assert "Benchmark Table" in prompt
    assert "Compare benchmarks" in prompt
    assert "Method" in prompt
    assert "Dataset" in prompt
    assert "ImageNet" in prompt
    assert "column-proposal" in prompt


def test_prompt_new_schema_none(db_session):
    prompt = build_schema_discussion_prompt(None)

    assert "new extraction schema from scratch" in prompt
    assert "column-proposal" in prompt
    # Should not mention any schema title
    assert "existing extraction schema" not in prompt


def test_prompt_system_prefix_prepended(db_session):
    schema = _make_schema(db_session)
    prompt = build_schema_discussion_prompt(schema, system_prefix="Always be concise.")

    assert prompt.startswith("Always be concise.")
    # Schema content still present after the prefix
    assert "My Schema" in prompt


def test_prompt_schema_no_columns(db_session):
    schema = _make_schema(db_session, with_columns=False)
    prompt = build_schema_discussion_prompt(schema)

    assert "No columns have been defined yet" in prompt


def test_prompt_column_proposal_fields_present(db_session):
    """Every required column-proposal JSON field must appear in the prompt."""
    prompt = build_schema_discussion_prompt(None)

    for field in ("name", "prompt", "description", "allowed_values"):
        assert f'"{field}"' in prompt, f"Field {field!r} missing from column-proposal example"


# ---------------------------------------------------------------------------
# 6–8: Integration tests for POST /api/llm/chat routing
# ---------------------------------------------------------------------------


def test_chat_schema_mode_uses_schema_system_prompt_and_no_docs(client, db_session):
    """context_type='extraction_schema' → schema prompt sent, context_documents=[]."""
    _seed_llm_settings(db_session)
    schema = _make_schema(db_session, title="Methods Table")

    # Create an extraction_schema auto-session scoped to the schema
    session_resp = client.post("/api/chat/sessions/auto", json={
        "context_type": "extraction_schema",
        "context_id": schema.id,
    })
    assert session_resp.status_code == 200
    session_id = session_resp.json()["id"]

    with patch("litexplorer.api.llm.make_llm_client") as mock_factory:
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "Here is a proposed column."
        mock_factory.return_value = mock_llm

        resp = client.post("/api/llm/chat", json={
            "session_id": session_id,
            "message": "Suggest columns for my schema.",
            "history": [],
        })

    assert resp.status_code == 200
    assert resp.json()["reply"] == "Here is a proposed column."

    call_args = mock_llm.chat.call_args
    assert call_args is not None, "llm_client.chat() was not called"

    # No context documents in schema mode
    assert call_args.kwargs["context_documents"] == []

    # System prompt must contain the schema title
    system_prompt = call_args.kwargs["system_prompt"]
    assert system_prompt is not None
    assert "Methods Table" in system_prompt
    assert "column-proposal" in system_prompt


def test_chat_schema_mode_no_context_id_uses_new_schema_prompt(client, db_session):
    """context_type='extraction_schema' with context_id=None → 'new schema' prompt."""
    _seed_llm_settings(db_session)

    session_resp = client.post("/api/chat/sessions/auto", json={
        "context_type": "extraction_schema",
        "context_id": None,
    })
    session_id = session_resp.json()["id"]

    with patch("litexplorer.api.llm.make_llm_client") as mock_factory:
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "Let's start with some questions."
        mock_factory.return_value = mock_llm

        resp = client.post("/api/llm/chat", json={
            "session_id": session_id,
            "message": "Where do I begin?",
        })

    assert resp.status_code == 200

    call_args = mock_llm.chat.call_args
    system_prompt = call_args.kwargs["system_prompt"]
    assert "new extraction schema from scratch" in system_prompt
    assert call_args.kwargs["context_documents"] == []


def test_chat_paper_mode_unaffected_by_schema_routing(client, db_session):
    """Normal paper-context chat (no session / context_type='papers') is unaffected."""
    _seed_llm_settings(db_session)
    work = Work(title="Neural Net Paper", doi="10.9999/schema-discuss", publication_year=2024)
    db_session.add(work)
    db_session.commit()

    with patch("litexplorer.api.llm.make_llm_client") as mock_factory:
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "This paper proposes a novel architecture."
        mock_factory.return_value = mock_llm

        resp = client.post("/api/llm/chat", json={
            "papers": [{"work_id": work.id, "use_pdf": False}],
            "message": "Summarise this paper.",
        })

    assert resp.status_code == 200
    assert resp.json()["reply"] == "This paper proposes a novel architecture."

    # system_prompt should be None (use provider default)
    call_args = mock_llm.chat.call_args
    assert call_args.kwargs.get("system_prompt") is None


# ---------------------------------------------------------------------------
# 9–10: GET /api/extraction/schemas/{id}/summary
# ---------------------------------------------------------------------------


def test_get_schema_summary_returns_schema_and_columns(client, db_session):
    schema = _make_schema(db_session, title="Summary Test", description="A test schema")

    resp = client.get(f"/api/extraction/schemas/{schema.id}/summary")

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == schema.id
    assert data["title"] == "Summary Test"
    assert data["description"] == "A test schema"
    assert len(data["columns"]) == 2
    col_names = {c["name"] for c in data["columns"]}
    assert col_names == {"Method", "Dataset"}
    # allowed_values present on Dataset column
    dataset_col = next(c for c in data["columns"] if c["name"] == "Dataset")
    assert dataset_col["allowed_values"] == ["ImageNet", "COCO", "Other"]


def test_get_schema_summary_not_found(client, db_session):
    resp = client.get("/api/extraction/schemas/99999/summary")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 11–16: parse_column_proposals unit tests
# ---------------------------------------------------------------------------


def test_parse_single_valid_proposal():
    """A single well-formed column-proposal block is returned via fenced_block strategy."""
    response = (
        "Here is my suggestion:\n\n"
        "```column-proposal\n"
        '{"name": "Method", "prompt": "What method is used?", '
        '"description": "The ML method", "allowed_values": null}\n'
        "```"
    )
    proposals, method = parse_column_proposals(response)
    assert method == "fenced_block"
    assert len(proposals) == 1
    p = proposals[0]
    assert p["name"] == "Method"
    assert p["prompt"] == "What method is used?"
    assert p["description"] == "The ML method"
    assert p["allowed_values"] is None


def test_parse_multiple_proposals():
    """Multiple column-proposal blocks in one response are all returned."""
    response = (
        "First column:\n"
        "```column-proposal\n"
        '{"name": "Method", "prompt": "What method?", "description": "A", "allowed_values": null}\n'
        "```\n\n"
        "Second column:\n"
        "```column-proposal\n"
        '{"name": "Dataset", "prompt": "What dataset?", "description": "B", '
        '"allowed_values": ["CIFAR", "ImageNet"]}\n'
        "```"
    )
    proposals, method = parse_column_proposals(response)
    assert method == "fenced_block"
    assert len(proposals) == 2
    assert proposals[0]["name"] == "Method"
    assert proposals[1]["name"] == "Dataset"
    assert proposals[1]["allowed_values"] == ["CIFAR", "ImageNet"]


def test_parse_no_proposals_returns_empty():
    """A response with no column-proposal blocks returns an empty list with method 'failed'."""
    response = "I'd be happy to help! What's your research topic?"
    proposals, method = parse_column_proposals(response)
    assert proposals == []
    assert method == "failed"


def test_parse_malformed_json_block_skipped():
    """A column-proposal block with invalid JSON is silently skipped."""
    response = (
        "```column-proposal\n"
        "this is not json\n"
        "```\n\n"
        "```column-proposal\n"
        '{"name": "Good", "prompt": "Fine?", "description": "", "allowed_values": null}\n'
        "```"
    )
    proposals, method = parse_column_proposals(response)
    assert method == "fenced_block"
    assert len(proposals) == 1
    assert proposals[0]["name"] == "Good"


def test_parse_missing_name_or_prompt_skipped_missing_optional_fields_defaulted():
    """Proposals without name/prompt are skipped; missing description/allowed_values use defaults."""
    response = (
        # Missing 'name' → skipped
        "```column-proposal\n"
        '{"prompt": "Only prompt, no name"}\n'
        "```\n"
        # Missing 'prompt' → skipped
        "```column-proposal\n"
        '{"name": "Only name, no prompt"}\n'
        "```\n"
        # Missing optional fields → defaults
        "```column-proposal\n"
        '{"name": "Complete", "prompt": "Is it complete?"}\n'
        "```"
    )
    proposals, method = parse_column_proposals(response)
    assert method == "fenced_block"
    assert len(proposals) == 1
    p = proposals[0]
    assert p["name"] == "Complete"
    assert p["description"] == ""
    assert p["allowed_values"] is None


def test_parse_missing_closing_fence_accepted():
    """A block without a closing fence (end-of-string) is still parsed."""
    response = (
        "```column-proposal\n"
        '{"name": "OpenEnded", "prompt": "No closing fence?", '
        '"description": "Lenient", "allowed_values": null}'
        # intentionally no closing ```
    )
    proposals, method = parse_column_proposals(response)
    assert method == "fenced_block"
    assert len(proposals) == 1
    assert proposals[0]["name"] == "OpenEnded"


# ---------------------------------------------------------------------------
# 17–19: POST /api/extraction/schemas/{id}/columns/from-proposal
# ---------------------------------------------------------------------------


def test_create_column_from_proposal_appended_at_end(client, db_session):
    """from-proposal creates a column with sort_order = max_existing + 1."""
    schema = _make_schema(db_session, with_columns=True)
    # Existing columns have sort_order 0 (Method) and 1 (Dataset)
    existing_orders = sorted(c.sort_order for c in schema.columns)
    assert existing_orders == [0, 1]

    resp = client.post(
        f"/api/extraction/schemas/{schema.id}/columns/from-proposal",
        json={
            "name": "Proposed Col",
            "prompt": "What is the proposed value?",
            "description": "An LLM-proposed column",
            "allowed_values": ["Yes", "No"],
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Proposed Col"
    assert data["prompt"] == "What is the proposed value?"
    assert data["sort_order"] == 2  # max(0, 1) + 1
    assert data["allowed_values"] == ["Yes", "No"]


def test_create_column_from_proposal_on_empty_schema(client, db_session):
    """from-proposal on a schema with no columns gets sort_order=0."""
    schema = _make_schema(db_session, with_columns=False)

    resp = client.post(
        f"/api/extraction/schemas/{schema.id}/columns/from-proposal",
        json={"name": "First", "prompt": "First question?"},
    )
    assert resp.status_code == 201
    assert resp.json()["sort_order"] == 0


def test_create_column_from_proposal_not_found(client, db_session):
    """from-proposal returns 404 for a nonexistent schema."""
    resp = client.post(
        "/api/extraction/schemas/99999/columns/from-proposal",
        json={"name": "X", "prompt": "Y?"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 20–22: POST /api/extraction/schemas/from-discussion
# ---------------------------------------------------------------------------


def test_create_schema_from_discussion(client, db_session):
    """from-discussion creates and returns a new schema."""
    resp = client.post(
        "/api/extraction/schemas/from-discussion",
        json={"title": "My Discussed Schema", "description": "Born in chat"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "My Discussed Schema"
    assert data["description"] == "Born in chat"
    assert data["project_id"] is None
    assert data["columns"] == []


def test_create_schema_from_discussion_with_project_id(client, db_session):
    """from-discussion respects an optional project_id."""
    project = Project(name="Test Project")
    db_session.add(project)
    db_session.commit()

    resp = client.post(
        "/api/extraction/schemas/from-discussion",
        json={"title": "Scoped Schema", "project_id": project.id},
    )
    assert resp.status_code == 201
    assert resp.json()["project_id"] == project.id


def test_create_schema_from_discussion_missing_title_returns_422(client, db_session):
    """from-discussion with no title fails Pydantic validation."""
    resp = client.post(
        "/api/extraction/schemas/from-discussion",
        json={"description": "No title provided"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 23–34: Multi-strategy parse_column_proposals tests
# ---------------------------------------------------------------------------


def test_parse_thinking_tags_stripped():
    """<think>...</think> tags are stripped; the fenced block inside is still parsed."""
    response = (
        "<think>Let me think about what column to propose...</think>\n"
        "Here is my suggestion:\n\n"
        "```column-proposal\n"
        '{"name": "Method", "prompt": "What method is used?", '
        '"description": "The ML method", "allowed_values": null}\n'
        "```"
    )
    proposals, method = parse_column_proposals(response)
    assert method == "fenced_block"
    assert len(proposals) == 1
    assert proposals[0]["name"] == "Method"


def test_parse_thinking_tags_only_wrapper_for_reasoning_model():
    """A full <thinking> block followed by a proposal block is parsed correctly."""
    response = (
        "<thinking>\n"
        "The researcher is studying network protocols. I should suggest columns about\n"
        "packet size, latency, and error rates.\n"
        "</thinking>\n"
        "I suggest starting with a column about packet size:\n\n"
        "```column-proposal\n"
        '{"name": "Packet Size", "prompt": "What is the typical packet size used?", '
        '"description": "Network packet size in bytes", "allowed_values": null}\n'
        "```"
    )
    proposals, method = parse_column_proposals(response)
    assert method == "fenced_block"
    assert len(proposals) == 1
    assert proposals[0]["name"] == "Packet Size"


def test_parse_json_fence_strategy():
    """A ```json fenced block containing a proposal is parsed via json_fence strategy."""
    response = (
        "Here is the column I'd propose:\n\n"
        "```json\n"
        '{"name": "Sample Size", "prompt": "How many participants?", '
        '"description": "Number of subjects", "allowed_values": null}\n'
        "```"
    )
    proposals, method = parse_column_proposals(response)
    assert method == "json_fence"
    assert len(proposals) == 1
    assert proposals[0]["name"] == "Sample Size"
    assert proposals[0]["prompt"] == "How many participants?"


def test_parse_bare_json_object_in_prose():
    """A bare JSON object embedded in prose is parsed via bare_json strategy."""
    response = (
        "I think you should add this column: "
        '{"name": "Evaluation Metric", "prompt": "What metric is used?", '
        '"description": "Primary evaluation metric", "allowed_values": null}'
    )
    proposals, method = parse_column_proposals(response)
    assert method == "bare_json"
    assert len(proposals) == 1
    assert proposals[0]["name"] == "Evaluation Metric"


def test_parse_json_array_of_proposals():
    """A JSON array of proposal objects is parsed via bare_json strategy."""
    response = (
        "Here are some columns I'd suggest:\n\n"
        '[{"name": "Method", "prompt": "What method?", "description": "ML method"}, '
        '{"name": "Dataset", "prompt": "What dataset?", "description": "Eval data"}]'
    )
    proposals, method = parse_column_proposals(response)
    assert method == "bare_json"
    assert len(proposals) == 2
    assert proposals[0]["name"] == "Method"
    assert proposals[1]["name"] == "Dataset"


def test_parse_json_wrapper_key():
    """A JSON object with a 'proposals' wrapper key yields all contained proposals."""
    response = (
        '{"proposals": ['
        '{"name": "A", "prompt": "Question A?"}, '
        '{"name": "B", "prompt": "Question B?", "allowed_values": ["Yes", "No"]}'
        "]}"
    )
    proposals, method = parse_column_proposals(response)
    assert method == "bare_json"
    assert len(proposals) == 2
    assert proposals[0]["name"] == "A"
    assert proposals[1]["allowed_values"] == ["Yes", "No"]


def test_parse_field_variant_question_for_prompt():
    """Field name 'question' is mapped to 'prompt'."""
    response = (
        "```column-proposal\n"
        '{"name": "Accuracy", "question": "What accuracy is reported?"}\n'
        "```"
    )
    proposals, method = parse_column_proposals(response)
    assert method == "fenced_block"
    assert len(proposals) == 1
    assert proposals[0]["prompt"] == "What accuracy is reported?"


def test_parse_field_variant_title_for_name():
    """Field name 'title' is mapped to 'name'."""
    response = (
        '{"title": "Latency", "prompt": "What is the reported latency?", '
        '"description": "End-to-end latency"}'
    )
    proposals, method = parse_column_proposals(response)
    assert method == "bare_json"
    assert len(proposals) == 1
    assert proposals[0]["name"] == "Latency"


def test_parse_field_variant_values_for_allowed_values():
    """Field name 'values' is mapped to 'allowed_values'."""
    response = (
        "```column-proposal\n"
        '{"name": "Supervised", "prompt": "Is the method supervised?", '
        '"values": ["Yes", "No", "Partially"]}\n'
        "```"
    )
    proposals, method = parse_column_proposals(response)
    assert method == "fenced_block"
    assert len(proposals) == 1
    assert proposals[0]["allowed_values"] == ["Yes", "No", "Partially"]


def test_parse_allowed_values_comma_string_normalized():
    """allowed_values given as a comma-separated string is normalized to a list."""
    response = (
        "```column-proposal\n"
        '{"name": "Scale", "prompt": "What scale?", "allowed_values": "small, medium, large"}\n'
        "```"
    )
    proposals, method = parse_column_proposals(response)
    assert method == "fenced_block"
    assert len(proposals) == 1
    assert proposals[0]["allowed_values"] == ["small", "medium", "large"]


def test_parse_allowed_values_null_like_string_normalized():
    """allowed_values of 'null', 'N/A', or empty string are normalized to None."""
    for null_str in ("null", "none", "N/A", "free-form", ""):
        response = (
            "```column-proposal\n"
            f'{{"name": "X", "prompt": "X?", "allowed_values": "{null_str}"}}\n'
            "```"
        )
        proposals, _ = parse_column_proposals(response)
        assert len(proposals) == 1, f"failed for null_str={null_str!r}"
        assert proposals[0]["allowed_values"] is None, f"expected None for {null_str!r}"


def test_parse_markdown_table_real_world_example():
    """Markdown table from GPT-OSS:120B output is parsed via markdown_table strategy."""
    response = (
        "| # | Column (what to list) | Why it helps to differentiate the two papers |\n"
        "|---|-----------------------|----------------------------------------------|\n"
        "| 1 | **Reference** (authors, year, venue) | Basic bibliographic identifier. |\n"
        "| 2 | **Model name** | *MOIRAI-MoE* vs. *MOIRAI-MoE (with token-clustering gating)* |\n"
        "| 3 | **Model family** (e.g., Decoder-only MoE, Encoder-decoder Transformer) | One paper introduces a **decoder-only MoE** architecture. |\n"
        "| 4 | **Number of experts** (and expert size) | The MoE paper varies the number of experts. |\n"
    )
    proposals, method = parse_column_proposals(response)
    assert method == "markdown_table"
    assert len(proposals) == 4
    assert proposals[0]["name"] == "Reference"
    assert proposals[1]["name"] == "Model name"
    assert proposals[2]["name"] == "Model family"
    assert proposals[3]["name"] == "Number of experts"
    # No bold markers or parentheticals in names
    for p in proposals:
        assert "**" not in p["name"]
        assert "(" not in p["name"]
    # Descriptions populated from the "Why" column
    assert "bibliographic" in proposals[0]["description"]
    assert "experts" in proposals[3]["description"]
    # prompt equals description (no separate prompt column)
    for p in proposals:
        assert p["prompt"] == p["description"]
    # allowed_values always None
    for p in proposals:
        assert p["allowed_values"] is None


def test_parse_markdown_table_no_name_header_falls_through():
    """A table with no column/name header is not parsed as markdown_table."""
    response = (
        "| A | B | C |\n"
        "|---|---|---|\n"
        "| x | y | z |\n"
    )
    proposals, method = parse_column_proposals(response)
    assert proposals == []
    assert method == "failed"


def test_parse_markdown_list_strategy():
    """A markdown key-value list is parsed via markdown_list strategy."""
    response = (
        "Here is my proposal:\n\n"
        "**Name**: Sample Size\n"
        "**Prompt**: How many participants are in the study?\n"
        "**Description**: Number of subjects or data points\n"
        "**Allowed Values**: small, medium, large\n"
    )
    proposals, method = parse_column_proposals(response)
    assert method == "markdown_list"
    assert len(proposals) == 1
    assert proposals[0]["name"] == "Sample Size"
    assert proposals[0]["prompt"] == "How many participants are in the study?"
    assert proposals[0]["allowed_values"] == ["small", "medium", "large"]


def test_parse_completely_non_compliant_returns_empty():
    """A completely non-compliant response returns empty list with method 'failed'."""
    response = (
        "I can help you design a schema! What is your research topic?\n"
        "Here are some ideas to consider: methodology, evaluation, limitations, etc.\n"
        "Just let me know what you'd like to extract."
    )
    proposals, method = parse_column_proposals(response)
    assert proposals == []
    assert method == "failed"


def test_parse_malformed_bare_json_no_crash():
    """Malformed JSON (missing closing brace) does not raise; returns empty or partial."""
    response = (
        'Here: {"name": "X", "prompt": "Y?"'
        # intentionally no closing }
    )
    # Must not raise
    proposals, method = parse_column_proposals(response)
    # If it managed to parse something, great; if not, must be empty/failed
    assert isinstance(proposals, list)
    assert isinstance(method, str)


def test_parse_returns_tuple_with_method_string():
    """parse_column_proposals always returns a (list, str) tuple."""
    for response in [
        "Nothing here",
        '{"name": "X", "prompt": "Y?"}',
        "```column-proposal\n{}\n```",
    ]:
        result = parse_column_proposals(response)
        assert isinstance(result, tuple) and len(result) == 2
        proposals, method = result
        assert isinstance(proposals, list)
        assert isinstance(method, str)


# ---------------------------------------------------------------------------
# 35–38: build_schema_discussion_prompt — new param and format tests
# ---------------------------------------------------------------------------


def test_prompt_format_critical_added_for_local_model(db_session):
    """[FORMAT CRITICAL] block is present when provider='openai' and model is not gpt-."""
    prompt = build_schema_discussion_prompt(
        None,
        provider="openai",
        model_id="llama3:8b",
    )
    assert "[FORMAT CRITICAL]" in prompt


def test_prompt_format_critical_not_added_for_gpt_model(db_session):
    """[FORMAT CRITICAL] block is absent for official gpt- models."""
    prompt = build_schema_discussion_prompt(
        None,
        provider="openai",
        model_id="gpt-4o",
    )
    assert "[FORMAT CRITICAL]" not in prompt


def test_prompt_format_critical_not_added_for_anthropic(db_session):
    """[FORMAT CRITICAL] block is absent for anthropic provider."""
    prompt = build_schema_discussion_prompt(
        None,
        provider="anthropic",
        model_id="claude-opus-4-6",
    )
    assert "[FORMAT CRITICAL]" not in prompt


def test_prompt_contains_concrete_example(db_session):
    """The system prompt includes a concrete realistic example of a column-proposal block."""
    prompt = build_schema_discussion_prompt(None)
    # The concrete 'Sample Size' example should appear
    assert "Sample Size" in prompt
    # Bookend reminder is present
    assert "Remember:" in prompt
    # Negative instructions are present
    assert "Do NOT" in prompt
