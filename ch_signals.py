"""Signal detection for the CH Lead Engine: raw Companies House JSON in,
plain signal facts out.

Everything in this module is a PURE function — no database, no network — so
the whole signal layer is testable against recorded fixture JSON. The points
each signal is worth live in ch_scoring.WEIGHTS, not here: this module only
answers "is the signal present?".
"""
import re
from decimal import Decimal, InvalidOperation

# ---------------------------------------------------------------------------
# Company-type / status rules (disqualifiers)
# ---------------------------------------------------------------------------

SUPPORTED_TYPES = {"ltd", "plc", "llp"}   # everything else is out of scope (v1)

# ---------------------------------------------------------------------------
# SIC code rules
# ---------------------------------------------------------------------------

# High expected banking usage: wholesale (46xxx), e-commerce retail (47910),
# warehousing/logistics (52xxx), vehicle trading (45112), web portals /
# software (62012, 62020).
TARGET_SIC_PREFIXES = ("46", "52")
TARGET_SIC_EXACT = {"47910", "45112", "62012", "62020"}

# Passive vehicles: holding companies and property SPVs. Down-scored only when
# these are the ONLY codes — a trading company that also holds property is fine.
PASSIVE_SIC = {"64205", "64209", "68100", "68209", "68320"}

# ---------------------------------------------------------------------------
# UK detection (PSC country_registered / officer address country are free text)
# ---------------------------------------------------------------------------

_UK_COUNTRY_WORDS = {
    "united kingdom", "uk", "great britain", "britain", "england", "scotland",
    "wales", "northern ireland", "england and wales", "england & wales",
    "united kingdom of great britain and northern ireland",
}


def is_uk_country(country):
    """True if a free-text country string means the UK. Unknown/blank counts
    as UK (i.e. NOT foreign) so missing data never fires the foreign signal."""
    if not country:
        return True
    return country.strip().lower() in _UK_COUNTRY_WORDS


# ---------------------------------------------------------------------------
# Address normalisation + formation-agent seed list
# ---------------------------------------------------------------------------

# Tokens that vary between tenants of the same building — drop them so every
# mailbox at a formation agent normalises to the same string. The unit's
# identifier comes AFTER these ("SUITE 4B"), so the next token goes too.
_UNIT_TOKENS = {"UNIT", "SUITE", "FLAT", "ROOM", "OFFICE", "APARTMENT", "APT",
                "DESK"}
# Floor words have their qualifier BEFORE them ("3RD FLOOR", "GROUND FLOOR"),
# so these drop alone; the ordinal/qualifier is dropped separately.
_FLOOR_TOKENS = {"FLOOR", "LEVEL", "MEZZANINE", "GROUND", "BASEMENT"}
# Ordinal floors ("3RD FLOOR") — drop the ordinal too.
_ORDINAL_RE = re.compile(r"^\d+(ST|ND|RD|TH)$")


def normalise_address(address):
    """Deterministic, deliberately dumb address key: uppercase, punctuation
    stripped, whitespace collapsed, unit/suite/floor tokens dropped.

    Accepts either a CH registered_office_address dict or a plain string.
    """
    if address is None:
        return ""
    if isinstance(address, dict):
        parts = [
            address.get("premises"), address.get("address_line_1"),
            address.get("address_line_2"), address.get("locality"),
            address.get("region"), address.get("postal_code"),
        ]
        address = " ".join(str(p) for p in parts if p)

    text = re.sub(r"[^A-Z0-9 ]+", " ", str(address).upper())
    tokens = text.split()

    kept, skip_next = [], False
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if tok in _UNIT_TOKENS:
            skip_next = True       # drop "SUITE" and the "4B" after it
            continue
        if tok in _FLOOR_TOKENS:
            continue               # drop "FLOOR" but keep what follows it
        if _ORDINAL_RE.match(tok):
            continue               # drop the "3RD" of "3RD FLOOR"
        kept.append(tok)
    return " ".join(kept)


# Notorious formation-agent / registered-office-service addresses, used until
# the bulk-snapshot hot_addresses table is built (and merged with it after).
# Normalised through the same function at import, so matching stays honest.
SEED_HOT_ADDRESSES = frozenset(normalise_address(a) for a in [
    "71-75 Shelton Street, Covent Garden, London, WC2H 9JQ",
    "20-22 Wenlock Road, London, N1 7GU",
    "128 City Road, London, EC1V 2NX",
    "124 City Road, London, EC1V 2NX",
    "Kemp House, 152-160 City Road, London, EC1V 2NX",
    "27 Old Gloucester Street, London, WC1N 3AX",
    "85 Great Portland Street, London, W1W 7LT",
    "167-169 Great Portland Street, London, W1W 5PF",
    "International House, 24 Holborn Viaduct, London, EC1A 2BN",
    "International House, 36-38 Cornhill, London, EC3V 3NG",
    "2 Frederick Street, Kings Cross, London, WC1X 0ND",
    "63-66 Hatton Garden, London, EC1N 8LE",
    "86-90 Paul Street, London, EC2A 4NE",
    "7 Bell Yard, London, WC2A 2JR",
    "483 Green Lanes, London, N13 4BS",
    "590 Kingston Road, London, SW20 8DN",
    "82 James Carter Road, Mildenhall, IP28 7DE",
    "3 Gower Street, London, WC1E 6HA",
    "41 Devonshire Street, London, W1G 7AJ",
    "1 Canada Square, Canary Wharf, London, E14 5AA",
])


# ---------------------------------------------------------------------------
# Capital parsing
# ---------------------------------------------------------------------------
# Statement of capital at incorporation is NOT a clean REST field. Order of
# attack (per the data gotchas): (a) description_values.capital on the filing
# item itself; (b) the same field on the item's associated_filings; (c) give
# up and leave capital signals null — a missing figure contributes 0 points,
# never negative. (Parsing the NEWINC document via the Document API is a
# possible future (b2) but is deliberately not attempted in v1.)

def _parse_figure(figure):
    """CH capital figures are strings like '1,000' or '50000.00'."""
    if figure is None:
        return None
    try:
        return Decimal(str(figure).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _capital_entries(description_values):
    """The [{currency, figure}] list from one filing's description_values."""
    out = []
    for entry in (description_values or {}).get("capital", []) or []:
        figure = _parse_figure(entry.get("figure"))
        currency = (entry.get("currency") or "").upper() or None
        if figure is not None:
            out.append({"currency": currency, "figure": figure})
    return out


def extract_capital_statements(filing_history_json):
    """Every capital statement found in a filing-history response.

    Returns [{filing_type, currency, figure, filing_date}] — one row per
    currency entry, since a single statement of capital can list several.
    """
    statements = []
    for item in (filing_history_json or {}).get("items", []) or []:
        filing_type = item.get("type") or item.get("category") or "UNKNOWN"
        filing_date = item.get("date")
        found = _capital_entries(item.get("description_values"))
        # Fallback: incorporation bundles hide the statement of capital in
        # associated_filings.
        if not found:
            for assoc in item.get("associated_filings", []) or []:
                found.extend(_capital_entries(assoc.get("description_values")))
                if found and not filing_date:
                    filing_date = assoc.get("date")
        for entry in found:
            statements.append({
                "filing_type": filing_type,
                "currency": entry["currency"],
                "figure": entry["figure"],
                "filing_date": filing_date,
            })
    return statements


def summarise_capital(statements):
    """Reduce capital rows to the two facts scoring needs: the largest GBP
    figure (None if we never saw one) and whether any non-GBP capital exists."""
    best_gbp = None
    has_foreign_currency = False
    for s in statements:
        if s["figure"] is None:
            continue
        if s["currency"] and s["currency"] != "GBP":
            has_foreign_currency = True
        elif s["currency"] == "GBP":
            if best_gbp is None or s["figure"] > best_gbp:
                best_gbp = s["figure"]
    return best_gbp, has_foreign_currency


# ---------------------------------------------------------------------------
# Officer / PSC / SIC signal detection
# ---------------------------------------------------------------------------

def active_directors(officers_json):
    """The active (unresigned) director items from an officers response."""
    return [
        o for o in (officers_json or {}).get("items", []) or []
        if o.get("officer_role") in ("director", "corporate-director")
        and not o.get("resigned_on")
    ]


def officer_id_from_item(officer_item):
    """officer_id parsed from links.officer.appointments
    ('/officers/{id}/appointments'). None if the link is missing."""
    link = ((officer_item.get("links") or {}).get("officer") or {}).get("appointments")
    if not link:
        return None
    parts = [p for p in link.split("/") if p]
    return parts[1] if len(parts) >= 2 else None


def psc_signals(psc_json):
    """(has_foreign_corporate_psc, has_uk_corporate_psc) from a PSC response."""
    foreign = uk = False
    for item in (psc_json or {}).get("items", []) or []:
        if item.get("ceased_on"):
            continue
        if item.get("kind") != "corporate-entity-person-with-significant-control":
            continue
        country = (item.get("identification") or {}).get("country_registered")
        if not country:
            country = (item.get("address") or {}).get("country")
        if is_uk_country(country):
            uk = True
        else:
            foreign = True
    return foreign, uk


def psc_country(psc_item):
    """Best-effort country string for storing on a ch_psc row."""
    country = (psc_item.get("identification") or {}).get("country_registered")
    return country or (psc_item.get("address") or {}).get("country")


def has_foreign_correspondence_officer(officers_json):
    for o in active_directors(officers_json):
        if not is_uk_country((o.get("address") or {}).get("country")):
            return True
    return False


def sic_flags(sic_codes):
    """(is_target_sic, is_passive_only) from a company's SIC code list.
    Missing/empty SIC is neutral: (False, False)."""
    codes = [str(c).strip() for c in (sic_codes or []) if str(c).strip()]
    codes = [c for c in codes if c.lower() != "none supplied"]
    if not codes:
        return False, False
    target = any(
        c.startswith(TARGET_SIC_PREFIXES) or c in TARGET_SIC_EXACT for c in codes
    )
    passive_only = all(c in PASSIVE_SIC for c in codes)
    return target, passive_only


# ---------------------------------------------------------------------------
# Serial-director quality (pure parts — the orchestration that fetches
# appointments/profiles lives in ch_enrich, where the rate budget is managed)
# ---------------------------------------------------------------------------

SPV_FARM_MIN_APPOINTMENTS = 10
SPV_FARM_DEAD_SHARE = 0.8
_DEAD_STATUSES = {"dissolved", "converted-closed", "removed", "closed"}
QUALITY_MIN_AGE_YEARS = 3
QUALITY_ACCOUNT_TYPES = {"full", "small"}   # not micro-entity / dormant


def prior_appointments(appointments_json, current_company_number):
    """Appointment items excluding the company being scored."""
    return [
        a for a in (appointments_json or {}).get("items", []) or []
        if ((a.get("appointed_to") or {}).get("company_number")
            != current_company_number)
    ]


def is_spv_farm(prior_items):
    """≥10 prior appointments of which ≥80% are at dead companies — the
    pattern of someone stamping out disposable SPVs."""
    if len(prior_items) < SPV_FARM_MIN_APPOINTMENTS:
        return False
    dead = sum(
        1 for a in prior_items
        if (a.get("appointed_to") or {}).get("company_status") in _DEAD_STATUSES
    )
    return dead / len(prior_items) >= SPV_FARM_DEAD_SHARE


def is_quality_company(profile, as_of):
    """Does a prior company's profile prove its director has run something
    real? All of: existed ≥3 years, filed real (full/small) accounts, and is
    still active. (CH can't distinguish dissolved-via-sale from struck-off,
    so v1 only credits companies that are still alive.) `as_of` is a
    datetime.date — passed in so this stays a pure function.
    """
    if not profile:
        return False
    if profile.get("company_status") != "active":
        return False
    created = profile.get("date_of_creation")
    if not created:
        return False
    try:
        year, month, day = (int(x) for x in str(created).split("-"))
    except ValueError:
        return False
    age_days = (as_of - as_of.__class__(year, month, day)).days
    if age_days < QUALITY_MIN_AGE_YEARS * 365:
        return False
    last_accounts = ((profile.get("accounts") or {}).get("last_accounts") or {})
    return last_accounts.get("type") in QUALITY_ACCOUNT_TYPES
