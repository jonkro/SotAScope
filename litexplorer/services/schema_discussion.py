"""System prompt builder and LLM-response parser for extraction schema discussions."""

from __future__ import annotations

import json
import re

from litexplorer.models.extraction import ExtractionSchema


# ---------------------------------------------------------------------------
# Column-proposal parsing
# ---------------------------------------------------------------------------

# Matches ```column-proposal … ``` (or end-of-string if the closing fence is missing).
_PROPOSAL_FENCE_RE = re.compile(
    r"```column-proposal\s*\n(.*?)(?:```|$)",
    re.DOTALL,
)


def parse_column_proposals(llm_response: str) -> list[dict]:
    """Extract all ``column-proposal`` fenced blocks from an LLM response.

    Each block is expected to contain a JSON object with the fields
    ``name``, ``prompt``, ``description``, and ``allowed_values``.

    Leniency rules:
    - Leading/trailing whitespace inside the block is stripped before parsing.
    - A missing closing fence (end-of-string) is accepted.
    - Blocks with invalid JSON are silently skipped.
    - ``description`` defaults to ``""`` when absent.
    - ``allowed_values`` defaults to ``None`` when absent or not a list.
    - Proposals without a non-empty ``name`` or ``prompt`` are skipped.

    Args:
        llm_response: Raw text returned by the LLM.

    Returns:
        List of proposal dicts, each with keys:
        ``name`` (str), ``prompt`` (str), ``description`` (str),
        ``allowed_values`` (list[str] | None).
        Returns ``[]`` if no valid proposals are found.
    """
    proposals: list[dict] = []

    for match in _PROPOSAL_FENCE_RE.finditer(llm_response):
        block_content = match.group(1).strip()
        try:
            data = json.loads(block_content)
        except json.JSONDecodeError:
            continue

        if not isinstance(data, dict):
            continue

        name = str(data.get("name", "")).strip()
        prompt = str(data.get("prompt", "")).strip()
        if not name or not prompt:
            # A proposal without name or prompt is not actionable
            continue

        description = str(data.get("description", "")).strip()

        allowed_values = data.get("allowed_values")
        if allowed_values is not None and not isinstance(allowed_values, list):
            allowed_values = None

        proposals.append({
            "name": name,
            "prompt": prompt,
            "description": description,
            "allowed_values": allowed_values,
        })

    return proposals


def build_schema_discussion_prompt(
    schema: ExtractionSchema | None,
    system_prefix: str = "",
    project_title: str = "",
) -> str:
    """Build the system prompt for an extraction schema design conversation.

    Args:
        schema: An existing :class:`ExtractionSchema`, or ``None`` when the user
            is designing a new schema from scratch.
        system_prefix: Optional user-configured prefix prepended to the prompt.
        project_title: Title of the project, for contextual grounding.

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
        "with exactly these fields:"
        "\n"
        "```column-proposal\n"
        "{\n"
        '  "name": "Short column name (used as table header)",\n'
        '  "prompt": "The exact question posed to the LLM for each paper",\n'
        '  "description": "Short explanation of what this column captures and why it is useful",\n'
        '  "allowed_values": ["option1", "option2"] or null for free-form answers\n'
        "}\n"
        "```"
        "\n"
        "Rules for proposals:\n"
        "- `name` must be concise (1–5 words) and unique within the schema.\n"
        "- `prompt` should be a clear, answerable question about a single paper.\n"
        "- Use `allowed_values` when the answer is one of a small fixed set "
        "(e.g. Yes/No, or a taxonomy of methodologies). Set to null for free-form text.\n"
        "- You may include multiple `column-proposal` blocks in one reply.\n"
        "- Always explain your reasoning in prose before or after the code block."
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

    return "\n".join(parts)
