"""Lead scoring — turn the enrichment signals into a single 0-100 "good lead"
score: how likely this company is to be a good customer for our AEs (sales fit).

THE ML SEAM. Everything else calls `score_lead(features)` and treats it as a
black box. Today the body is a set of hand-written rules; once we have enough
labelled data, swap that body for a trained model WITHOUT touching any caller.

`lead_score` is about SALES FIT only. How sure we are about the website/LinkedIn
data is a SEPARATE number (`confidence_score`) and is NOT part of this score.

How the rules work
------------------
Each KEY financial signal scores `PASS_ALONE` (50) when it hits its minimum — so
ANY ONE of these, on its own, clears the default 50 bar:

    turnover >= £1m   |   cash >= £1m   |   employees >= 8   |
    trade debtors >= £500k   |   trade creditors >= £500k   |   |FX| > £100k

Below a minimum a signal scores proportionally less, and all signals are ADDED
UP (then clamped to 100). So two "half-strength" signals still pass (e.g. turnover
£500k + cash £200k), while genuinely weak ones don't (e.g. 4 staff + £20k cash).
The account-type category is only a small nudge and never caps a company.
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


# A signal at its minimum scores this — enough to clear the default 50 bar alone.
PASS_ALONE = 50

# The minimums (the levels that score PASS_ALONE). Easy to tune.
TURNOVER_MIN = 1_000_000
CASH_MIN = 1_000_000
EMPLOYEES_MIN = 8                  # 8+ counts (your "10" with a little headroom)
BALANCE_MIN = 500_000              # trade debtors / creditors
FX_MIN = 100_000                   # |foreign exchange|
SEGMENT_TURNOVER_CAP = 30_000_000  # over this is out of the SMB sweet spot (kept, scored low)


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


def _cash_points(cash):
    """Cash at bank — liquidity / banking fit. >= £1m passes on its own."""
    if cash is None:
        return 0
    if cash >= CASH_MIN:
        return PASS_ALONE
    if cash >= 500_000:
        return 38
    if cash >= 200_000:
        return 25
    if cash >= 100_000:
        return 15
    if cash >= 25_000:
        return 8
    if cash >= 1_000:
        return 3
    return 0


def _turnover_points(turnover):
    """Segment fit. >= £1m passes on its own; over £30m is kept but scored low.
    Turnover often isn't filed — that just means 0 here, and the lead leans on its
    other signals (cash, employees, debtors...) instead."""
    if turnover is None:
        return 0
    if turnover > SEGMENT_TURNOVER_CAP:
        return 10
    if turnover >= TURNOVER_MIN:
        return PASS_ALONE
    if turnover >= 500_000:
        return 25
    if turnover >= 250_000:
        return 15
    if turnover >= 100_000:
        return 8
    if turnover >= 1:
        return 3
    return 0


def _employee_points(e):
    """Headcount — a strong, account-type-independent signal. 8+ employees passes
    on its own, even for a company that filed as a micro-entity."""
    if e is None:
        return 0
    if e >= EMPLOYEES_MIN:
        return PASS_ALONE
    if e >= 5:
        return 32
    if e >= 3:
        return 18
    if e >= 1:
        return 6
    return 0


def _balance_points(value):
    """Trade debtors / creditors — a proxy for trading volume. >= £500k passes."""
    if value is None:
        return 0
    if value >= BALANCE_MIN:
        return PASS_ALONE
    if value >= 250_000:
        return 28
    if value >= 100_000:
        return 14
    if value >= 1:
        return 4
    return 0


def _fx_points(import_activity, export_activity, foreign_exchange):
    """FX exposure = a need for multi-currency / FX. |FX| > £100k passes on its
    own; a smaller FX line plus import/export flags add a little."""
    pts = 0
    if foreign_exchange is not None and abs(foreign_exchange) > FX_MIN:
        pts += PASS_ALONE
    elif foreign_exchange:
        pts += 6
    if import_activity:
        pts += 3
    if export_activity:
        pts += 3
    return pts


def _account_size_points(account_type):
    """A small nudge from the account-size category — never enough to pass on its
    own, never enough to cap a company. Financials carry the weight."""
    return {"small": 12, "micro": 6, "large": 4}.get(account_tier(account_type), 0)


def _activity_points(director_change_recent):
    return 8 if director_change_recent else 0


def _heuristic_score(f: LeadFeatures) -> int:
    """Today's rule-based score — the part a trained model will replace. Add up
    every signal, clamp to 0-100. Dormant companies are disqualified."""
    if "dormant" in (f.account_type or "").lower():
        return 0
    score = (
        _cash_points(f.cash_at_bank)
        + _turnover_points(f.turnover)
        + _employee_points(f.employee_count)
        + _balance_points(f.trade_debtors)
        + _balance_points(f.trade_creditors)
        + _fx_points(f.import_activity, f.export_activity, f.foreign_exchange)
        + _account_size_points(f.account_type)
        + _activity_points(f.director_change_recent)
    )
    return max(0, min(score, 100))


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
        cash_at_bank=f.cash_at_bank if f.cash_at_bank is not None else CASH_MIN,
        turnover=f.turnover if f.turnover is not None else TURNOVER_MIN,
        foreign_exchange=f.foreign_exchange if f.foreign_exchange is not None else -(FX_MIN + 1),
        employee_count=f.employee_count if f.employee_count is not None else EMPLOYEES_MIN,
        trade_debtors=f.trade_debtors if f.trade_debtors is not None else BALANCE_MIN,
        trade_creditors=f.trade_creditors if f.trade_creditors is not None else BALANCE_MIN,
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
