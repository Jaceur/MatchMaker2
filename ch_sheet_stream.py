"""Stream new UK incorporations from Companies House and POST each one to a
Google Apps Script web app (which writes it into a Google Sheet).

Standalone and deliberately DECOUPLED from the ch_ scoring pipeline / Supabase
(that "High Quality New Incorps" idea is parked). It reads the Companies House
/companies stream and, for each genuinely new incorporation, POSTs this JSON to
the Apps Script endpoint:

    {
        "timePulled":  "2026-07-07T10:30:00Z",
        "companyName": "<ltd name from the stream>",
        "sicCode":     "<sic code(s), comma-joined>",
        "firstName":   "<PSC first name>",
        "lastName":    "<PSC last name>"
    }

First/Last come from the company's Person with Significant Control; if the PSC
data hasn't landed yet (it often lags a brand-new incorporation), it falls back
to the first director.

Config — all via ENVIRONMENT VARIABLES (this runs headless on Railway/a VM):
  CH_STREAM_KEY        Companies House STREAMING key
  CH_API_KEY           Companies House REST key (PSC / officer lookups)
  SHEET_WEBHOOK_URL    (optional) overrides the Apps Script URL below

Run:  python ch_sheet_stream.py
"""
import json
import os
import random
import time
from datetime import datetime

import requests

import ch_client  # rate-limited CH REST client (no database dependency)
import ch_signals  # pure parsers (capital extraction) — no database dependency
from ch_client import get_secret

# The Apps Script web-app endpoint. Overridable via env var so a new deployment
# doesn't need a code change.
WEBHOOK_URL = os.environ.get(
    "SHEET_WEBHOOK_URL",
    "https://script.google.com/macros/s/"
    "AKfycbx5rXEvRKOUY_VpVxnkgybvfqZBbzJExVvIRCR1qxIa63svlAamBFJjetJl5veIRweYsA/exec",
)

STREAM_URL = "https://stream.companieshouse.gov.uk/companies"
NEW_INCORP_MAX_AGE_DAYS = 10          # ignore stream updates for older companies
READ_TIMEOUT = 120
BACKOFF_MAX = 300


# ---------------------------------------------------------------------------
# Person name (PSC, falling back to the first director)
# ---------------------------------------------------------------------------

def _split_name(full):
    """First/last from a CH officer name ('SURNAME, Forename'). Rough is fine."""
    if not full:
        return "", ""
    full = full.strip()
    if "," in full:
        last, rest = full.split(",", 1)
        first = rest.strip().split()[0] if rest.strip() else ""
        return first.title(), last.strip().title()
    parts = full.split()
    if len(parts) == 1:
        return parts[0].title(), ""
    return parts[0].title(), parts[-1].title()


def _name_from_elements(item):
    """(first, last) from a CH name_elements block, or None if absent."""
    ne = item.get("name_elements") or {}
    if ne.get("forename") or ne.get("surname"):
        return (ne.get("forename") or "").title(), (ne.get("surname") or "").title()
    return None


def _dob_str(item):
    """CH only publishes a person's birth month + year (not the day)."""
    dob = item.get("date_of_birth") or {}
    month, year = dob.get("month"), dob.get("year")
    if month and year:
        return f"{int(month):02d}/{year}"
    return ""


def _person_from(item):
    first, last = _name_from_elements(item) or _split_name(item.get("name"))
    return {"first": first, "last": last,
            "residence": item.get("country_of_residence") or "",
            "dob": _dob_str(item)}


def person_details(company_number):
    """First/last, country of residence and birth month/year of the PSC —
    falling back to the first active director when the PSC list hasn't populated
    yet (common for a brand-new company). One REST call, sometimes two."""
    try:
        psc = ch_client.persons_with_significant_control(company_number)
    except Exception:
        psc = None
    for item in (psc or {}).get("items", []) or []:
        if item.get("ceased_on"):
            continue
        if (item.get("kind") or "").startswith("individual"):
            return _person_from(item)

    try:
        officers = ch_client.officers(company_number)
    except Exception:
        officers = None
    for o in (officers or {}).get("items", []) or []:
        if o.get("officer_role") == "director" and not o.get("resigned_on"):
            return _person_from(o)
    return {"first": "", "last": "", "residence": "", "dob": ""}


def starting_capital(company_number):
    """The company's paid-up share capital from its incorporation filing — one
    REST call. Returns a number (GBP figure if present, else the largest figure
    of any currency) or '' when no capital statement is available."""
    try:
        fh = ch_client.filing_history(company_number)
    except Exception:
        return ""
    statements = ch_signals.extract_capital_statements(fh)
    best_gbp, _ = ch_signals.summarise_capital(statements)
    if best_gbp is not None:
        return int(best_gbp)
    figures = [s["figure"] for s in statements if s["figure"] is not None]
    return int(max(figures)) if figures else ""


# ---------------------------------------------------------------------------
# POST to the Apps Script web app
# ---------------------------------------------------------------------------

def post_to_sheet(payload, retries=4):
    """POST the JSON payload to the Apps Script endpoint, with backoff. Returns
    True only when the script actually confirms success ({"status":"success"}).
    A 200 that is really Google's login page (wrong access setting / wrong URL)
    counts as a FAILURE and is logged with its body so the cause is visible."""
    last = None
    for attempt in range(retries):
        try:
            resp = requests.post(WEBHOOK_URL, json=payload, timeout=30)
            body = (resp.text or "")[:200]
            if resp.ok and "success" in body.lower():
                return True
            last = f"HTTP {resp.status_code}, body: {body!r}"
        except requests.RequestException as e:
            last = str(e)
        time.sleep(2 ** attempt)
    print(f"[sheet] POST failed: {last}", flush=True)
    return False


# ---------------------------------------------------------------------------
# The stream (self-contained — no DB, in-memory timepoint resume on reconnect)
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


def stream_events():
    """Yield /companies stream events forever, reconnecting with jittered
    backoff and resuming from the last seen timepoint within a run."""
    key = get_secret("CH_STREAM_KEY")
    if not key:
        raise RuntimeError("CH_STREAM_KEY not set.")
    last_timepoint = None
    failures = 0
    while True:
        params = {"timepoint": last_timepoint + 1} if last_timepoint else None
        try:
            with requests.get(STREAM_URL, auth=(key, ""), params=params,
                              stream=True, timeout=(10, READ_TIMEOUT)) as resp:
                if resp.status_code != 200:
                    raise RuntimeError(f"stream HTTP {resp.status_code}")
                failures = 0
                print("[sheet] stream connected", flush=True)
                for line in resp.iter_lines():
                    if not line:
                        continue
                    event = json.loads(line)
                    tp = (event.get("event") or {}).get("timepoint")
                    if tp is not None:
                        last_timepoint = tp
                    yield event
        except Exception as e:
            failures += 1
            wait = min(BACKOFF_MAX, (2 ** failures) + random.uniform(0, 3))
            print(f"[sheet] disconnected ({e}); reconnecting in {wait:.0f}s",
                  flush=True)
            time.sleep(wait)


def main():
    print(f"[sheet] posting new incorporations to {WEBHOOK_URL[:60]}...",
          flush=True)
    seen = set()
    for event in stream_events():
        data = event.get("data") or {}
        number = data.get("company_number")
        if not number or number in seen or not _is_new_incorporation(data):
            continue
        seen.add(number)

        company = data.get("company_name", "") or ""
        print(f"[sheet] new incorp {company} ({number}) — posting...", flush=True)
        person = person_details(number)
        payload = {
            "timePulled": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            # camelCase keys to match what the Apps Script reads.
            "companyName": company,
            "sicCode": ", ".join(data.get("sic_codes") or []),
            "startingCapital": starting_capital(number),
            "firstName": person["first"],
            "lastName": person["last"],
            "residence": person["residence"],
            "doB": person["dob"],
        }
        if post_to_sheet(payload):
            print(f"[sheet] posted {company} ({number})", flush=True)


if __name__ == "__main__":
    main()
