"""Lead scoring — turn the enrichment signals into a single 0-100 "good lead"
score: how likely this company is to be a good customer for our AEs (sales fit).

THE ML SEAM. Everything else calls `score_lead(features)` and treats it as a
black box. Today the body is a set of hand-written rules; once we have enough
labelled data, swap that body for a trained model WITHOUT touching any caller.

`lead_score` is about SALES FIT only. Confidence in the website/LinkedIn data is
a SEPARATE number (`confidence_score`) and is NOT part of this score.

How the rules work
------------------
Each signal is turned into a "strength" between 0 and ~0.9 by a saturating curve:
hitting its MINIMUM earns ~0.5 (so that signal ALONE lands at ~50, the default
bar), and bigger magnitudes above the minimum add only a little more (diminishing
returns — 50 staff barely beats 10). The strengths then COMBINE with diminishing
returns too: each signal closes part of the remaining gap to 100, so

    score = 100 * (1 - (1-s1)(1-s2)(1-s3)...)

That gives exactly the shape asked for: any one minimum passes; a strong lead
(several big signals) climbs toward ~99 without everyone clamping at a flat 100;
and weak leads stay well below 50. Thresholds are named constants below — easy to
tune. Dormant companies are disqualified outright.
"""
from dataclasses import dataclass
from typing import Optional, Mapping


@dataclass
class LeadFeatures:
    account_type: Optional[str] = None
    employee_count: Optional[int] = None
    turnover: Optional[int] = None
    cash_at_bank: Optional[int] = None
    foreign_exchange: Optional[int] = None     # signed FX gain/loss line
    trade_debtors: Optional[int] = None
    trade_creditors: Optional[int] = None
    import_activity: bool = False
    export_activity: bool = False
    director_change_recent: bool = False


# Minimums — the level at which a signal earns the pass (~0.5 strength = ~50 alone).
TURNOVER_MIN = 1_000_000
CASH_MIN = 1_000_000
EMPLOYEES_MIN = 10
BALANCE_MIN = 500_000              # trade debtors / creditors
FX_MIN = 100_000                   # |foreign exchange|
SEGMENT_TURNOVER_CAP = 30_000_000  # over this is out of the SMB segment (kept, scored low)

PASS = 0.50    # a signal at its minimum -> this strength -> ~50 on its own (the default bar)


def account_tier(account_type):
    """Bucket a Companies House account category into small / micro / large. The
    audit/total-exemption and abridged types are SMALL companies despite the word
    'full' in 'total-exemption-full', so match those before plain full/group."""
    t = (account_type or "").lower()
    if "micro" in t:
        return "micro"
    if "small" in t or "medium" in t or "total-exemption" in t or "abridged" in t:
        return "small"
    if "full" in t or "group" in t:
        return "large"
    return "unknown"


def _cash_strength(c):
    """Cash at bank. Hits the pass at £1m, then magnitude adds diminishing more."""
    if c is None:
        return 0.0
    if c >= 20_000_000:
        return 0.90
    if c >= 5_000_000:
        return 0.80
    if c >= CASH_MIN:
        return 0.55
    if c >= 500_000:
        return 0.30
    if c >= 200_000:
        return 0.18
    if c >= 100_000:
        return 0.10
    if c >= 25_000:
        return 0.05
    if c >= 1_000:
        return 0.02
    return 0.0


def _turnover_strength(t):
    """Turnover. Passes at £1m within the £30m segment; over £30m is kept but
    weak (out of segment). Not filed -> 0 (the lead leans on its other signals)."""
    if t is None:
        return 0.0
    if t > SEGMENT_TURNOVER_CAP:
        return 0.10
    if t >= 10_000_000:
        return 0.70
    if t >= 5_000_000:
        return 0.62
    if t >= TURNOVER_MIN:
        return PASS
    if t >= 500_000:
        return 0.30
    if t >= 250_000:
        return 0.18
    if t >= 100_000:
        return 0.10
    if t >= 1:
        return 0.04
    return 0.0


def _employee_strength(e):
    """Headcount — saturates fast: 10 passes, and 50 only edges a little higher."""
    if e is None:
        return 0.0
    if e >= 100:
        return 0.62
    if e >= 50:
        return 0.58
    if e >= 30:
        return 0.54
    if e >= 20:
        return 0.51
    if e >= EMPLOYEES_MIN:
        return PASS
    if e >= 8:
        return 0.40
    if e >= 5:
        return 0.26
    if e >= 3:
        return 0.14
    if e >= 1:
        return 0.05
    return 0.0


def _balance_strength(v):
    """Trade debtors / creditors. Passes at £500k, then diminishing magnitude."""
    if v is None:
        return 0.0
    if v >= 5_000_000:
        return 0.78
    if v >= 1_000_000:
        return 0.62
    if v >= BALANCE_MIN:
        return PASS
    if v >= 250_000:
        return 0.30
    if v >= 100_000:
        return 0.16
    if v >= 1:
        return 0.05
    return 0.0


def _fx_strength(import_activity, export_activity, foreign_exchange):
    """FX exposure. |FX| over £100k passes, and a really large FX line adds more;
    import/export flags (HMRC) add a small amount on top."""
    line = 0.0
    if foreign_exchange is not None:
        a = abs(foreign_exchange)
        if a >= 5_000_000:
            line = 0.90
        elif a >= 1_000_000:
            line = 0.70
        elif a >= 500_000:
            line = 0.58
        elif a > FX_MIN:
            line = PASS
        elif a > 0:
            line = 0.08
    flags = 0.06 * (bool(import_activity) + bool(export_activity))
    return min(line + flags, 0.92)


def _activity_strength(director_change_recent):
    return 0.08 if director_change_recent else 0.0


def _heuristic_score(f: LeadFeatures) -> int:
    """Today's rule-based score — the part a trained model will replace. Combine
    every signal's strength with diminishing returns (each closes part of the gap
    to 100) and scale to 0-100. Dormant companies are disqualified."""
    if "dormant" in (f.account_type or "").lower():
        return 0
    strengths = (
        _cash_strength(f.cash_at_bank),
        _turnover_strength(f.turnover),
        _employee_strength(f.employee_count),
        _balance_strength(f.trade_debtors),
        _balance_strength(f.trade_creditors),
        _fx_strength(f.import_activity, f.export_activity, f.foreign_exchange),
        _activity_strength(f.director_change_recent),
    )
    gap = 1.0
    for s in strengths:
        gap *= (1.0 - s)
    return round(100 * (1.0 - gap))


def score_lead(features: LeadFeatures) -> int:
    """A 0-100 "good lead" score (sales fit). THE SWAP POINT for a trained model;
    callers just get a number from this one function."""
    return _heuristic_score(features)


def best_possible_score(f: LeadFeatures) -> int:
    """Highest score this lead could still reach if every not-yet-measured accounts
    figure turned out favourably. The pipeline's early 'start safe' gate uses this:
    a lead is only binned when even this best case can't reach the bar. A known
    dormant company still scores 0."""
    best = LeadFeatures(
        account_type=f.account_type,
        import_activity=f.import_activity,
        export_activity=f.export_activity,
        director_change_recent=f.director_change_recent,
        cash_at_bank=f.cash_at_bank if f.cash_at_bank is not None else 5_000_000,
        turnover=f.turnover if f.turnover is not None else 5_000_000,
        foreign_exchange=f.foreign_exchange if f.foreign_exchange is not None else -1_000_000,
        employee_count=f.employee_count if f.employee_count is not None else 50,
        trade_debtors=f.trade_debtors if f.trade_debtors is not None else 1_000_000,
        trade_creditors=f.trade_creditors if f.trade_creditors is not None else 1_000_000,
    )
    return _heuristic_score(best)


def features_from_mapping(row: Mapping) -> LeadFeatures:
    """Build the scorer's inputs from a sales_leads row or the enrichment result
    dict (anything dict-like). Missing keys are tolerated."""
    g = row.get
    return LeadFeatures(
        account_type=g("account_type"),
        employee_count=g("employee_count"),
        turnover=g("turnover"),
        cash_at_bank=g("cash_at_bank"),
        foreign_exchange=g("foreign_exchange"),
        trade_debtors=g("trade_debtors"),
        trade_creditors=g("trade_creditors"),
        import_activity=bool(g("import_activity")),
        export_activity=bool(g("export_activity")),
        director_change_recent=bool(g("director_change_recent")),
    )
