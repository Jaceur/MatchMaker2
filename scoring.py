"""Lead scoring — combine enrichment signals into a single 0-100 base score.

Heuristic for now; this module is the seam where a trained model will slot in
later (just replace score_lead's body). Component weights are ordered by
importance, per the brief:

    account size (small/medium)  >  data confidence  >  trade activity  >  director changes
"""

ACCOUNT_SIZE_MAX = 40   # small / medium companies are the sweet spot
CONFIDENCE_MAX = 30     # how sure we are about the enriched data
TRADE_EACH = 10         # per direction (import, export) -> max 20
DIRECTOR_RECENT = 10    # a recent director change


def _account_size_points(account_type):
    """Map a Companies House account category to size points. Small & medium are
    the target market (max); micro and larger taper down."""
    t = (account_type or "").lower()
    if "medium" in t or "small" in t:   # incl. total-exemption-small
        return ACCOUNT_SIZE_MAX
    if "micro" in t:
        return 15
    if "full" in t or "group" in t:
        return 10
    return 0


def score_lead(confidence_score=0, account_type=None,
               import_activity=False, export_activity=False,
               director_change_recent=False):
    """A 0-100 base lead score from the enrichment signals."""
    account = _account_size_points(account_type)
    confidence = round((confidence_score or 0) / 100 * CONFIDENCE_MAX)
    trade = (TRADE_EACH if import_activity else 0) + (TRADE_EACH if export_activity else 0)
    directors = DIRECTOR_RECENT if director_change_recent else 0
    return account + confidence + trade + directors
