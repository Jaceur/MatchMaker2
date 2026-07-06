"""Daily digest for the CH Lead Engine: Tier 1 (high-touch) and Tier 2
(automated sequence) companies as markdown + CSV.

Each row: name, company number, score, top 3 signals, Companies House link.
Tier 3 is stored but never surfaced. Companies on the ch_suppression list are
excluded everywhere.

Usage:
    python ch_digest.py            # last 24h of newly scored/promoted, to ./digests/
    python ch_digest.py 48         # widen the window to 48h
    python ch_digest.py all        # every current Tier 1/2 company

The page (new_incorps_page.py) calls build_digest() directly and serves the
same strings as download buttons.
"""
import csv
import io
import os
import sys
from datetime import datetime, timedelta

from sqlalchemy import select

from database import engine
from models import ch_companies, ch_scores, ch_suppression

DIGEST_DIR = "digests"
CH_LINK = "https://find-and-search.company-information.service.gov.uk/company/{}"


def fetch_digest_rows(hours=24):
    """Tier 1/2 companies scored (or event-promoted — that bumps scored_at)
    in the last `hours`, minus suppressed ones. hours=None → all current."""
    query = (
        select(
            ch_companies.c.company_number, ch_companies.c.name,
            ch_companies.c.date_of_creation, ch_companies.c.sic_codes,
            ch_scores.c.score, ch_scores.c.tier, ch_scores.c.breakdown,
            ch_scores.c.scored_at,
        )
        .select_from(ch_scores.join(
            ch_companies,
            ch_scores.c.company_number == ch_companies.c.company_number))
        .where(ch_scores.c.tier.in_([1, 2]))
        .where(ch_scores.c.company_number.notin_(
            select(ch_suppression.c.company_number)))
        .order_by(ch_scores.c.tier, ch_scores.c.score.desc())
    )
    if hours is not None:
        query = query.where(
            ch_scores.c.scored_at >= datetime.utcnow() - timedelta(hours=hours))
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(query).fetchall()]


def build_digest(hours=24):
    """(markdown_str, csv_str, row_count) for the current digest window."""
    # Imported here (not at module top) so this module stays importable in
    # environments that only want fetch rows; top_signals is pure anyway.
    from ch_scoring import top_signals

    rows = fetch_digest_rows(hours)
    window = f"last {hours}h" if hours is not None else "all current"
    today = datetime.utcnow().strftime("%Y-%m-%d")

    md = [f"# High Quality New Incorps — {today} ({window})", ""]
    for tier, title in ((1, "Tier 1 — high-touch outbound"),
                        (2, "Tier 2 — automated sequence")):
        tier_rows = [r for r in rows if r["tier"] == tier]
        md.append(f"## {title} ({len(tier_rows)})")
        md.append("")
        if not tier_rows:
            md.append("_None in this window._")
            md.append("")
            continue
        md.append("| Company | Number | Score | Top signals | CH |")
        md.append("|---|---|---|---|---|")
        for r in tier_rows:
            signals = ", ".join(top_signals(r["breakdown"] or {})) or "—"
            md.append(
                f"| {r['name'] or '?'} | {r['company_number']} | {r['score']} "
                f"| {signals} | [link]({CH_LINK.format(r['company_number'])}) |"
            )
        md.append("")
    markdown = "\n".join(md)

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["tier", "company_name", "company_number", "score",
                     "incorporated", "sic_codes", "top_signals", "ch_link"])
    for r in rows:
        writer.writerow([
            r["tier"], r["name"], r["company_number"], r["score"],
            r["date_of_creation"], r["sic_codes"],
            "; ".join(top_signals(r["breakdown"] or {})),
            CH_LINK.format(r["company_number"]),
        ])
    return markdown, buf.getvalue(), len(rows)


def write_digest_files(hours=24, out_dir=DIGEST_DIR):
    """Write digest-YYYY-MM-DD.md/.csv into out_dir. Returns the two paths."""
    markdown, csv_text, count = build_digest(hours)
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y-%m-%d")
    md_path = os.path.join(out_dir, f"digest-{stamp}.md")
    csv_path = os.path.join(out_dir, f"digest-{stamp}.csv")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown)
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        f.write(csv_text)
    print(f"{count} companies -> {md_path} + {csv_path}")
    return md_path, csv_path


if __name__ == "__main__":
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "24"
    write_digest_files(hours=None if arg == "all" else int(arg))
