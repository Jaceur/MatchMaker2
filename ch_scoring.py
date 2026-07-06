"""Scoring for the CH Lead Engine.

The metric optimised is expected transaction volume × probability of
displacing the incumbent bank — NOT raw signup likelihood. So a
foreign-parented NewCo with structural cross-border flows outranks a hundred
single-director £1 companies, and passive vehicles (holding cos, property
SPVs) are deliberately down-scored despite their incorporation volume.

Every point value lives in WEIGHTS — no magic numbers anywhere else. Each
scored company keeps its full {signal: points} breakdown (stored as JSONB in
ch_scores) so weights can be tuned later without re-enriching.

Pure module: no database, no network. This is deliberately a simple additive
model, unlike the noisy-OR model in scoring.py — incorporation-time signals
are sparse booleans, not magnitudes, so addition is the honest choice here.
"""
from decimal import Decimal

WEIGHTS = {
    # --- positive signals -------------------------------------------------
    "foreign_corporate_psc":     40,   # foreign parent → structural FX flows
    "foreign_currency_capital":  35,   # share capital denominated outside GBP
    "capital_50k_plus":          30,   # paid-up capital ≥ £50k
    "capital_10k_to_50k":        20,   # paid-up capital £10k–£50k
    "target_sic":                15,   # wholesale / ecommerce / logistics / software
    "quality_serial_director":   15,   # director previously ran a real company
    "two_plus_directors":        10,
    "officer_foreign_address":   10,   # director corresponds from abroad
    "uk_corporate_psc":           5,   # UK group subsidiary — treasury likely
                                       # stays with the group's incumbent bank,
                                       # hence deliberately small
    # --- negative signals -------------------------------------------------
    "formation_agent_address":  -25,   # registered at a mass formation address
    "single_director_low_capital": -15,  # 1 director AND capital ≤ £100
    "passive_sic_only":         -20,   # holding co / property SPV codes only
    "spv_farm_director":        -10,   # director with a trail of dead SPVs
    # --- event triggers (filings stream) ----------------------------------
    "event_bonus":               50,   # SH01 fresh raise / MR01 charge
}

# Capital thresholds are GBP-only: figures in other currencies aren't compared
# (no FX table here) — they earn foreign_currency_capital instead.
CAPITAL_HIGH = Decimal(50000)
CAPITAL_MID = Decimal(10000)
CAPITAL_TRIVIAL = Decimal(100)   # the "£1 company" test for the combo penalty

TIER1_THRESHOLD = 60   # high-touch outbound
TIER2_THRESHOLD = 30   # automated sequence
                       # below 30 = Tier 3: stored but suppressed from digests


def tier_for(score):
    if score >= TIER1_THRESHOLD:
        return 1
    if score >= TIER2_THRESHOLD:
        return 2
    return 3


def score_company(signals, event_types=()):
    """signals dict → (score, tier, breakdown dict).

    `signals` keys (all optional — anything missing simply contributes 0,
    which is how "score must tolerate missing data" is guaranteed):
        disqualified            str reason | None
        foreign_corporate_psc   bool
        uk_corporate_psc        bool
        best_gbp_capital        Decimal | None   (None = capital unknown)
        has_foreign_currency_capital  bool
        target_sic              bool
        passive_sic_only        bool
        active_director_count   int | None
        officer_foreign_address bool
        quality_serial_director bool
        spv_farm_director       bool
        formation_agent_address bool

    `event_types` — event rows ('sh01_raise'/'mr01_charge') for this company;
    any event adds the bonus and forces Tier 1 regardless of base score.
    """
    breakdown = {}

    reason = signals.get("disqualified")
    if reason:
        breakdown["disqualified"] = reason
        return 0, 3, breakdown

    def add(name, condition):
        if condition:
            breakdown[name] = WEIGHTS[name]

    add("foreign_corporate_psc", signals.get("foreign_corporate_psc"))
    add("uk_corporate_psc", signals.get("uk_corporate_psc"))
    add("foreign_currency_capital", signals.get("has_foreign_currency_capital"))

    capital = signals.get("best_gbp_capital")
    if capital is not None:
        if capital >= CAPITAL_HIGH:
            add("capital_50k_plus", True)
        elif capital >= CAPITAL_MID:
            add("capital_10k_to_50k", True)

    add("target_sic", signals.get("target_sic"))
    add("passive_sic_only", signals.get("passive_sic_only"))

    directors = signals.get("active_director_count")
    add("two_plus_directors", directors is not None and directors >= 2)
    # The combo penalty needs BOTH facts known — an unknown capital figure
    # must never count against a company.
    add(
        "single_director_low_capital",
        directors == 1 and capital is not None and capital <= CAPITAL_TRIVIAL,
    )

    add("officer_foreign_address", signals.get("officer_foreign_address"))
    add("quality_serial_director", signals.get("quality_serial_director"))
    add("spv_farm_director", signals.get("spv_farm_director"))
    add("formation_agent_address", signals.get("formation_agent_address"))

    score = sum(breakdown.values())

    # Event triggers override: a fresh raise or new charge on a young company
    # is actionable NOW, whatever the incorporation-time signals said.
    event_types = list(event_types or ())
    if event_types:
        breakdown["event_bonus"] = WEIGHTS["event_bonus"]
        breakdown["events"] = event_types
        score += WEIGHTS["event_bonus"]
        return score, 1, breakdown

    return score, tier_for(score), breakdown


def top_signals(breakdown, n=3):
    """The n biggest positive contributors, for digest rows — e.g.
    ['foreign_corporate_psc +40', 'capital_50k_plus +30']."""
    positives = [
        (name, pts) for name, pts in breakdown.items()
        if isinstance(pts, int) and pts > 0
    ]
    positives.sort(key=lambda x: -x[1])
    return [f"{name} +{pts}" for name, pts in positives[:n]]
