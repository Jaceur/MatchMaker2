"""Plot the distribution of every lead's score, with a line at the current cutoff.

Reads `lead_score` for every scored lead in the database and draws a histogram.
Bars at/above the current qualification bar (the admin slider value) are
highlighted as "qualifies"; bars below it are greyed as "screened out". A dashed
line marks the cutoff itself.

    python lead_score_distribution.py

It saves a PNG (`lead_score_distribution.png`) next to this file and also opens a
window if your machine can show one.

NOTE: needs matplotlib, which is a LOCAL-only tool here (kept out of
requirements.txt so the Streamlit Cloud deploy stays lean):

    python -m pip install matplotlib

The graph reflects the scores CURRENTLY stored in the database. If you've just
changed the scoring, run `python rescore_leads.py` first so the figures are fresh.
"""
import logging
logging.getLogger("streamlit").setLevel(logging.ERROR)

import warnings
from statistics import mean, median

from sqlalchemy import select
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from database import engine
from models import sales_leads
from settings import get_qualify_bar

# Colours
PASS_COLOR = "#2e7d32"   # green  — qualifies (score >= bar)
FAIL_COLOR = "#bdbdbd"   # grey   — below the cutoff
LINE_COLOR = "#c62828"   # red    — the cutoff line

BIN_WIDTH = 5            # one bar per 5 score-points (0-5, 5-10, ... 95-100)
OUTPUT_PNG = "lead_score_distribution.png"


def load_scores():
    """Every non-empty lead_score currently in the database."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(sales_leads.c.lead_score).where(sales_leads.c.lead_score.isnot(None))
        ).fetchall()
    return [r[0] for r in rows]


def main():
    bar = get_qualify_bar()
    scores = load_scores()
    if not scores:
        print("No scored leads found — run the pipeline or rescore_leads.py first.")
        return

    total = len(scores)
    n_pass = sum(s >= bar for s in scores)
    n_fail = total - n_pass
    print(f"{total} scored leads — {n_pass} qualify (>= {bar}), {n_fail} screened out.")

    fig, ax = plt.subplots(figsize=(11, 6))
    bins = list(range(0, 100 + BIN_WIDTH, BIN_WIDTH))
    counts, edges, patches = ax.hist(scores, bins=bins, edgecolor="white", zorder=2)

    # Colour each bar by which side of the cutoff it sits on.
    for patch, left_edge in zip(patches, edges[:-1]):
        patch.set_facecolor(PASS_COLOR if left_edge >= bar else FAIL_COLOR)

    # Count label above each non-empty bar.
    for count, left_edge in zip(counts, edges[:-1]):
        if count:
            ax.text(left_edge + BIN_WIDTH / 2, count, str(int(count)),
                    ha="center", va="bottom", fontsize=8, color="#444")

    # The cutoff line.
    ax.axvline(bar, color=LINE_COLOR, linestyle="--", linewidth=2, zorder=3)
    top = max(counts)
    ax.text(bar + 0.6, top * 0.97, f"cutoff = {bar}", color=LINE_COLOR,
            fontweight="bold", va="top")

    # Labels, ticks, framing.
    ax.set_title(f"Lead score distribution — {n_pass} of {total} qualify (bar {bar})",
                 fontsize=14, fontweight="bold")
    ax.set_xlabel("Lead score  (sales fit, 0–100)")
    ax.set_ylabel("Number of leads")
    ax.set_xticks(range(0, 101, 10))
    ax.set_xlim(0, 100)
    ax.margins(y=0.12)
    ax.grid(axis="y", alpha=0.3, zorder=0)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(handles=[
        Patch(facecolor=PASS_COLOR, label=f"Qualifies (≥ {bar})"),
        Patch(facecolor=FAIL_COLOR, label=f"Below cutoff (< {bar})"),
    ], loc="upper right")

    # Small stats footnote.
    fig.text(0.99, 0.01,
             f"mean {mean(scores):.0f}   median {median(scores):.0f}",
             ha="right", va="bottom", fontsize=9, color="#888")

    fig.tight_layout()
    fig.savefig(OUTPUT_PNG, dpi=120)
    print(f"Saved {OUTPUT_PNG}")
    # Open a window if this machine can show one; if not, the saved PNG is it
    # (silence matplotlib's "non-interactive backend" notice in that case).
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*non-interactive.*")
        plt.show()


if __name__ == "__main__":
    main()
