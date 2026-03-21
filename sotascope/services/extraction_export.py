"""Export helpers for extraction schemas — CSV and LaTeX tabular."""

from __future__ import annotations

import csv
import io
import re

from sotascope.models.extraction import ExtractionColumn, ExtractionSchema
from sotascope.models.library import Work, WorkNote

# ---------------------------------------------------------------------------
# LaTeX escaping
# ---------------------------------------------------------------------------

_LATEX_ESCAPE_RE = re.compile(r'[\\&%$#_{}~^]')
_LATEX_ESCAPE_MAP: dict[str, str] = {
    '\\': r'\textbackslash{}',
    '&':  r'\&',
    '%':  r'\%',
    '$':  r'\$',
    '#':  r'\#',
    '_':  r'\_',
    '{':  r'\{',
    '}':  r'\}',
    '~':  r'\textasciitilde{}',
    '^':  r'\textasciicircum{}',
}


def _escape_latex(text: str) -> str:
    """Escape all LaTeX special characters in *text* in a single pass."""
    return _LATEX_ESCAPE_RE.sub(lambda m: _LATEX_ESCAPE_MAP[m.group()], text)


def _slugify(text: str) -> str:
    """Convert *text* to a lowercase alphanumeric slug suitable for a LaTeX label."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '_', text)
    return text.strip('_') or 'table'


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


def export_as_csv(
    schema: ExtractionSchema,
    columns: list[ExtractionColumn],
    works: list[Work],
    notes_by_work_column: dict[tuple[int, int], WorkNote],
) -> str:
    """Return a CSV string for *schema*.

    Args:
        schema: The extraction schema (used for the table caption only).
        columns: Ordered list of :class:`ExtractionColumn` objects (header columns).
        works: List of :class:`Work` objects — one row per work.
        notes_by_work_column: Mapping of ``(work_id, column_id)`` → :class:`WorkNote`.
            Missing entries produce an empty cell.

    Returns:
        A UTF-8 CSV string with a header row and one data row per work.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)

    # Header row
    writer.writerow(["Paper"] + [col.name for col in columns])

    # Data rows
    for work in works:
        year = work.publication_year
        paper_cell = f"{work.title} ({year})" if year is not None else work.title
        row = [paper_cell]
        for col in columns:
            note = notes_by_work_column.get((work.id, col.id))
            row.append(note.content if note is not None else "")
        writer.writerow(row)

    return buf.getvalue()


# ---------------------------------------------------------------------------
# LaTeX export
# ---------------------------------------------------------------------------


def export_as_latex(
    schema: ExtractionSchema,
    columns: list[ExtractionColumn],
    works: list[Work],
    notes_by_work_column: dict[tuple[int, int], WorkNote],
) -> str:
    """Return a complete LaTeX ``tabular`` environment for *schema*.

    Uses the ``booktabs`` package (``\\toprule``, ``\\midrule``, ``\\bottomrule``).
    Column headers for constrained columns (those with ``allowed_values``) are
    rotated 90° with ``\\rotatebox`` to save horizontal space.

    Args:
        schema: The extraction schema — its title becomes the table caption.
        columns: Ordered list of :class:`ExtractionColumn` objects.
        works: List of :class:`Work` objects — one row per work.
        notes_by_work_column: Mapping of ``(work_id, column_id)`` → :class:`WorkNote`.

    Returns:
        A complete ``\\begin{table}…\\end{table}`` block as a string.
    """
    # Build column spec: 'l' for Paper column, 'c' for constrained, 'l' for free-text
    def _col_align(col: ExtractionColumn) -> str:
        return 'c' if (col.allowed_values and len(col.allowed_values) > 0) else 'l'

    col_spec = 'l' + ''.join(_col_align(col) for col in columns)
    label = _slugify(schema.title)
    escaped_title = _escape_latex(schema.title)

    lines: list[str] = [
        r'\begin{table}[ht]',
        r'\centering',
        f'\\caption{{{escaped_title}}}',
        f'\\label{{tab:{label}}}',
        f'\\begin{{tabular}}{{{col_spec}}}',
        r'\toprule',
    ]

    # Header row — rotate constrained column names
    def _format_header(col: ExtractionColumn) -> str:
        name = _escape_latex(col.name)
        if col.allowed_values and len(col.allowed_values) > 0:
            return f'\\rotatebox{{90}}{{{name}}}'
        return name

    header_cells = ['Paper'] + [_format_header(col) for col in columns]
    lines.append(' & '.join(header_cells) + r' \\')
    lines.append(r'\midrule')

    # Data rows
    for work in works:
        year = work.publication_year
        paper_cell = (
            f'{_escape_latex(work.title)} ({year})'
            if year is not None
            else _escape_latex(work.title)
        )
        cells = [paper_cell]
        for col in columns:
            note = notes_by_work_column.get((work.id, col.id))
            cells.append(_escape_latex(note.content) if note is not None else '')
        lines.append(' & '.join(cells) + r' \\')

    lines += [
        r'\bottomrule',
        r'\end{tabular}',
        r'\end{table}',
    ]

    return '\n'.join(lines) + '\n'
