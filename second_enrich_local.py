"""Run the SECOND enrichment locally — parse filed accounts documents for Tier 3
leads and store the figures (today: employee count).

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
    d = data or {}
    emp = d.get("employee_count")
    turn = d.get("turnover")
    emp_str = emp if emp is not None else "—"
    turn_str = f"{turn:,}" if turn is not None else "—"
    print(f"  => [{done}/{total}] {name[:32]:<32}  emp={emp_str}  turnover={turn_str}")


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
