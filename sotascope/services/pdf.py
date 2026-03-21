"""PDF text extraction utilities."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    """Raised when PDF text extraction fails."""


def extract_pdf_text(pdf_path: Path) -> str:
    """Extract text from a PDF file using pdfplumber.

    Handles two-column layouts common in academic PDFs: detects columns by
    looking for a word-density gap near the horizontal page centre and extracts
    each column top-to-bottom before concatenating.

    Returns the full text content (pages joined with newlines).
    Raises ExtractionError if the PDF has no text layer or cannot be read.
    Does not write any files — the caller is responsible for persistence.
    """
    import pdfplumber

    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages_text = []
            for page in pdf.pages:
                text = _extract_page_text(page)
                if text:
                    pages_text.append(text)
            full_text = "\n\n".join(pages_text)
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(str(exc)) from exc

    if not full_text.strip():
        raise ExtractionError("No text layer found — this may be a scanned PDF")

    return full_text


def _extract_page_text(page) -> str:
    """Extract text from a single page, detecting two-column layout.

    Two-column detection uses a word-density histogram on word x0 values: in a
    two-column layout there is a physical gutter between columns that produces
    a near-empty bin near the horizontal midpoint.  In single-column layouts
    word starts are distributed across the full width and no such gap appears.

    In two-column mode words are split by x-midpoint (x0 < mid_x → left,
    x0 >= mid_x → right).  Full-width elements (titles, headings) start near
    the left margin so they land in the left group and sort to the top by
    y-position, giving correct top-to-bottom reading order.
    """
    words = page.extract_words(x_tolerance=1, y_tolerance=3)
    if not words:
        return ""

    mid_x = page.width / 2
    is_two_col = _detect_two_column(words, page.width)

    logger.debug(
        "Page %.0fx%.0f — words=%d is_two_col=%s",
        page.width, page.height, len(words), is_two_col,
    )

    if not is_two_col:
        return page.extract_text(x_tolerance=1, y_tolerance=3) or ""

    left_words = [w for w in words if w["x0"] < mid_x]
    right_words = [w for w in words if w["x0"] >= mid_x]

    logger.debug("  Two-col: left=%d right=%d", len(left_words), len(right_words))

    left_text = _words_to_text(sorted(left_words, key=lambda w: (w["top"], w["x0"])))
    right_text = _words_to_text(sorted(right_words, key=lambda w: (w["top"], w["x0"])))
    return "\n\n".join(p for p in [left_text, right_text] if p.strip())


def _detect_two_column(words: list[dict], page_width: float) -> bool:
    """Return True if the page likely has a two-column layout.

    Builds a 10-point histogram of word x0 values and applies two
    complementary heuristics — either one triggering is sufficient:

    1. **Gutter gap** — the emptiest bin within ±50 pt of the page midpoint
       is near zero.  Fires when full-width elements (title, bibliography)
       don't pollute the gutter zone (e.g. scanned arXiv papers where even
       the title occupies one of the two columns).

    2. **Right-column margin spike** — a bin in [mid_x, 0.75 × page_width]
       is ≥ 2.5× the page-wide average.  The right column's left margin
       concentrates many line-starts in a single narrow band even when a
       full-width abstract has partially filled the gutter zone.
    """
    if len(words) <= 50:
        return False

    mid_x = page_width / 2
    BIN_W = 10

    bins: dict[int, int] = {}
    for w in words:
        b = int(w["x0"] / BIN_W) * BIN_W
        bins[b] = bins.get(b, 0) + 1

    expected_per_bin = len(words) * BIN_W / page_width

    # Heuristic 1: gutter gap — emptiest bin near mid_x is near zero
    gap_start = int((mid_x - 50) / BIN_W) * BIN_W
    gap_end = int((mid_x + 50) / BIN_W) * BIN_W
    min_in_zone = min(
        bins.get(b, 0)
        for b in range(gap_start, gap_end + BIN_W, BIN_W)
    )
    if min_in_zone < 0.15 * expected_per_bin:
        return True

    # Heuristic 2: right-column margin spike — a bin in the right half is
    # much denser than average (right-column line-starts cluster there)
    rhs_start = int(mid_x / BIN_W) * BIN_W
    rhs_end = int(page_width * 0.75 / BIN_W) * BIN_W
    max_in_rhs = max(
        bins.get(b, 0)
        for b in range(rhs_start, rhs_end + BIN_W, BIN_W)
    )
    return max_in_rhs >= 2.5 * expected_per_bin


def _words_to_text(words: list[dict]) -> str:
    """Reconstruct flowing text from a position-sorted list of word dicts.

    Groups words into lines using the average character height as a tolerance,
    joining words on the same line with spaces and lines with newlines.
    """
    if not words:
        return ""

    avg_height = sum(w["bottom"] - w["top"] for w in words) / len(words)
    line_gap_threshold = max(avg_height * 0.5, 2)

    lines: list[list[str]] = []
    current_line: list[str] = [words[0]["text"]]
    prev_top: float = words[0]["top"]

    for w in words[1:]:
        if w["top"] - prev_top > line_gap_threshold:
            lines.append(" ".join(current_line))
            current_line = [w["text"]]
        else:
            current_line.append(w["text"])
        prev_top = w["top"]  # always update, even when appending to current line

    if current_line:
        lines.append(" ".join(current_line))

    return "\n".join(lines)
