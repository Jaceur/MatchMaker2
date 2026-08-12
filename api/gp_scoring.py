"""BETA high-value scoring — ranks a new incorporation by likely GROSS PROFIT.

The original high-value test asks "is this company substantial?" (capital,
corporate parent, central-London address). This one asks a different question:
**how much FX, card interchange and balance is this company likely to generate?**

Those are not the same question, and the data says they sometimes point opposite
ways. Measured over 6,055 leads / 14 days (2026-08-12):

  - Corporate ownership is 50% property SPVs, dormant companies, holding
    companies and residents'-management — structurally near-zero GP. It is an
    ANTI-signal for GP even though it reads as "substantial".
  - Median starting capital is £1 and p90 is £100, so capital is nearly always
    noise; only the top ~1% says anything.
  - The FX-heavy trading sectors run 48-69% overseas directors, and overseas
    ownership is the most direct proxy for cross-border payments there is.

So the score is built from what predicts FLOW, not size:

  FX          <- import/export, wholesale, e-commerce, agents; overseas director;
                 overseas corporate parent; trade words in the name
  Card spend  <- e-commerce and ad agencies (paid media is the biggest card
                 category in small business), tech buying SaaS
  Balances    <- real trading revenue, i.e. NOT an SPV holding an asset

Everything here is a PURE FUNCTION of the ingest event: no database, no network,
no clock. That's deliberate — scoring runs on the hot ingest path, and it has to
be trivially testable when the weights get retuned (they will).
"""
from __future__ import annotations

import math
import re

# ---------------------------------------------------------------------------
# Sector bands. SIC arrives in the STREAM EVENT itself, so everything in this
# section can be scored at phase 1, before a single Companies House REST call.
# ---------------------------------------------------------------------------

# Cross-border trade: the FX engine. Overseas-director rates in the live data:
# 46900 wholesale 69%, 46190 agents 66%, 47910 internet retail 49%.
SECTOR_FX = {
    "46900": "Wholesale",
    "46190": "Trading agent",
    "47910": "E-commerce",
    "47190": "Retail (non-specialised)",
    # Freight and travel move money across borders by definition.
    "52290": "Freight forwarding",
    "49410": "Road haulage",
    "79110": "Travel agency",
    "79120": "Tour operator",
}

# High card spend: paid media is the single biggest card category for a small
# business, and tech buys everything on subscription.
SECTOR_CARD = {
    "73110": "Advertising agency",
    "73120": "Media buying",
    "62012": "Software development",
    "62020": "IT consultancy",
    "62090": "IT services",
    "58290": "Software publishing",
    "59111": "Media production",
}

# Structurally incapable of generating card, FX or balance GP: an SPV holds an
# asset, a dormant company does nothing, a holdco's money sits still. These are
# ~17% of the current feed — 74 leads a day of AE attention returning nothing.
SECTOR_DEAD = {
    "68209": "Property letting",
    "68100": "Property dealing",
    "68320": "Property management",
    "41100": "Property development",
    "41202": "Domestic construction",
    "98000": "Residents' management",
    "99999": "Dormant",
    "74990": "Non-trading",
    "64209": "Holding company",
    "64999": "Financial intermediation",
}

# Registered-office addresses used by company formation agents at scale. NOT a
# GP signal in itself — it's a proxy for the population that registers there
# (overseas-founded e-commerce and trading), which the sector bands already
# capture. Hence a small weight: it breaks ties, it doesn't decide.
AGENT_POSTCODES = {
    "WC2H9JQ", "EC1V2NX", "EC2A4NA", "W1W5PF", "N17GU",
    "E15NF", "E10SG", "WC1N3AX", "EH39WJ", "LS252DY",
}

# Words that betray cross-border trade before any enrichment has run.
TRADE_WORDS = re.compile(
    r"\b(IMPORT|EXPORT|GLOBAL|INTERNATIONAL|TRADING|TRADE|LOGISTIC|FREIGHT|"
    r"SHIPPING|CARGO|WHOLESALE|OVERSEAS|WORLDWIDE)\w*\b", re.I)

# Company-name endings that are NOT UK forms — if the corporate PSC is called
# "… GmbH" then the parent is foreign, which means inbound funding and ongoing
# cross-border settlement. The strongest FX signal available at incorporation.
FOREIGN_SUFFIX = re.compile(
    r"(?:^|[\s,.])(GMBH|S\.?R\.?L|B\.?V|N\.?V|LLC|L\.?L\.?C|INC|S\.?A|SAS|SARL|"
    r"A/S|APS|AB|OY|OU|UAB|SIA|SP\.?\s?Z\.?\s?O\.?O|SPA|S\.?P\.?A|KFT|D\.?O\.?O|"
    r"LDA|PTE|PTY|SDN|BHD|AG|KK|PT)\.?\s*$", re.I)

UK_RESIDENCES = {
    "united kingdom", "england", "scotland", "wales", "northern ireland",
    "great britain", "uk", "gb",
}

# ---- Weights. Tunable; the shape matters more than the exact numbers. -------
W_SECTOR_FX = 30
W_SECTOR_CARD = 20
W_SECTOR_DEAD = -40      # a hard suppressant, not a nudge
W_OVERSEAS_DIRECTOR = 25
W_FOREIGN_PARENT = 15    # stacks with the corporate-owner point below
W_CORPORATE_OWNER = 10
W_TRADE_WORD = 10
W_AGENT_ADDRESS = 5
W_NOMINEE_DIRECTOR = -15

# Capital is deliberately log-scaled and capped: £1,000 scores nothing, £100,000
# alone clears the bar. A company can report £1 of share capital and still be
# well funded (one investor, one share), so a low figure must never PENALISE —
# it just says nothing. Only a genuinely large figure is evidence.
CAPITAL_FLOOR = 1_000      # at or below: zero points
CAPITAL_CEILING = 100_000  # at or above: full points
W_CAPITAL_MAX = 40

DEFAULT_THRESHOLD = 40


def _norm_postcode(value) -> str:
    return str(value or "").upper().replace(" ", "")


def _first_sic(event: dict) -> str:
    """The first SIC code, however CHStream happened to send them."""
    codes = event.get("sic_codes")
    if isinstance(codes, list):
        return str(codes[0]).strip() if codes else ""
    return str(codes or "").split(",")[0].strip()


def is_overseas(residence) -> bool:
    text = str(residence or "").strip().lower()
    return bool(text) and text not in UK_RESIDENCES


def capital_points(value) -> float:
    """Log-scaled: £1k -> 0, £10k -> 20, £100k+ -> 40.

    Linear scaling would let a £2m outlier swamp every other signal, and would
    make £5k look meaningfully better than £1k when it isn't."""
    try:
        capital = float(value)
    except (TypeError, ValueError):
        return 0.0
    if capital <= CAPITAL_FLOOR:
        return 0.0
    if capital >= CAPITAL_CEILING:
        return float(W_CAPITAL_MAX)
    span = math.log10(CAPITAL_CEILING) - math.log10(CAPITAL_FLOOR)
    return round(W_CAPITAL_MAX * (math.log10(capital) - math.log10(CAPITAL_FLOOR)) / span, 1)


def score_lead(event: dict) -> tuple[float, list[str]]:
    """(score, reasons) for one incorporation.

    Reasons are human-readable and carry their own sign ("Property SPV -40"), so
    an AE can see why a lead ranked where it did and the weights can be argued
    with from the sheet rather than from the source.
    """
    score = 0.0
    reasons: list[str] = []

    def add(points: float, label: str) -> None:
        nonlocal score
        score += points
        reasons.append(f"{label} {points:+g}")

    # --- Sector (free at phase 1) ---
    sic = _first_sic(event)
    if sic in SECTOR_DEAD:
        add(W_SECTOR_DEAD, f"{SECTOR_DEAD[sic]} (no card/FX/balance)")
    elif sic in SECTOR_FX:
        add(W_SECTOR_FX, f"{SECTOR_FX[sic]} (FX)")
    elif sic in SECTOR_CARD:
        add(W_SECTOR_CARD, f"{SECTOR_CARD[sic]} (card spend)")

    # --- Name (free at phase 1) ---
    name = str(event.get("company_name") or "")
    match = TRADE_WORDS.search(name)
    if match:
        add(W_TRADE_WORD, f"Trade name ({match.group(0).title()})")

    # --- Registered office (free at phase 1) ---
    if _norm_postcode(event.get("postcode")) in AGENT_POSTCODES:
        add(W_AGENT_ADDRESS, "Formation-agent address")

    # --- Ownership and people (phase 2 only) ---
    if is_overseas(event.get("director_residence")):
        add(W_OVERSEAS_DIRECTOR, f"Overseas director ({event.get('director_residence')})")

    if event.get("corporate_owner"):
        add(W_CORPORATE_OWNER, "Corporate owner")
        owner = str(event.get("owner_name") or "")
        if FOREIGN_SUFFIX.search(owner.strip()):
            add(W_FOREIGN_PARENT, f"Foreign parent ({owner})")

    # A director sitting on 20+ boards is a nominee or a corporate service
    # provider, not a founder — the company is administered, not run, and tends
    # to be a shell. Penalised rather than ignored.
    try:
        others = int(event.get("director_other_companies"))
    except (TypeError, ValueError):
        others = None
    if others is not None and others >= 20:
        add(W_NOMINEE_DIRECTOR, f"Nominee director ({others} companies)")

    # --- Capital: weak, log-scaled, never negative ---
    points = capital_points(event.get("starting_capital"))
    if points:
        add(points, f"Capital £{int(float(event['starting_capital'])):,}")

    return round(score, 1), reasons


def qualifies(event: dict, threshold: float = DEFAULT_THRESHOLD) -> bool:
    return score_lead(event)[0] >= threshold
