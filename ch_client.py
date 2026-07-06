"""Companies House REST client for the CH Lead Engine.

One shared client for every caller (enrichment, backfill, listeners), so the
rate limit is enforced in exactly one place. CH allows 600 requests per
5-minute window; we budget 550 to leave headroom for the main Matchmaker
pipeline, which uses the same key.

Auth is HTTP Basic with the API key as the username and an empty password —
the same CH_API_KEY the rest of the app already uses.

Smoke test (spec Phase 0):
    python ch_client.py 00000006     # prints that company's profile summary
"""
import os
import sys
import threading
import time
from collections import deque

import requests

BASE_URL = "https://api.company-information.service.gov.uk"

REQUEST_TIMEOUT = 15          # never wait forever on a stuck network call
MAX_RETRIES = 5               # per request, on 429/5xx/network errors
BACKOFF_BASE = 2.0            # seconds; doubles per retry

RATE_LIMIT_CALLS = 550        # CH allows 600 / 5 min; keep a safety margin
RATE_LIMIT_WINDOW = 300       # seconds


def get_secret(name, default=None):
    """Read a secret from .streamlit/secrets.toml (how the whole app stores
    keys), falling back to an environment variable. Lazy so importing this
    module never requires Streamlit or a secrets file (tests, cron boxes)."""
    try:
        import streamlit as st
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name, default)


class RateLimiter:
    """Sliding-window limiter: at most `max_calls` in any `window` seconds.

    Thread-safe, because the stream listeners and an enrichment run may share
    this process one day. wait() blocks until a slot is free.
    """

    def __init__(self, max_calls=RATE_LIMIT_CALLS, window=RATE_LIMIT_WINDOW):
        self.max_calls = max_calls
        self.window = window
        self._calls = deque()          # monotonic timestamps of recent calls
        self._lock = threading.Lock()

    def wait(self):
        while True:
            with self._lock:
                now = time.monotonic()
                # Forget calls that have aged out of the window.
                while self._calls and self._calls[0] <= now - self.window:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                sleep_for = self._calls[0] + self.window - now
            # Sleep OUTSIDE the lock so other threads aren't blocked.
            time.sleep(max(0.05, min(sleep_for, 5)))


# The one limiter every REST call in this subsystem goes through.
_limiter = RateLimiter()


def _get(path, params=None):
    """GET a CH REST path with auth, rate limiting and retry/backoff.

    Returns the parsed JSON dict, or None on 404 (CH's way of saying "no such
    resource / nothing here yet"). Raises after MAX_RETRIES failures so a
    caller's attempts/last_error bookkeeping can record it.
    """
    api_key = get_secret("CH_API_KEY")
    if not api_key:
        raise RuntimeError(
            "CH_API_KEY not found in .streamlit/secrets.toml or the environment."
        )

    last_error = None
    for attempt in range(MAX_RETRIES):
        _limiter.wait()
        try:
            resp = requests.get(
                BASE_URL + path,
                auth=(api_key, ""),
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as e:
            last_error = e
            time.sleep(BACKOFF_BASE * (2 ** attempt))
            continue

        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 404:
            return None
        if resp.status_code == 429:
            # We outran the server-side window — honour Retry-After if given.
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if retry_after else BACKOFF_BASE * (2 ** attempt)
            time.sleep(min(wait, 60))
            last_error = RuntimeError("429 rate limited")
            continue
        if resp.status_code >= 500:
            last_error = RuntimeError(f"CH server error {resp.status_code}")
            time.sleep(BACKOFF_BASE * (2 ** attempt))
            continue
        # 401/403 etc — retrying won't help.
        raise RuntimeError(f"CH API {resp.status_code} for {path}: {resp.text[:200]}")

    raise RuntimeError(f"CH API gave up after {MAX_RETRIES} tries for {path}: {last_error}")


# ---------------------------------------------------------------------------
# Endpoint wrappers. All return parsed JSON or None (404).
# New incorporations have few officers/PSCs/filings, so one 100-item page is
# enough — we deliberately skip pagination loops to keep the rate budget flat.
# ---------------------------------------------------------------------------

def company_profile(company_number):
    return _get(f"/company/{company_number}")


def persons_with_significant_control(company_number):
    return _get(
        f"/company/{company_number}/persons-with-significant-control",
        params={"items_per_page": 100},
    )


def officers(company_number):
    return _get(f"/company/{company_number}/officers", params={"items_per_page": 100})


def officer_appointments(officer_id):
    """Prior appointment history for the serial-director quality signal."""
    return _get(f"/officers/{officer_id}/appointments", params={"items_per_page": 50})


def filing_history(company_number, category=None):
    params = {"items_per_page": 100}
    if category:
        params["category"] = category
    return _get(f"/company/{company_number}/filing-history", params=params)


def charges(company_number):
    """Registered charges — corroborates an MR01 event from the stream."""
    return _get(f"/company/{company_number}/charges")


def advanced_search(params):
    """GET /advanced-search/companies — used by the backfill (no stream key
    needed). Caller supplies incorporated_from/to, size, start_index, etc."""
    return _get("/advanced-search/companies", params=params)


if __name__ == "__main__":
    number = sys.argv[1] if len(sys.argv) > 1 else "00000006"
    profile = company_profile(number)
    if profile is None:
        print(f"No company found for {number}")
    else:
        print(f"Company:  {profile.get('company_name')}")
        print(f"Number:   {profile.get('company_number')}")
        print(f"Created:  {profile.get('date_of_creation')}")
        print(f"Status:   {profile.get('company_status')}")
        print(f"Type:     {profile.get('type')}")
        print(f"SIC:      {', '.join(profile.get('sic_codes', []) or ['(none)'])}")
        office = profile.get("registered_office_address", {}) or {}
        print(f"Address:  {', '.join(str(v) for v in office.values())}")
