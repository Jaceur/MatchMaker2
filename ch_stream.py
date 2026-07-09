"""Companies House Streaming API listener for the CH Lead Engine.

Long-running process (run in its own terminal / service — NOT part of the
Streamlit app and can't run on Streamlit Cloud):

    python ch_stream.py companies   # new incorporations -> ch_queue

This is the ONLY stream this project uses: real-time NEW INCORPORATIONS. The
SH01/MR01 trigger events are detected over plain REST instead (ch_enrich
.sweep_events, run by ch_run_local.py) — so there's just one streaming process
to keep alive, and no second stream key budget to worry about.

Needs CH_STREAM_KEY in .streamlit/secrets.toml (or the environment) — a
SEPARATE key from CH_API_KEY, requested in the CH developer hub under
"streaming". Without a stream key you can still run everything else: the
ch_backfill.py REST poller feeds the same queue.

The stream is one long HTTP response of newline-delimited JSON with blank
heartbeat lines. Every event carries event.timepoint; we persist the last
processed timepoint per stream (ch_stream_state) and reconnect with
?timepoint= to resume without gaps. Disconnects get jittered exponential
backoff. Events may be redelivered around a restart — every downstream write
is an upsert, so that's harmless.
"""
import json
import random
import sys
import time
from datetime import datetime

import requests
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ch_client import get_secret
from ch_enrich import queue_company
from database import engine
from models import ch_stream_state

STREAM_BASE_URL = "https://stream.companieshouse.gov.uk"

# Only treat a /companies event as a NEW incorporation if it was created in
# the last N days (the stream also emits updates for existing companies).
NEW_INCORP_MAX_AGE_DAYS = 10

TIMEPOINT_SAVE_EVERY = 25      # events between ch_stream_state writes
READ_TIMEOUT = 120             # heartbeats arrive well within this; a silent
                               # connection this long is dead — reconnect
BACKOFF_MAX = 300              # cap the reconnect backoff at 5 minutes


# ---------------------------------------------------------------------------
# Timepoint persistence
# ---------------------------------------------------------------------------

def load_timepoint(stream):
    with engine.connect() as conn:
        row = conn.execute(
            select(ch_stream_state.c.timepoint)
            .where(ch_stream_state.c.stream == stream)
        ).first()
    return row[0] if row else None


def save_timepoint(stream, timepoint):
    stmt = pg_insert(ch_stream_state).values(
        stream=stream, timepoint=timepoint, updated_at=datetime.utcnow(),
    ).on_conflict_do_update(
        index_elements=["stream"],
        set_={"timepoint": timepoint, "updated_at": datetime.utcnow()},
    )
    with engine.begin() as conn:
        conn.execute(stmt)


# ---------------------------------------------------------------------------
# The generic stream reader
# ---------------------------------------------------------------------------

def stream_events(path, stream_name):
    """Yield parsed events forever, reconnecting (with resume) on any drop."""
    stream_key = get_secret("CH_STREAM_KEY")
    if not stream_key:
        raise RuntimeError(
            "CH_STREAM_KEY not found. Request a Streaming API key in the CH "
            "developer hub and add it to .streamlit/secrets.toml — or use "
            "ch_backfill.py, which needs only the normal REST key."
        )

    failures = 0
    while True:
        timepoint = load_timepoint(stream_name)
        params = {"timepoint": timepoint + 1} if timepoint is not None else None
        try:
            with requests.get(
                STREAM_BASE_URL + path,
                auth=(stream_key, ""),
                params=params,
                stream=True,
                timeout=(10, READ_TIMEOUT),
            ) as resp:
                if resp.status_code != 200:
                    raise RuntimeError(f"stream HTTP {resp.status_code}")
                failures = 0
                print(f"[{stream_name}] connected"
                      f"{f' (resuming after timepoint {timepoint})' if timepoint else ''}")
                for line in resp.iter_lines():
                    if not line:
                        continue           # heartbeat
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue           # partial line around a drop — skip
        except Exception as e:
            failures += 1
            wait = min(BACKOFF_MAX, (2 ** failures) + random.uniform(0, 3))
            print(f"[{stream_name}] disconnected ({e}); "
                  f"reconnecting in {wait:.0f}s...")
            time.sleep(wait)


# ---------------------------------------------------------------------------
# /companies — new incorporations -> queue
# ---------------------------------------------------------------------------

def _is_new_incorporation(data):
    created = data.get("date_of_creation")
    if not created or data.get("company_status") not in (None, "active"):
        return False
    try:
        age = (datetime.utcnow().date()
               - datetime.strptime(created, "%Y-%m-%d").date()).days
    except ValueError:
        return False
    return 0 <= age <= NEW_INCORP_MAX_AGE_DAYS


def run_companies_listener():
    seen_since_save = 0
    ingested = 0
    for event in stream_events("/companies", "companies"):
        data = event.get("data") or {}
        timepoint = (event.get("event") or {}).get("timepoint")
        number = data.get("company_number")

        if number and _is_new_incorporation(data):
            with engine.begin() as conn:
                was_new = queue_company(
                    conn, number,
                    name=data.get("company_name"),
                    date_of_creation=data.get("date_of_creation"),
                )
            if was_new:
                ingested += 1
                print(f"[companies] +{data.get('company_name')} ({number}) "
                      f"— {ingested} ingested this session")

        seen_since_save += 1
        if timepoint is not None and seen_since_save >= TIMEPOINT_SAVE_EVERY:
            save_timepoint("companies", timepoint)
            seen_since_save = 0


if __name__ == "__main__":
    which = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    if which == "companies":
        run_companies_listener()
    else:
        print("Usage: python ch_stream.py companies")
