"""One-off script to generate synthetic, copyright-free PDF fixtures.

Run once from the repository root:

    python tests/fixtures/generate_fixtures.py

Produces:
    tests/fixtures/pdfs/two_column.pdf    — synthetic two-column academic paper
    tests/fixtures/pdfs/single_column.pdf — synthetic single-column paper

The two PDFs share the same title, authors, abstract, and figure, but differ
in body layout so that extraction tests can verify column-detection behaviour.

Dependencies (not in the main test requirements — install separately):
    fpdf2, matplotlib
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------

PDFS_DIR = Path(__file__).resolve().parent / "pdfs"

# ---------------------------------------------------------------------------
# Shared content strings
# ---------------------------------------------------------------------------

TITLE = "Synthetic Test Paper"
AUTHORS = "Alice Example, Bob Example"

ABSTRACT = (
    "ABSTRACT: This study investigates synthetic data generation for unit "
    "testing of PDF text extraction software. Lorem ipsum dolor sit amet, "
    "consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore "
    "et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud "
    "exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat "
    "duis aute irure dolor in reprehenderit in voluptate velit esse cillum "
    "dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non "
    "proident, sunt in culpa qui officia deserunt mollit anim id est laborum."
)

# Left column — three paragraphs, distinctive keywords ALPHA / BETA / GAMMA
LEFT_BODY = (
    "LEFTCOL: The ALPHA theory provides a foundational framework for "
    "understanding this domain. Lorem ipsum dolor sit amet, consectetur "
    "adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore "
    "magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation "
    "ullamco laboris nisi ut aliquip ex ea commodo consequat duis aute "
    "irure dolor in reprehenderit in voluptate velit esse cillum dolore.\n\n"
    "The BETA analysis extends prior work in significant ways. Nisi ut "
    "aliquip ex ea commodo consequat duis aute irure dolor in reprehenderit "
    "in voluptate velit esse cillum dolore eu fugiat nulla pariatur. "
    "Excepteur sint occaecat cupidatat non proident, sunt in culpa qui "
    "officia deserunt mollit anim id est laborum et perspiciatis unde "
    "omnis iste natus error sit voluptatem accusantium doloremque.\n\n"
    "Results confirm that GAMMA outperforms all prior baselines across every "
    "evaluated benchmark and dataset split. Sunt in culpa qui officia "
    "deserunt mollit anim id est laborum et perspiciatis unde omnis iste "
    "natus error sit voluptatem accusantium doloremque laudantium totam "
    "rem aperiam eaque ipsa quae ab illo inventore veritatis et quasi."
)

# Right column — three paragraphs, keywords DELTA / EPSILON / ZETA
RIGHT_BODY = (
    "RIGHTCOL: The DELTA framework unifies disparate prior approaches under "
    "a common theoretical umbrella. Lorem ipsum dolor sit amet, consectetur "
    "adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore "
    "magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation "
    "ullamco laboris nisi ut aliquip ex ea commodo consequat duis aute "
    "irure dolor in reprehenderit in voluptate velit esse cillum dolore.\n\n"
    "EPSILON evaluation across five independent benchmarks shows consistent "
    "and statistically significant gains. Quis nostrud exercitation ullamco "
    "laboris nisi ut aliquip ex ea commodo consequat duis aute irure dolor "
    "in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla "
    "pariatur. Excepteur sint occaecat cupidatat non proident, sunt in "
    "culpa qui officia deserunt mollit anim id est laborum et perspiciatis.\n\n"
    "ZETA conclusions follow directly and rigorously from the experimental "
    "evidence presented in the preceding sections of this paper. Excepteur "
    "sint occaecat cupidatat non proident, sunt in culpa qui officia "
    "deserunt mollit anim id est laborum et perspiciatis unde omnis iste "
    "natus error sit voluptatem accusantium doloremque laudantium totam."
)

# Single-column body — keywords THETA / IOTA / KAPPA, long enough that no
# 10-pt x0 bin near the page midpoint is empty (avoids false two-col detection)
SINGLE_BODY = (
    "SINGLECOL: This paper explores a single-column layout to verify that "
    "the extraction pipeline does not erroneously detect two columns. Lorem "
    "ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim "
    "veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex "
    "ea commodo consequat duis aute irure dolor in reprehenderit in "
    "voluptate velit esse cillum dolore eu fugiat nulla pariatur excepteur.\n\n"
    "The THETA principles underpin this entire investigation and inform "
    "every design decision made throughout the experimental protocol and "
    "the analysis phase. Quis nostrud exercitation ullamco laboris nisi ut "
    "aliquip ex ea commodo consequat duis aute irure dolor in reprehenderit "
    "in voluptate velit esse cillum dolore eu fugiat nulla pariatur. "
    "Excepteur sint occaecat cupidatat non proident, sunt in culpa qui "
    "officia deserunt mollit anim id est laborum et perspiciatis unde omnis "
    "iste natus error sit voluptatem accusantium doloremque laudantium.\n\n"
    "IOTA methodology applies a rigorous and reproducible experimental "
    "protocol that has been independently validated by three external "
    "reviewers with recognised domain expertise. Deserunt mollit anim id "
    "est laborum et perspiciatis unde omnis iste natus error sit voluptatem "
    "accusantium doloremque laudantium totam rem aperiam eaque ipsa quae ab "
    "illo inventore veritatis et quasi architecto beatae vitae dicta sunt "
    "explicabo. Nemo enim ipsam voluptatem quia voluptas sit aspernatur.\n\n"
    "KAPPA validation confirms reproducibility of results across all "
    "evaluated settings and datasets beyond any reasonable doubt. Lorem "
    "ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor "
    "incididunt ut labore et dolore magna aliqua ut enim ad minim veniam "
    "quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea "
    "commodo consequat duis aute irure dolor in reprehenderit voluptate.\n\n"
    "Further analysis demonstrates convergence under a wide variety of "
    "initialisation conditions and hyperparameter choices encountered in "
    "practical deployment scenarios for research and production settings. "
    "Nulla pariatur excepteur sint occaecat cupidatat non proident sunt in "
    "culpa qui officia deserunt mollit anim id est laborum et perspiciatis "
    "nemo enim ipsam voluptatem quia voluptas sit aspernatur aut odit aut "
    "fugit sed quia consequuntur magni dolores eos qui ratione sequi nesciunt."
)

CAPTION = "Figure 1: A sine wave."

# ---------------------------------------------------------------------------
# Plot helper
# ---------------------------------------------------------------------------


def _save_sine_plot(path: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    x = np.linspace(0, 2 * math.pi, 300)
    fig, ax = plt.subplots(figsize=(4, 1.6), dpi=100)
    ax.plot(x, np.sin(x), linewidth=1.5)
    ax.set_xlabel("x", fontsize=8)
    ax.set_ylabel("sin(x)", fontsize=8)
    ax.tick_params(labelsize=7)
    fig.tight_layout(pad=0.4)
    fig.savefig(path, dpi=100)
    plt.close(fig)


# ---------------------------------------------------------------------------
# PDF builders
# ---------------------------------------------------------------------------


def _build_two_column(plot_png: str) -> None:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()

    # Geometry in mm
    page_w = 210.0
    margin = 15.0
    content_w = page_w - 2 * margin  # 180 mm
    gutter = 10.0
    col_w = (content_w - gutter) / 2  # 85 mm

    # Title
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_xy(margin, 15)
    pdf.cell(content_w, 8, TITLE, align="C", new_x="LMARGIN", new_y="NEXT")

    # Authors
    pdf.set_font("Helvetica", "", 11)
    pdf.set_x(margin)
    pdf.cell(content_w, 6, AUTHORS, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # Abstract (justified, full width)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_x(margin)
    pdf.multi_cell(content_w, 4.5, ABSTRACT, align="J")
    pdf.ln(4)

    # --- Two columns ---
    col_top = pdf.get_y()
    left_x = margin
    right_x = margin + col_w + gutter

    # Left column
    pdf.set_xy(left_x, col_top)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(col_w, 4.5, LEFT_BODY, align="J")
    left_end_y = pdf.get_y()

    # Right column (same top as left)
    pdf.set_xy(right_x, col_top)
    pdf.multi_cell(col_w, 4.5, RIGHT_BODY, align="J")
    right_end_y = pdf.get_y()

    pdf.set_y(max(left_end_y, right_end_y) + 5)

    # Plot (centred)
    img_w_mm, img_h_mm = 90, 35
    pdf.image(plot_png, x=(page_w - img_w_mm) / 2, w=img_w_mm, h=img_h_mm)
    pdf.ln(2)

    # Caption (centred across full content width)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_x(margin)
    pdf.cell(content_w, 5, CAPTION, align="C")

    out = PDFS_DIR / "two_column.pdf"
    pdf.output(str(out))
    print(f"  wrote {out}")


def _build_single_column(plot_png: str) -> None:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()

    page_w = 210.0
    margin = 15.0
    content_w = page_w - 2 * margin  # 180 mm

    # Title
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_xy(margin, 15)
    pdf.cell(content_w, 8, TITLE, align="C", new_x="LMARGIN", new_y="NEXT")

    # Authors
    pdf.set_font("Helvetica", "", 11)
    pdf.set_x(margin)
    pdf.cell(content_w, 6, AUTHORS, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # Abstract (justified, full width)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_x(margin)
    pdf.multi_cell(content_w, 4.5, ABSTRACT, align="J")
    pdf.ln(4)

    # Single-column body
    pdf.set_x(margin)
    pdf.multi_cell(content_w, 4.5, SINGLE_BODY, align="J")
    pdf.ln(5)

    # Plot (centred)
    img_w_mm, img_h_mm = 90, 35
    pdf.image(plot_png, x=(page_w - img_w_mm) / 2, w=img_w_mm, h=img_h_mm)
    pdf.ln(2)

    # Caption
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_x(margin)
    pdf.cell(content_w, 5, CAPTION, align="C")

    out = PDFS_DIR / "single_column.pdf"
    pdf.output(str(out))
    print(f"  wrote {out}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    PDFS_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        plot_png = f.name

    try:
        print("Generating sine wave plot…")
        _save_sine_plot(plot_png)
        print("Building two_column.pdf…")
        _build_two_column(plot_png)
        print("Building single_column.pdf…")
        _build_single_column(plot_png)
    finally:
        Path(plot_png).unlink(missing_ok=True)

    print("Done.")


if __name__ == "__main__":
    main()
