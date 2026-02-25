"""Provider-agnostic LLM client abstraction for LitExplorer."""

from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]

try:
    import openai
except ImportError:
    openai = None  # type: ignore[assignment]


_SYSTEM_PROMPT = "You are a research assistant helping analyze academic literature."


@dataclass
class ContextDocument:
    """A document to include as context in an LLM conversation."""

    work_id: int
    title: str
    year: int | None
    text: str | None
    pdf_bytes: bytes | None
    remark: str | None


class LLMClient(ABC):
    """Abstract base for LLM provider clients."""

    @abstractmethod
    def chat(self, messages: list[dict], context_documents: list[ContextDocument]) -> str:
        """Send a chat request and return the assistant's reply.

        Args:
            messages: Full conversation history as a list of role/content dicts.
                      Each dict has ``role`` ("user" or "assistant") and ``content``.
                      The last entry is the current user turn.
            context_documents: Papers to include as context. Prepended to the first
                                user message as structured content blocks.

        Returns:
            The assistant's response text.
        """

    @abstractmethod
    def list_models(self) -> list[str]:
        """Return the list of model IDs available from this provider."""


def _build_document_header(doc: ContextDocument) -> str:
    """Build the header line for a document context block."""
    year_str = str(doc.year) if doc.year is not None else "n.d."
    header = f"--- Paper: {doc.title} ({year_str}) ---"
    if doc.remark:
        header += f"\n{doc.remark}"
    return header


class AnthropicLLMClient(LLMClient):
    """LLM client backed by the Anthropic API."""

    def __init__(self, api_key: str, model_id: str) -> None:
        if anthropic is None:
            raise ImportError(
                "anthropic package is required for the Anthropic provider: "
                "pip install anthropic"
            )
        self._api_key = api_key
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model_id = model_id

    def chat(self, messages: list[dict], context_documents: list[ContextDocument]) -> str:
        """Build Anthropic messages with document content blocks and call the API."""
        context_blocks: list[dict] = []
        for doc in context_documents:
            context_blocks.append({"type": "text", "text": _build_document_header(doc)})
            if doc.pdf_bytes is not None:
                context_blocks.append({
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.b64encode(doc.pdf_bytes).decode(),
                    },
                })
            elif doc.text is not None:
                context_blocks.append({"type": "text", "text": doc.text})
            else:
                context_blocks.append({
                    "type": "text",
                    "text": "No content available for this paper.",
                })

        api_messages = list(messages)
        if context_blocks:
            if api_messages:
                first = api_messages[0]
                first_content = first.get("content", "")
                if isinstance(first_content, str):
                    first_content = [{"type": "text", "text": first_content}]
                api_messages[0] = {**first, "content": context_blocks + list(first_content)}
            else:
                api_messages = [{"role": "user", "content": context_blocks}]

        result = self._client.messages.create(
            model=self._model_id,
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            messages=api_messages,
        )
        return result.content[0].text

    def list_models(self) -> list[str]:
        """Fetch available models from the Anthropic models endpoint."""
        resp = httpx.get(
            "https://api.anthropic.com/v1/models",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return [m["id"] for m in data.get("data", [])]


class OpenAILLMClient(LLMClient):
    """LLM client backed by an OpenAI-compatible API.

    Works with OpenAI cloud as well as local inference servers (Ollama, vLLM,
    LM Studio, llama.cpp) that expose an OpenAI-compatible ``/v1`` endpoint.
    """

    def __init__(self, api_key: str, model_id: str, base_url: str | None = None) -> None:
        if openai is None:
            raise ImportError(
                "openai package is required for the OpenAI provider: "
                "pip install openai"
            )
        # Local servers don't validate the key, but the SDK requires a non-empty string.
        effective_key = api_key if api_key else "local"
        kwargs: dict = {"api_key": effective_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = openai.OpenAI(**kwargs)
        self._model_id = model_id
        self._base_url = base_url

    def chat(self, messages: list[dict], context_documents: list[ContextDocument]) -> str:
        """Build an OpenAI-compatible messages list and call the API.

        PDF bytes are silently ignored — only extracted text is used.
        """
        context_parts: list[str] = []
        for doc in context_documents:
            header = _build_document_header(doc)
            # pdf_bytes silently ignored for OpenAI-compatible clients
            if doc.text is not None:
                context_parts.append(f"{header}\n\n{doc.text}")
            else:
                context_parts.append(f"{header}\n\nNo content available for this paper.")

        api_messages: list[dict] = [{"role": "system", "content": _SYSTEM_PROMPT}]

        if context_parts:
            context_str = "\n\n".join(context_parts)
            conv_messages = list(messages)
            if conv_messages:
                first = conv_messages[0]
                first_content = first.get("content", "")
                conv_messages[0] = {**first, "content": f"{context_str}\n\n{first_content}"}
            else:
                conv_messages = [{"role": "user", "content": context_str}]
            api_messages.extend(conv_messages)
        else:
            api_messages.extend(messages)

        result = self._client.chat.completions.create(
            model=self._model_id,
            messages=api_messages,
        )
        return result.choices[0].message.content

    def list_models(self) -> list[str]:
        """Return available model IDs.

        For OpenAI cloud (no base_url), filters to IDs containing ``"gpt"``.
        For local servers (base_url set), returns all IDs unfiltered.
        """
        models = self._client.models.list()
        all_ids = [m.id for m in models]
        if self._base_url is None:
            return [mid for mid in all_ids if "gpt" in mid]
        return all_ids


def make_llm_client(
    provider: str,
    api_key: str,
    model_id: str,
    base_url: str | None,
) -> LLMClient:
    """Instantiate the appropriate LLMClient for the given provider.

    Args:
        provider: ``"anthropic"`` or ``"openai"``.
        api_key: Provider API key. May be empty for local servers.
        model_id: Model ID string.
        base_url: Optional base URL override (enables Ollama, vLLM, etc.).

    Raises:
        ValueError: If *provider* is not a recognised value.
    """
    if provider == "anthropic":
        return AnthropicLLMClient(api_key=api_key, model_id=model_id)
    if provider == "openai":
        return OpenAILLMClient(api_key=api_key, model_id=model_id, base_url=base_url)
    raise ValueError(f"Unsupported LLM provider: {provider!r}")
