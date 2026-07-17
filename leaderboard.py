"""AE Leaderboard: ranks AEs by activity points.

Points are derived from the raw per-AE counters in ae_stats (written by
leads.award_activity as AEs work). The API layer (api/routers/leaderboard.py)
does the querying and ranking; this module owns only the points formula, so the
weights live in exactly one place.
"""

POINTS_PER_URL = 25            # each URL an AE adds/corrects
POINTS_PER_SAVE = 50          # each lead saved into Salesforce
POINTS_PER_SWIPE_BLOCK = 100  # awarded per full block of swipes
SWIPES_PER_BLOCK = 20


def compute_points(urls_added, leads_swiped, leads_saved):
    """Points from raw activity counts. Works on scalars or pandas Series."""
    return (
        urls_added * POINTS_PER_URL
        + leads_saved * POINTS_PER_SAVE
        + (leads_swiped // SWIPES_PER_BLOCK) * POINTS_PER_SWIPE_BLOCK
    )
