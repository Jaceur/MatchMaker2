"""Enrich leads locally — faster than the deployed app and with no Streamlit
Cloud time limit. It writes to the same Cloud SQL database, so results appear in
the web app immediately.

Usage:
    python enrich_local.py          # enrich up to 100 'sourced' leads
    python enrich_local.py 250      # enrich up to 250
    python enrich_local.py all      # enrich every 'sourced' lead

Needs .streamlit/secrets.toml locally with the same gcp_service_account,
DB_PASSWORD and CH_API_KEY the deployed app uses (already present here), and the
project's dependencies installed:  pip install -r requirements.txt
"""
import logging
import sys
import time

# Quieten Streamlit's "missing ScriptRunContext" warnings when run outside the app.
logging.getLogger("streamlit").setLevel(logging.ERROR)

from pipeline import run_pipeline  # noqa: E402  (after logging setup)


def _progress(done, total, name):
    bar_len = 30
    filled = int(bar_len * done / total) if total else bar_len
    bar = "█" * filled + "─" * (bar_len - filled)
    print(f"\r[{bar}] {done}/{total}  {(name or '')[:38]:<38}", end="", flush=True)


def main():
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "100"
    if arg == "all":
        limit = None
    elif arg.isdigit():
        limit = int(arg)
    else:
        print(f"Usage: python enrich_local.py [N | all]   (got {arg!r})")
        return

    target = "ALL sourced leads" if limit is None else f"up to {limit} leads"
    print(f"Enriching {target} (writing to Cloud SQL)...\n")

    start = time.time()
    count = run_pipeline(limit=limit, progress_callback=_progress)
    elapsed = (time.time() - start) / 60
    print(f"\n\nDone — enriched {count} leads in {elapsed:.1f} min.")


if __name__ == "__main__":
    main()
