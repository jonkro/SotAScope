"""System prompt builder and LLM-response parser for extraction schema discussions."""

from __future__ import annotations

import json
import logging
import re

from litexplorer.models.extraction import ExtractionSchema

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pre-processing helpers
# ---------------------------------------------------------------------------

_THINKING_TAGS_RE = re.compile(
    r"<(?:think|thinking|reasoning|scratchpad)>.*?</(?:think|thinking|reasoning|scratchpad)>",
    re.DOTALL | re.IGNORECASE,
)


def _strip_thinking_tags(text: str) -> str:
    """Remove <think>...</think> and similar reasoning wrappers from LLM output."""
    return _THINKING_TAGS_RE.sub("", text).strip()


# ---------------------------------------------------------------------------
# Field-name normalization
# ---------------------------------------------------------------------------

# Maps raw field name variants → canonical proposal field name
_FIELD_ALIASES: dict[str, str] = {
    # name
    "name": "name",
    "title": "name",
    "column_name": "name",
    "column": "name",
    # prompt
    "prompt": "prompt",
    "question": "prompt",
    "extraction_prompt": "prompt",
    "query": "prompt",
    "llm_prompt": "prompt",
    # description
    "description": "description",
    "desc": "description",
    "explanation": "description",
    "details": "description",
    # allowed_values
    "allowed_values": "allowed_values",
    "values": "allowed_values",
    "options": "allowed_values",
    "choices": "allowed_values",
    "valid_values": "allowed_values",
    "allowed": "allowed_values",
}

# Strings that mean "no constrained values" when found in allowed_values
_NULL_LIKE = frozenset({"null", "none", "n/a", "na", "free-form", "freeform", "free form", ""})


def _normalize_field_name(raw: str) -> str | None:
    """Map a raw field name to its canonical form, or None if not recognized."""
    return _FIELD_ALIASES.get(raw.lower().strip().replace(" ", "_"))


def _normalize_allowed_values(value: object) -> list[str] | None:
    """Convert allowed_values to ``list[str]`` or ``None``.

    Handles:
    - ``None`` → ``None``
    - list → ``list[str]`` (empty list → ``None``)
    - comma-separated string → list of strings
    - null-like strings ("null", "none", "N/A", etc.) → ``None``
    """
    if value is None:
        return None
    if isinstance(value, list):
        items = [str(v) for v in value]
        return items if items else None
    if isinstance(value, str):
        s = value.strip()
        if s.lower() in _NULL_LIKE:
            return None
        parts = [p.strip() for p in s.split(",") if p.strip()]
        return parts if parts else None
    return None


# ---------------------------------------------------------------------------
# Proposal dict extraction helpers
# ---------------------------------------------------------------------------


def _looks_like_proposal(data: dict) -> bool:
    """Return True if a dict has normalized keys for at least name + prompt."""
    normalized = {_normalize_field_name(k) for k in data}
    return "name" in normalized and "prompt" in normalized


def _extract_proposal_from_dict(data: dict) -> dict | None:
    """Normalize raw field names and build a canonical proposal dict.

    Returns ``None`` when ``name`` or ``prompt`` are absent or empty.
    """
    normalized: dict[str, object] = {}
    for raw_key, raw_val in data.items():
        canonical = _normalize_field_name(raw_key)
        if canonical and canonical not in normalized:
            normalized[canonical] = raw_val

    name = str(normalized.get("name", "")).strip()
    prompt = str(normalized.get("prompt", "")).strip()
    if not name or not prompt:
        return None

    return {
        "name": name,
        "prompt": prompt,
        "description": str(normalized.get("description", "")).strip(),
        "allowed_values": _normalize_allowed_values(normalized.get("allowed_values")),
    }


def _parse_proposals_from_json_text(text: str) -> list[dict]:
    """Try to parse one or more proposals from a JSON text block.

    Handles:
    - Single dict with proposal fields
    - Array of dicts
    - Dict with a ``"proposals"`` / ``"columns"`` wrapper key
    """
    try:
        data = json.loads(text.strip())
    except json.JSONDecodeError:
        return []

    results: list[dict] = []

    if isinstance(data, dict):
        # Check for wrapper keys
        for key in ("proposals", "columns", "column_proposals"):
            if isinstance(data.get(key), list):
                for item in data[key]:
                    if isinstance(item, dict) and _looks_like_proposal(item):
                        p = _extract_proposal_from_dict(item)
                        if p:
                            results.append(p)
                return results
        # Try as a single proposal
        if _looks_like_proposal(data):
            p = _extract_proposal_from_dict(data)
            if p:
                results.append(p)

    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and _looks_like_proposal(item):
                p = _extract_proposal_from_dict(item)
                if p:
                    results.append(p)

    return results


def _find_balanced(text: str, open_char: str, close_char: str, start: int) -> str | None:
    """Return the balanced substring starting at *start*, or None if unbalanced."""
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


# ---------------------------------------------------------------------------
# Column-proposal parsing — per-strategy helpers
# ---------------------------------------------------------------------------

# Strategy 1: ```column-proposal … ``` (or end-of-string)
_PROPOSAL_FENCE_RE = re.compile(
    r"```column-proposal\s*\n(.*?)(?:```|$)",
    re.DOTALL,
)

# Strategy 2: any code fence that is NOT column-proposal
_GENERIC_FENCE_RE = re.compile(
    r"```(?!column-proposal)(\w*)\s*\n(.*?)```",
    re.DOTALL,
)

# Strategy 4: key-value lines like "**Name**: Sample Size" or "- Prompt: ..."
# Use [ \t]* (horizontal whitespace only) around the colon so the pattern
# cannot span across lines via \s* matching \n characters.
_KV_LINE_RE = re.compile(
    r"^[ \t]*[-•*>]?[ \t]*\**([^:\n]{2,60}?)\**[ \t]*:[ \t]*(.+)",
    re.MULTILINE,
)


def _strategy1_fenced_blocks(text: str) -> list[dict]:
    """Strategy 1: parse ``column-proposal`` fenced blocks."""
    proposals: list[dict] = []
    for match in _PROPOSAL_FENCE_RE.finditer(text):
        block = match.group(1).strip()
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            p = _extract_proposal_from_dict(data)
            if p:
                proposals.append(p)
    return proposals


def _strategy2_generic_fenced_blocks(text: str) -> list[dict]:
    """Strategy 2: parse generic code fences (``json``, bare `` ``` ``, etc.)."""
    proposals: list[dict] = []
    for match in _GENERIC_FENCE_RE.finditer(text):
        block = match.group(2).strip()
        proposals.extend(_parse_proposals_from_json_text(block))
    return proposals


def _strategy3_bare_json(text: str) -> list[dict]:
    """Strategy 3: scan for bare ``[...]`` or ``{...}`` JSON blocks in prose."""
    # Try arrays first — they may encode multiple proposals
    pos = 0
    while pos < len(text):
        idx = text.find("[", pos)
        if idx == -1:
            break
        candidate = _find_balanced(text, "[", "]", idx)
        if candidate:
            proposals = _parse_proposals_from_json_text(candidate)
            if proposals:
                return proposals
        pos = idx + 1

    # Fall back to individual objects
    results: list[dict] = []
    pos = 0
    while pos < len(text):
        idx = text.find("{", pos)
        if idx == -1:
            break
        candidate = _find_balanced(text, "{", "}", idx)
        if candidate:
            proposals = _parse_proposals_from_json_text(candidate)
            results.extend(proposals)
            pos = idx + len(candidate)
        else:
            pos = idx + 1

    return results


def _strategy4_markdown_list(text: str) -> list[dict]:
    """Strategy 4: extract a proposal from markdown key-value patterns.

    Looks for lines like::

        **Name**: Sample Size
        **Prompt**: How many subjects are in the study?
        **Description**: Number of participants or data points.
        **Allowed Values**: small, medium, large
    """
    matches = _KV_LINE_RE.findall(text)
    if not matches:
        return []

    raw: dict[str, str] = {}
    for key, value in matches:
        canonical = _normalize_field_name(key.strip())
        if canonical and canonical not in raw:
            raw[canonical] = value.strip()

    name = raw.get("name", "")
    prompt = raw.get("prompt", "")
    if not name or not prompt:
        return []

    return [{
        "name": name,
        "prompt": prompt,
        "description": raw.get("description", ""),
        "allowed_values": _normalize_allowed_values(raw.get("allowed_values")),
    }]


# ---------------------------------------------------------------------------
# Column-proposal parsing — main entry point
# ---------------------------------------------------------------------------


def parse_column_proposals(llm_response: str) -> tuple[list[dict], str]:
    """Extract column proposals from an LLM response using multiple strategies.

    Strategies tried in order:

    1. **fenced_block**: ``column-proposal`` fenced blocks (canonical format).
    2. **json_fence**: Generic code fences (``json``, bare `` ``` ``, etc.)
       that contain JSON with recognizable proposal fields.
    3. **bare_json**: Bare ``{...}`` or ``[...]`` JSON structures embedded in
       prose, including JSON arrays of proposal objects.
    4. **markdown_list**: Markdown key-value patterns
       (``**Name**: ..., **Prompt**: ...``).
    5. **failed**: All strategies exhausted — returns an empty list.

    Pre-processing (always applied before strategy 1):

    - Strip ``<think>`` / ``<thinking>`` / ``<reasoning>`` / ``<scratchpad>``
      thinking tags and their content.

    Field-name normalization (applied in all strategies):

    - ``title`` / ``column_name`` / ``column`` → ``name``
    - ``question`` / ``extraction_prompt`` / ``query`` → ``prompt``
    - ``desc`` / ``explanation`` / ``details`` → ``description``
    - ``values`` / ``options`` / ``choices`` / ``valid_values`` → ``allowed_values``

    ``allowed_values`` normalization:

    - Comma-separated string → list of strings
    - ``"null"`` / ``"none"`` / ``"N/A"`` / empty string → ``None``
    - Empty list → ``None``

    Args:
        llm_response: Raw text returned by the LLM.

    Returns:
        A 2-tuple ``(proposals, parsing_method)`` where *proposals* is a list
        of dicts with keys ``name``, ``prompt``, ``description``,
        ``allowed_values``, and *parsing_method* is one of
        ``"fenced_block"``, ``"json_fence"``, ``"bare_json"``,
        ``"markdown_list"``, or ``"failed"``.
    """
    text = _strip_thinking_tags(llm_response)

    proposals = _strategy1_fenced_blocks(text)
    if proposals:
        return proposals, "fenced_block"

    proposals = _strategy2_generic_fenced_blocks(text)
    if proposals:
        return proposals, "json_fence"

    proposals = _strategy3_bare_json(text)
    if proposals:
        return proposals, "bare_json"

    proposals = _strategy4_markdown_list(text)
    if proposals:
        return proposals, "markdown_list"

    logger.debug(
        "parse_column_proposals: all strategies failed. First 200 chars: %r",
        llm_response[:200],
    )
    return [], "failed"


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------


def build_schema_discussion_prompt(
    schema: ExtractionSchema | None,
    system_prefix: str = "",
    project_title: str = "",
    provider: str = "",
    model_id: str = "",
) -> str:
    """Build the system prompt for an extraction schema design conversation.

    Args:
        schema: An existing :class:`ExtractionSchema`, or ``None`` when the
            user is designing a new schema from scratch.
        system_prefix: Optional user-configured prefix prepended to the prompt.
        project_title: Title of the project, for contextual grounding.
        provider: LLM provider string (e.g. ``"openai"`` or ``"anthropic"``).
            Used to add extra format reinforcement for local/non-GPT models.
        model_id: Model ID string. Combined with *provider* to detect local
            models that need extra formatting instructions.

    Returns:
        The full system prompt string.
    """
    parts: list[str] = []

    if system_prefix:
        parts.append(system_prefix.rstrip())
        parts.append("")  # blank line separator

    parts.append(
        "You are a research assistant helping a researcher design a structured extraction "
        "schema for a systematic literature review. "
        "Your goal is to help define extraction columns — each column is a structured question "
        "that will be answered by an LLM for every paper in the corpus, producing a table of "
        "comparable, structured data across papers."
    )

    if project_title:
        parts.append(f"The researcher is working on a project titled: {project_title!r}.")

    if schema is not None:
        parts.append(
            f"\nThe researcher is refining an existing extraction schema titled {schema.title!r}."
        )
        if schema.description:
            parts.append(f"Schema description: {schema.description}")

        columns = sorted(schema.columns, key=lambda c: c.sort_order)
        if columns:
            parts.append(f"\nCurrent columns ({len(columns)} defined):")
            for i, col in enumerate(columns, start=1):
                col_lines = [f"  {i}. {col.name}"]
                col_lines.append(f"     Prompt: {col.prompt}")
                if col.description:
                    col_lines.append(f"     Description: {col.description}")
                if col.allowed_values:
                    col_lines.append(f"     Allowed values: {', '.join(col.allowed_values)}")
                parts.append("\n".join(col_lines))
        else:
            parts.append("No columns have been defined yet.")
    else:
        parts.append(
            "\nThe researcher is designing a new extraction schema from scratch. "
            "Help them identify what structured information would be most useful to extract "
            "from each paper in their literature corpus."
        )

    parts.append(
        "\n## Proposing a column"
        "\n"
        "When you want to propose a new column (or a revision to an existing one), "
        "output a fenced code block tagged `column-proposal` containing a JSON object "
        "with exactly these four fields:"
        "\n"
        "```column-proposal\n"
        "{\n"
        '  "name": "Sample Size",\n'
        '  "prompt": "How many participants or data points does the study use?",\n'
        '  "description": "The number of subjects in the dataset or experiment (e.g. 50 patients, 10k images).",\n'
        '  "allowed_values": null\n'
        "}\n"
        "```"
        "\n"
        "For a constrained column, set `allowed_values` to a JSON array of strings, e.g.:\n"
        '`"allowed_values": ["Yes", "No"]` or `"allowed_values": ["Supervised", "Unsupervised", "Semi-supervised"]`'
        "\n\n"
        "Rules for proposals:\n"
        "- `name` must be concise (1–5 words) and unique within the schema.\n"
        "- `prompt` should be a clear, answerable question about a single paper.\n"
        "- `description` is a short explanation of what this column captures and why it is useful.\n"
        "- Set `allowed_values` to `null` for free-form text answers.\n"
        "- Use ONLY the four field names listed above: name, prompt, description, allowed_values.\n"
        "- Do NOT use field names other than these four (no 'question', 'title', 'values', etc.).\n"
        "- Do NOT add comments, trailing commas, or markdown formatting inside the JSON.\n"
        "- Do NOT output proposals as markdown tables, numbered lists, or plain prose — "
        "always use the fenced block format shown above.\n"
        "- You may include multiple `column-proposal` blocks in one reply.\n"
        "- Always explain your reasoning in prose before or after the code block."
    )

    parts.append(
        "\n## Examples of correctly formatted proposals"
        "\n"
        "Study these two examples before proposing any columns. "
        "They show the exact fenced-block format the system expects — "
        "one constrained column (with allowed_values) and one free-form column (allowed_values is null)."
        "\n\n"
        "**Example A — constrained column:**\n"
        "```column-proposal\n"
        "{\n"
        '  "name": "Model family",\n'
        '  "prompt": "Which broad family of model architecture does this paper use or propose?",\n'
        '  "description": "The high-level architecture family. Use Other if the model does not fit the listed categories.",\n'
        '  "allowed_values": ["Transformer", "CNN", "GNN", "RNN", "Other"]\n'
        "}\n"
        "```"
        "\n\n"
        "**Example B — free-form column:**\n"
        "```column-proposal\n"
        "{\n"
        '  "name": "Key contribution",\n'
        '  "prompt": "What is the primary technical contribution of this paper in one or two sentences?",\n'
        '  "description": "A concise statement of what is novel — the main algorithmic idea, dataset, or theoretical result.",\n'
        '  "allowed_values": null\n'
        "}\n"
        "```"
    )

    parts.append(
        "\n## Conversation style"
        "\n"
        "Engage conversationally. Ask clarifying questions to understand the research topic "
        "and what comparisons the researcher wants to make across papers. "
        "Suggest columns based on common structured review dimensions "
        "(methodology, dataset, metrics, results, limitations, etc.) but tailor them to the "
        "specific research topic. Be concise and actionable."
    )

    # Extra-aggressive format instruction for local / non-GPT OpenAI-compatible models
    _is_local_model = provider == "openai" and not model_id.startswith("gpt-")
    if _is_local_model:
        parts.append(
            "\n[FORMAT CRITICAL] Every column proposal MUST appear in a ```column-proposal "
            "fenced JSON block. Do NOT substitute markdown tables, numbered lists, or plain "
            "prose descriptions for fenced blocks. Start each proposal block with "
            "```column-proposal on its own line and close it with ``` on its own line. "
            "The block content must be valid JSON — no comments, no trailing commas."
        )

    # Bookend reminder (weak models pay more attention to instructions near the end)
    parts.append(
        "\nRemember: every column you propose MUST be wrapped in a ```column-proposal``` "
        "fenced JSON block, exactly as shown in the examples above. "
        "Do NOT use markdown tables, numbered lists, bullet points, or plain prose descriptions "
        "instead of fenced blocks — those formats cannot be automatically parsed and accepted. "
        'The four required JSON fields are: "name", "prompt", "description", "allowed_values".'
    )

    return "\n".join(parts)
