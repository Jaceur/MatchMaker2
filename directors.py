"""Director enrichment via the Companies House officers API.

Triggered when an AE adds an approved lead to their pipeline. Keeps only the
3 youngest active directors, stored as 'First Last' names on the lead. Reuses
the same CH_API_KEY secret the sourcing pipeline already relies on.
"""
from urllib.parse import urlparse

import requests
import streamlit as st
from sqlalchemy import update

from database import engine
from models import sales_leads

# Email-format guesses we surface under each director for the AE to vet. The
# order here is the order shown in the UI.
EMAIL_PATTERNS = [
    ("first.last", "{first}.{last}@{domain}"),
    ("flast",      "{f}{last}@{domain}"),
    ("first",      "{first}@{domain}"),
    ("firstlast",  "{first}{last}@{domain}"),
    ("firstl",     "{first}{l}@{domain}"),
]

CH_OFFICERS_URL = "https://api.company-information.service.gov.uk/company/{crn}/officers"
MAX_DIRECTORS = 3
# Leadership roles we treat as "directors". LLPs (also sourced) use llp-member
# instead of director, so include both; corporate variants have no DOB and are
# filtered out anyway.
DIRECTOR_ROLES = {"director", "llp-member"}


def _format_name(raw_name: str) -> str:
    """Reduce a Companies House officer name to 'First Last'.

    CH gives officers as 'SURNAME, Forename Other Names', so
    'SMITH, John Andrew Mathews' -> 'John Smith'. Falls back to first+last token
    for the rare name without a comma.
    """
    raw_name = (raw_name or "").strip()
    if "," in raw_name:
        surname, _, forenames = raw_name.partition(",")
        forename_parts = forenames.split()
        first = forename_parts[0] if forename_parts else ""
        full = f"{first} {surname.strip()}"
    else:
        parts = raw_name.split()
        full = f"{parts[0]} {parts[-1]}" if len(parts) >= 2 else raw_name
    return full.strip().title()


def domain_from_url(url):
    """Bare domain for building emails: 'https://www.acme.co.uk/about' -> 'acme.co.uk'."""
    if not url:
        return ""
    parsed = urlparse(url if "//" in url else "https://" + url)
    netloc = parsed.netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def email_candidates(full_name, domain):
    """[(pattern, email), ...] for e.g. 'John Smith' + 'acme.co.uk'. Empty if we
    can't form a first+last name or there's no domain."""
    parts = full_name.split()
    if len(parts) < 2 or not domain:
        return []
    first = parts[0].lower()
    last = parts[-1].lower()
    return [
        (key, tmpl.format(first=first, last=last, f=first[0], l=last[0], domain=domain))
        for key, tmpl in EMAIL_PATTERNS
    ]


def fetch_top_directors(crn, limit=MAX_DIRECTORS):
    """Return up to `limit` youngest active directors as 'First Last' strings.

    Youngest = most recent date of birth. Companies House only exposes the month
    and year of birth, so that's the granularity we sort on.
    """
    try:
        resp = requests.get(
            CH_OFFICERS_URL.format(crn=crn),
            auth=(st.secrets["CH_API_KEY"], ""),
            params={"items_per_page": 100},
            timeout=15,
        )
    except Exception as e:
        print(f"CH officers request failed for {crn}: {e}")
        return []

    if resp.status_code != 200:
        print(f"CH officers error for {crn}: {resp.status_code}")
        return []

    # Active, natural-person directors only — corporate directors and resigned
    # officers are excluded (corporate directors have no date_of_birth).
    directors = [
        o for o in resp.json().get("items", [])
        if o.get("officer_role") in DIRECTOR_ROLES
        and not o.get("resigned_on")
        and o.get("date_of_birth")
    ]
    # Youngest first: newest (year, month) of birth.
    directors.sort(
        key=lambda o: (o["date_of_birth"].get("year", 0), o["date_of_birth"].get("month", 0)),
        reverse=True,
    )
    return [_format_name(o.get("name", "")) for o in directors[:limit] if o.get("name")]


def enrich_lead_directors(lead_id, crn):
    """Fetch the lead's directors and persist them on sales_leads. Marks the lead
    enriched even if none were found, so the pipeline gate doesn't get stuck on a
    quiet company or a transient API hiccup. Returns the list of names."""
    names = fetch_top_directors(crn)
    with engine.begin() as conn:
        conn.execute(
            update(sales_leads).where(sales_leads.c.id == lead_id)
            .values(
                active_directors=", ".join(names),
                directors_enriched=True,
                # Keep updated_at unchanged: enriching directors shouldn't bump the
                # lead to the top of the My-Pipeline list (which orders by
                # updated_at). Assigning the column to itself suppresses the
                # onupdate=utcnow default, so the lead keeps its place.
                updated_at=sales_leads.c.updated_at,
            )
        )
    return names
