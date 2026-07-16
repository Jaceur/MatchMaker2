"""SIC code reference data + loader.

SIC (Standard Industrial Classification) codes are what Companies House uses to
describe a company's nature of business. The full condensed SIC 2007 list lives
in `data/uk_sic_codes.csv` (728 codes), which also carries **our own business
grouping** per code in `Section_Description` — e.g. "Software/Data",
"Used Car Sales", "Restaurants/Pubs/Food Service". That grouping is what the
analytics board rolls approvals up by; it is deliberately NOT the official SIC
section letter, because the business thinks in finer/different categories.

To change the data: edit the CSV and re-run the loader (`python sic_data.py`, or
the Streamlit app's boot hook). `load_sic_lookup()` is a full REPLACE — codes
that vanish from the CSV are deleted from the table.

Two Companies House codes are NOT in the official SIC list but appear on real
company records, so they're added back in CH_EXTRA_CODES below.
"""
import csv
import functools
import os

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import engine
from models import sic_lookup

# Streamlit is optional: the web app has it, the FastAPI backend and the
# headless workers import this module without it (same pattern as database.py).
try:
    import streamlit as st
except ImportError:
    st = None

CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "uk_sic_codes.csv")

# Companies House issues these three outside the official SIC 2007 list, so the
# CSV can't be the whole story. They are NOT rare: at the 2026-07-16 load, 74990
# alone was on 111 leads. code -> (description, our grouping).
CH_EXTRA_CODES = {
    "74990": ("Non-trading company", "Other"),
    "99999": ("Dormant company", "Other"),
    "98000": ("Residents property management", "Real estate activities"),
}


def _normalise_code(raw):
    """Companies House always reports 5-digit, zero-padded SIC codes ("01110"),
    but the source CSV drops the leading zero on the low ones ("1110"). Pad so a
    lead's code matches the table — without this every code below 10000
    (agriculture, mining) would silently fail to resolve."""
    code = str(raw).strip()
    return code.zfill(5) if code.isdigit() and len(code) < 5 else code


def read_sic_csv(path=CSV_PATH):
    """The CSV as {code: (description, section)}, codes normalised to 5 digits.
    Raises if the file is missing — a silent empty load would wipe the table."""
    records = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            code = _normalise_code(row["SIC_Code"])
            if not code:
                continue
            records[code] = (
                (row.get("Description") or "").strip(),
                (row.get("Section_Description") or "").strip() or None,
            )
    return records


def load_sic_lookup(path=CSV_PATH):
    """Replace the sic_lookup table with the CSV (+ the CH extras). Idempotent.
    Returns the number of codes now in the table."""
    records = {**read_sic_csv(path), **CH_EXTRA_CODES}
    rows = [
        {"code": code, "description": desc, "section": section}
        for code, (desc, section) in sorted(records.items())
    ]
    if not rows:
        raise RuntimeError(f"No SIC rows parsed from {path} — refusing to clear the table.")

    stmt = pg_insert(sic_lookup).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["code"],
        set_={"description": stmt.excluded.description, "section": stmt.excluded.section},
    )
    with engine.begin() as conn:
        conn.execute(stmt)
        # A true replace: drop codes the CSV no longer lists (the old hand-seeded
        # descriptions that aren't in the official list).
        conn.execute(delete(sic_lookup).where(sic_lookup.c.code.notin_(list(records))))
    _clear_sic_cache()
    return len(rows)


def _clear_sic_cache():
    """Drop the cached lookup so a fresh load is visible immediately. The two
    cache decorators name this differently — st.cache_data gives .clear(),
    lru_cache gives .cache_clear() — and neither has the other's method."""
    for attr in ("clear", "cache_clear"):
        clear = getattr(get_sic_records, attr, None)
        if callable(clear):
            clear()
            return


def _cache(func):
    """Streamlit's data cache inside the app (shared across reruns); a plain
    lru_cache singleton for the API/workers, where st.cache_data isn't available."""
    if st is not None:
        return st.cache_data(ttl=3600)(func)
    return functools.lru_cache(maxsize=1)(func)


@_cache
def get_sic_records():
    """code -> {"description", "section"}, read from the table and cached.
    Falls back to the CSV on the disk if the table read fails, so a lead card
    never renders bare codes just because the DB hiccuped."""
    try:
        with engine.connect() as conn:
            rows = conn.execute(select(sic_lookup)).mappings().fetchall()
        if rows:
            return {
                r["code"]: {"description": r["description"], "section": r["section"]}
                for r in rows
            }
    except Exception:
        pass
    try:
        records = {**read_sic_csv(), **CH_EXTRA_CODES}
        return {c: {"description": d, "section": s} for c, (d, s) in records.items()}
    except Exception:
        return {}


def parse_sic_codes(sic_codes):
    """The stored comma-separated `sales_leads.sic_codes` string -> a clean list
    of codes exactly as Companies House reported them. Tolerates None, spaces,
    and trailing commas.

    Deliberately does NOT zero-pad, unlike the CSV reader. CH always sends
    5-digit SIC 2007 codes (verified against the live pool: every lead code is
    5 digits except two legacy stragglers), so a 4-digit code here is a retired
    SIC 2003 one — e.g. 7414. Padding it would invent a code that doesn't exist,
    and could actively mislead: SIC 2003 '1110' is crude petroleum extraction,
    but padded to '01110' it would read as "Growing of cereals". Better to show
    the real code with no description than a confident wrong one."""
    if not sic_codes:
        return []
    return [c.strip() for c in str(sic_codes).split(",") if c.strip()]


def describe_sic_codes(sic_codes):
    """A lead's sic_codes string -> [{code, description, section}], in the order
    Companies House lists them (first = primary). Unknown codes still come back,
    with a null description, so the card can show the bare code rather than drop
    it."""
    records = get_sic_records()
    out = []
    for code in parse_sic_codes(sic_codes):
        rec = records.get(code)
        out.append({
            "code": code,
            "description": rec["description"] if rec else None,
            "section": rec["section"] if rec else None,
        })
    return out


def with_sic_detail(leads):
    """Attach `sic_detail` to a lead dict (or a list of them) for the API, so the
    frontend renders "01110 — Growing of cereals…" without its own lookup table.
    Mutates and returns the input."""
    if isinstance(leads, dict):
        leads["sic_detail"] = describe_sic_codes(leads.get("sic_codes"))
        return leads
    for lead in leads:
        lead["sic_detail"] = describe_sic_codes(lead.get("sic_codes"))
    return leads


if __name__ == "__main__":
    print(f"Loading SIC codes from {CSV_PATH} …")
    print(f"Done — {load_sic_lookup()} codes in sic_lookup.")
