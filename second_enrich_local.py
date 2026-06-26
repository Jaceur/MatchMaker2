"""Run the SECOND enrichment locally — parse filed accounts documents for Tier 3
leads and store the figures (employee count + balance-sheet / P&L items: turnover,
cash, FX, trade debtors/creditors, admin expenses, bank loans).

Usage:
    python second_enrich_local.py          # process all Tier 3 leads not yet done
    python second_enrich_local.py 50       # process up to 50

Needs (local only, for scanned-PDF OCR):
    pip install -r requirements.txt
    + poppler  (for pdf2image)   + tesseract  (for pytesseract)
Plus .streamlit/secrets.toml with CH_API_KEY etc. (already present).
"""
import logging
import sys
import time

logging.getLogger("streamlit").setLevel(logging.ERROR)

from second_enrichment import second_enrich_tier3  # noqa: E402  (after logging setup)


def _progress(done, total, name, data=None):
    """One summary block per lead, showing EVERY parsed figure (head count plus
    all the monetary fields — cash, FX, debtors/creditors, admin, bank loans) so
    issues are visible at a glance. '—' means the field wasn't found in the
    filing. Generic over `data`, so any new ACCOUNTS_FIELDS entry shows up here
    automatically — no need to touch this runner."""
    d = data or {}
    figures = " · ".join(
        f"{k}={'—' if v is None else format(v, ',')}" for k, v in d.items()
    ) or "no figures parsed"
    print(f"  => [{done}/{total}] {name[:32]:<32}")
    print(f"       {figures}")


def main():
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "all"
    if arg == "all":
        limit = None
    elif arg.isdigit():
        limit = int(arg)
    else:
        print(f"Usage: python second_enrich_local.py [N | all]   (got {arg!r})")
        return

    target = "all Tier 3 leads" if limit is None else f"up to {limit} Tier 3 leads"
    print(f"Second enrichment: {target} (writing to Cloud SQL)...\n")

    start = time.time()
    count = second_enrich_tier3(limit=limit, progress_callback=_progress)
    elapsed = (time.time() - start) / 60
    print(f"\n\nDone — second-enriched {count} leads in {elapsed:.1f} min.")


if __name__ == "__main__":
    main()
