"""Lead scoring — turn the enrichment signals into a single 0-100 "good lead"
score: how likely this company is to be a good customer for our AEs (sales fit).

THE ML SEAM. Everything else calls `score_lead(features)` and treats it as a
black box. Today the body is a set of hand-written rules; once we have enough
labelled data, swap that body for a trained model WITHOUT touching any caller.
Keep this contract stable:

    score_lead(LeadFeatures) -> int in 0..100

`lead_score` is about SALES FIT only. How sure we are about the website/LinkedIn
data is a SEPARATE number (`confidence_score`) and is deliberately NOT part of
this score.
"""
from dataclasses import dataclass
from typing import Optional, Mapping


# ---------------------------------------------------------------------------
# The scorer's inputs (the "features"). One definition, shared by the heuristic
# today and a trained model later. Anything unknown is None/False and handled
# gracefully — missing data lowers a score, it never crashes it.
# ---------------------------------------------------------------------------
@dataclass
class LeadFeatures:
    account_type: Optional[str] = None        # CH account category (micro/small/.../dormant)
    employee_count: Optional[int] = None      # from the filed accounts
    turnover: Optional[int] = None            # £, from the filed accounts (often not disclosed)
    cash_at_bank: Optional[int] = None        # £, from the filed accounts
    foreign_exchange: Optional[int] = None    # £ FX gain/loss line (signed); its presence = FX activity
    import_activity: bool = False             # HMRC UK Trade Info
    export_activity: bool = False             # HMRC UK Trade Info
    director_change_recent: bool = False      # CH filing history (~6 months)


# ---------------------------------------------------------------------------
# Tunable weights — each component's maximum. They sum to 100, so the whole
# scoring rule is auditable in one place.
# ---------------------------------------------------------------------------
CASH_MAX = 25             # cash at bank — liquidity / banking fit
FX_MAX = 20               # import/export + a real FX line in the accounts
TURNOVER_MAX = 20         # turnover in the SMB sweet spot
SUBSTANCE_MAX = 25        # a real trading business (size + employees), not a shell
ACTIVITY_MAX = 10         # signs of recent activity / change

SEGMENT_TURNOVER_CAP = 30_000_000   # AEs target SMBs under £30m; over this is out of the
                                    # sweet spot — kept and scored low, NOT eliminated


def _cash_points(cash):
    """Cash at bank. Unknown -> 0: we can't credit what we can't see (and the
    pipeline's gates won't eliminate a lead just for missing data)."""
    if cash is None:
        return 0
    if cash >= 500_000:
        return CASH_MAX
    if cash >= 100_000:
        return 18
    if cash >= 25_000:
        return 12
    if cash >= 1_000:
        return 6
    return 0


def _fx_points(import_activity, export_activity, foreign_exchange):
    """FX exposure = a need for multi-currency / FX. Import/export is always known
    (HMRC); a non-zero FX line in the accounts is extra confirmation."""
    pts = 0
    if import_activity:
        pts += 6
    if export_activity:
        pts += 6
    if foreign_exchange:                 # non-zero FX gain/loss disclosed
        pts += 8
    return min(pts, FX_MAX)


def _account_tier(account_type):
    """Bucket a Companies House account category into small / micro / large.

    The audit / total-exemption and abridged types are SMALL companies — even
    though 'total-exemption-full' contains the word 'full' — so they must be
    matched BEFORE the plain 'full' / 'group' (which mean genuinely larger full
    or group accounts). Getting this wrong badly under-scores small leads."""
    t = (account_type or "").lower()
    if "micro" in t:
        return "micro"
    if "small" in t or "medium" in t or "total-exemption" in t or "abridged" in t:
        return "small"
    if "full" in t or "group" in t:
        return "large"
    return "unknown"


def _turnover_points(turnover, account_type):
    """Segment fit. Turnover in the £1m–£30m SMB sweet spot scores the maximum.
    Over the £30m cap is out of the sweet spot — kept (not eliminated), just not
    given the bonus. When turnover isn't filed — common for small companies —
    fall back to the account-size category rather than penalising them for it."""
    if turnover is not None:
        if turnover > SEGMENT_TURNOVER_CAP:
            return 8        # too big for the segment — kept, but no sweet-spot bonus
        if turnover >= 1_000_000:
            return TURNOVER_MAX
        if turnover >= 100_000:
            return 14
        return 8
    tier = _account_tier(account_type)
    if tier == "small":
        return 16
    if tier == "micro":
        return 10
    return 6


def _substance_points(account_type, employee_count):
    """A real, trading business rather than a dormant shell: the account-size
    category plus employee headcount where the accounts give it."""
    size = {"small": 15, "micro": 7, "large": 5}.get(_account_tier(account_type), 0)
    emp = 0
    if employee_count is not None:
        if employee_count >= 10:
            emp = 10
        elif employee_count >= 3:
            emp = 6
        elif employee_count >= 1:
            emp = 2
    return min(size + emp, SUBSTANCE_MAX)


def _activity_points(director_change_recent):
    return ACTIVITY_MAX if director_change_recent else 0


def _heuristic_score(f: LeadFeatures) -> int:
    """Today's rule-based score — the part a trained model will replace."""
    t = (f.account_type or "").lower()
    # Hard disqualifier — a dormant company isn't trading. (Over-£30m companies
    # are NOT eliminated — they're rare, so we keep them and just score them low.)
    if "dormant" in t:
        return 0
    score = (
        _cash_points(f.cash_at_bank)
        + _fx_points(f.import_activity, f.export_activity, f.foreign_exchange)
        + _turnover_points(f.turnover, f.account_type)
        + _substance_points(f.account_type, f.employee_count)
        + _activity_points(f.director_change_recent)
    )
    return max(0, min(score, 100))


def score_lead(features: LeadFeatures) -> int:
    """A 0-100 "good lead" score (sales fit) from the enrichment signals.

    THE SWAP POINT: when a trained model is available, load it once and return
    its prediction here, falling back to `_heuristic_score` when it isn't. Every
    caller just gets a 0-100 number from this one function — nothing else changes.
    """
    return _heuristic_score(features)


def best_possible_score(f: LeadFeatures) -> int:
    """The highest score this lead could still reach if every not-yet-measured
    accounts figure (cash, turnover, FX, employees) turned out favourably.

    The pipeline's early "start safe" gate uses this: a lead is only binned when
    even this best case can't reach the bar. An already-known dormant company
    still scores 0 (assuming the unknowns are good can't rescue it)."""
    best = LeadFeatures(
        account_type=f.account_type,
        import_activity=f.import_activity,
        export_activity=f.export_activity,
        director_change_recent=f.director_change_recent,
        cash_at_bank=f.cash_at_bank if f.cash_at_bank is not None else 1_000_000,
        turnover=f.turnover if f.turnover is not None else 5_000_000,
        foreign_exchange=f.foreign_exchange if f.foreign_exchange is not None else -1,
        employee_count=f.employee_count if f.employee_count is not None else 50,
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
        import_activity=bool(g("import_activity")),
        export_activity=bool(g("export_activity")),
        director_change_recent=bool(g("director_change_recent")),
    )
