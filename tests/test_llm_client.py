"""Tests for the LLM client abstraction and GET /api/llm/models endpoint.

All Anthropic and OpenAI SDK calls are mocked — no real API calls are made.
"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from litexplorer.api.deps import get_db
from litexplorer.external.llm_client import (
    AnthropicLLMClient,
    ContextDocument,
    OpenAILLMClient,
    make_llm_client,
)
from litexplorer.models.base import Base
from litexplorer.models.settings import Setting


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
def client(db_session):
    from litexplorer.app import app

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_doc(
    work_id: int = 1,
    title: str = "Test Paper",
    year: int | None = 2023,
    text: str | None = None,
    pdf_bytes: bytes | None = None,
    remark: str | None = None,
) -> ContextDocument:
    return ContextDocument(
        work_id=work_id,
        title=title,
        year=year,
        text=text,
        pdf_bytes=pdf_bytes,
        remark=remark,
    )


# ---------------------------------------------------------------------------
# AnthropicLLMClient — chat()
# ---------------------------------------------------------------------------


def test_anthropic_chat_text_context():
    """chat() sends correct system prompt and text content blocks for a text document."""
    with patch("litexplorer.external.llm_client.anthropic") as mock_mod:
        mock_instance = MagicMock()
        mock_mod.Anthropic.return_value = mock_instance
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="The answer")]
        mock_instance.messages.create.return_value = mock_resp

        c = AnthropicLLMClient(api_key="test-key", model_id="claude-test")
        doc = _make_doc(title="My Paper", year=2022, text="Some extracted text")
        result = c.chat([{"role": "user", "content": "What is this paper about?"}], [doc])

        assert result == "The answer"

        call_kwargs = mock_instance.messages.create.call_args.kwargs
        assert call_kwargs["system"] == (
            "You are a research assistant helping analyze academic literature."
        )

        messages_sent = call_kwargs["messages"]
        first_msg = messages_sent[0]
        assert first_msg["role"] == "user"

        blocks = first_msg["content"]
        # Block 0: header
        assert blocks[0]["type"] == "text"
        assert "My Paper" in blocks[0]["text"]
        assert "2022" in blocks[0]["text"]
        # Block 1: extracted text
        assert blocks[1]["type"] == "text"
        assert blocks[1]["text"] == "Some extracted text"
        # Block 2: original user message
        assert blocks[2]["type"] == "text"
        assert blocks[2]["text"] == "What is this paper about?"


def test_anthropic_chat_pdf_context():
    """chat() adds a document content block with base64 PDF data when pdf_bytes is set."""
    with patch("litexplorer.external.llm_client.anthropic") as mock_mod:
        mock_instance = MagicMock()
        mock_mod.Anthropic.return_value = mock_instance
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="PDF summary")]
        mock_instance.messages.create.return_value = mock_resp

        c = AnthropicLLMClient(api_key="test-key", model_id="claude-test")
        pdf_data = b"%PDF-1.4 fake pdf content"
        doc = _make_doc(title="PDF Paper", year=2021, pdf_bytes=pdf_data)
        result = c.chat([{"role": "user", "content": "Summarize."}], [doc])

        assert result == "PDF summary"

        messages_sent = mock_instance.messages.create.call_args.kwargs["messages"]
        blocks = messages_sent[0]["content"]

        # Block 0: header text, block 1: document content block
        assert len(blocks) >= 2
        doc_block = blocks[1]
        assert doc_block["type"] == "document"
        assert doc_block["source"]["type"] == "base64"
        assert doc_block["source"]["media_type"] == "application/pdf"
        assert doc_block["source"]["data"] == base64.b64encode(pdf_data).decode()


def test_anthropic_chat_no_content():
    """chat() produces a 'No content available' text block when text and pdf_bytes are None."""
    with patch("litexplorer.external.llm_client.anthropic") as mock_mod:
        mock_instance = MagicMock()
        mock_mod.Anthropic.return_value = mock_instance
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="OK")]
        mock_instance.messages.create.return_value = mock_resp

        c = AnthropicLLMClient(api_key="test-key", model_id="claude-test")
        doc = _make_doc(title="Empty Paper", year=2020)  # text=None, pdf_bytes=None
        c.chat([{"role": "user", "content": "Discuss."}], [doc])

        messages_sent = mock_instance.messages.create.call_args.kwargs["messages"]
        blocks = messages_sent[0]["content"]

        fallback_block = blocks[1]
        assert fallback_block["type"] == "text"
        assert "No content available" in fallback_block["text"]


# ---------------------------------------------------------------------------
# AnthropicLLMClient — list_models()
# ---------------------------------------------------------------------------


def test_anthropic_list_models():
    """list_models() GETs the Anthropic models endpoint and returns model IDs."""
    mock_http_resp = MagicMock()
    mock_http_resp.json.return_value = {
        "data": [
            {"id": "claude-sonnet-4-6"},
            {"id": "claude-haiku-4-5-20251001"},
        ]
    }
    mock_http_resp.raise_for_status = MagicMock()

    with patch("litexplorer.external.llm_client.anthropic") as mock_mod:
        mock_mod.Anthropic.return_value = MagicMock()
        with patch("litexplorer.external.llm_client.httpx") as mock_httpx:
            mock_httpx.get.return_value = mock_http_resp

            c = AnthropicLLMClient(api_key="test-key", model_id="claude-test")
            models = c.list_models()

    assert models == ["claude-sonnet-4-6", "claude-haiku-4-5-20251001"]

    call_args = mock_httpx.get.call_args
    assert "api.anthropic.com/v1/models" in call_args.args[0]
    assert call_args.kwargs["headers"]["x-api-key"] == "test-key"


# ---------------------------------------------------------------------------
# OpenAILLMClient — chat()
# ---------------------------------------------------------------------------


def test_openai_chat_text_context():
    """chat() sends a system message and context prepended to the user message."""
    with patch("litexplorer.external.llm_client.openai") as mock_mod:
        mock_instance = MagicMock()
        mock_mod.OpenAI.return_value = mock_instance
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock(message=MagicMock(content="OpenAI answer"))]
        mock_instance.chat.completions.create.return_value = mock_completion

        c = OpenAILLMClient(api_key="sk-test", model_id="gpt-4o")
        doc = _make_doc(title="OA Paper", year=2024, text="Paper text here")
        result = c.chat([{"role": "user", "content": "Explain this."}], [doc])

        assert result == "OpenAI answer"

        messages_sent = mock_instance.chat.completions.create.call_args.kwargs["messages"]
        # First message is the system prompt
        assert messages_sent[0]["role"] == "system"
        assert "research assistant" in messages_sent[0]["content"]
        # Second message is the user turn with context prepended
        user_msg = messages_sent[1]
        assert user_msg["role"] == "user"
        assert "OA Paper" in user_msg["content"]
        assert "2024" in user_msg["content"]
        assert "Paper text here" in user_msg["content"]
        assert "Explain this." in user_msg["content"]


def test_openai_chat_pdf_ignored():
    """chat() silently ignores pdf_bytes and uses extracted text instead."""
    with patch("litexplorer.external.llm_client.openai") as mock_mod:
        mock_instance = MagicMock()
        mock_mod.OpenAI.return_value = mock_instance
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock(message=MagicMock(content="text response"))]
        mock_instance.chat.completions.create.return_value = mock_completion

        c = OpenAILLMClient(api_key="sk-test", model_id="gpt-4o")
        doc = _make_doc(
            title="PDF Paper",
            year=2023,
            text="Extracted text content",
            pdf_bytes=b"%PDF-1.4 fake",
        )
        c.chat([{"role": "user", "content": "Summarize."}], [doc])

        messages_sent = mock_instance.chat.completions.create.call_args.kwargs["messages"]
        user_msg = messages_sent[1]
        assert "Extracted text content" in user_msg["content"]
        # pdf_bytes must not appear as base64 or raw binary
        assert "base64" not in user_msg["content"]
        assert "%PDF" not in user_msg["content"]


# ---------------------------------------------------------------------------
# OpenAILLMClient — list_models()
# ---------------------------------------------------------------------------


def test_openai_list_models_cloud_filters_gpt():
    """list_models() for OpenAI cloud (no base_url) returns only 'gpt' model IDs."""
    with patch("litexplorer.external.llm_client.openai") as mock_mod:
        mock_instance = MagicMock()
        mock_mod.OpenAI.return_value = mock_instance
        mock_instance.models.list.return_value = [
            MagicMock(id="gpt-4o"),
            MagicMock(id="gpt-4-turbo"),
            MagicMock(id="text-embedding-ada-002"),
            MagicMock(id="whisper-1"),
        ]

        c = OpenAILLMClient(api_key="sk-test", model_id="gpt-4o")  # base_url=None
        models = c.list_models()

    assert models == ["gpt-4o", "gpt-4-turbo"]


def test_openai_list_models_local_returns_all():
    """list_models() for a local server uses httpx directly (no Bearer token when
    api_key is empty) and returns all model IDs unfiltered."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": [
            {"id": "llama3.2"},
            {"id": "mistral-7b"},
            {"id": "phi-3"},
        ]
    }
    mock_response.raise_for_status = MagicMock()

    with patch("litexplorer.external.llm_client.openai") as mock_mod, \
         patch("litexplorer.external.llm_client.httpx.get", return_value=mock_response) as mock_get:
        mock_mod.OpenAI.return_value = MagicMock()

        c = OpenAILLMClient(
            api_key="",
            model_id="llama3.2",
            base_url="http://localhost:11434/v1",
        )
        models = c.list_models()

    assert models == ["llama3.2", "mistral-7b", "phi-3"]
    # Verify no Authorization header was sent (empty api_key)
    mock_get.assert_called_once_with(
        "http://localhost:11434/v1/models", headers={}
    )


def test_openai_list_models_local_with_key_sends_bearer():
    """list_models() for a local server with a real api_key sends Bearer auth."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"data": [{"id": "my-model"}]}
    mock_response.raise_for_status = MagicMock()

    with patch("litexplorer.external.llm_client.openai") as mock_mod, \
         patch("litexplorer.external.llm_client.httpx.get", return_value=mock_response) as mock_get:
        mock_mod.OpenAI.return_value = MagicMock()

        c = OpenAILLMClient(
            api_key="sk-local-key",
            model_id="my-model",
            base_url="http://localhost:11434/v1",
        )
        models = c.list_models()

    assert models == ["my-model"]
    mock_get.assert_called_once_with(
        "http://localhost:11434/v1/models",
        headers={"Authorization": "Bearer sk-local-key"},
    )


# ---------------------------------------------------------------------------
# make_llm_client factory
# ---------------------------------------------------------------------------


def test_make_llm_client_unsupported_provider():
    """make_llm_client() raises ValueError for an unrecognised provider string."""
    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        make_llm_client("unknown_provider", "key", "model", None)


# ---------------------------------------------------------------------------
# GET /api/llm/models endpoint
# ---------------------------------------------------------------------------


def test_llm_models_provider_not_configured(db_session, client):
    """Returns {'models': []} when llm_provider is not set in the DB."""
    # No settings rows inserted — get_setting_value returns None for llm_provider
    resp = client.get("/api/llm/models")
    assert resp.status_code == 200
    assert resp.json() == {"models": []}


def test_llm_models_sdk_error_returns_200_with_error(db_session, client):
    """Returns HTTP 200 with {'models': [], 'error': '...'} on SDK failure."""
    db_session.add(Setting(key="llm_provider", value="anthropic", description="test"))
    db_session.add(Setting(key="llm_api_key", value="bad-key", description="test"))
    db_session.add(Setting(key="llm_model_id", value="claude-test", description="test"))
    db_session.add(Setting(key="llm_base_url", value="", description="test"))
    db_session.commit()

    with patch("litexplorer.api.llm.make_llm_client") as mock_factory:
        mock_llm = MagicMock()
        mock_llm.list_models.side_effect = Exception("Authentication failed")
        mock_factory.return_value = mock_llm

        resp = client.get("/api/llm/models")

    assert resp.status_code == 200
    data = resp.json()
    assert data["models"] == []
    assert "Authentication failed" in data["error"]
