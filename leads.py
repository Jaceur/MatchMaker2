"""Lead data operations: queries, feature engineering, and admin mutations.

These are the read/write helpers that sit on top of the sales_leads table and
are shared by the UI pages and the CLI.
"""
from datetime import datetime

import pandas as pd
from sqlalchemy import select, delete, insert, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import engine
from models import sales_leads, pipeline_archive, ae_stats
from settings import get_qualify_bar


# ==========================================
# FEATURE ENGINEERING
# ==========================================
def engineer_ml_features(current_lead):
    """Converts raw database strings into numerical features for Machine
    Learning. Shared by the swipe page (Pass) and My Pipeline (Approve)."""
    try:
        incorp_date = pd.to_datetime(current_lead['incorporation_date'])
        age_in_days = (pd.Timestamp.now() - incorp_date).days
        company_age_months = max(0, age_in_days // 30)
    except Exception:
        company_age_months = 0

    directors = current_lead.get('active_directors', '')
    if not directors or pd.isna(directors):
        director_count = 0
    else:
        director_count = len(str(directors).split(','))

    return company_age_months, director_count


def build_ml_row(lead, swiped_by, **overrides):
    """Build the fields the Pass and Approve paths share when logging to
    ml_pipeline_analytics, then layer on per-decision values via `overrides`
    (is_worth_it, crm_status, rejection_reason, dwell_time_seconds, the
    website/linkedin verdicts, corrected URLs). Keeps the three score columns
    distinct instead of triple-storing one number."""
    age_months, dir_count = engineer_ml_features(lead)
    row = {
        "lead_id": lead["id"],
        "crn": lead["crn"],
        "company_age_months": age_months,
        "director_count": dir_count,
        "website_score": lead.get("website_score") or 0,
        "linkedin_score": lead.get("linkedin_score") or 0,
        "overall_score": lead.get("confidence_score") or 0,
        # Learning-to-rank: snapshot the candidate sets the AE chose from.
        "website_candidates": lead.get("website_candidates"),
        "linkedin_candidates": lead.get("linkedin_candidates"),
        "swiped_by": swiped_by,
    }
    row.update(overrides)
    # The chosen URL = the AE's correction if any, else the scraped default. Read
    # after overrides so a swipe-time correction wins.
    row["website_chosen"] = row.get("corrected_website_url") or lead.get("website_url")
    row["linkedin_chosen"] = row.get("corrected_linkedin_url") or lead.get("linkedin_url")
    return row


# ==========================================
# DATA LOADING
# ==========================================
def get_pending_leads(ae_username):
    """Fetches this AE's unprocessed leads, best score first. Not cached: the
    swipe page holds the result in a session queue (the real cache) and only
    calls this when that queue is empty."""
    bar = get_qualify_bar()
    with engine.connect() as conn:
        query = (
            select(sales_leads)
            .where(
                (sales_leads.c.status == 'ready_for_swipe')
                & (sales_leads.c.assigned_ae_username == ae_username)
                & (sales_leads.c.lead_score >= bar)          # the fit bar (admin slider)
            )
            # Best fit first; confidence in the web data breaks ties.
            .order_by(sales_leads.c.lead_score.desc(),
                      sales_leads.c.confidence_score.desc())
        )
        return [dict(row) for row in conn.execute(query).mappings().fetchall()]


# ==========================================
# TEAM TOP-UP ALLOCATION
# ==========================================
# Each team member should hold a swipe pile of about this many PENDING leads.
PENDING_TARGET = 20
# Lowest rank weight: the bottom of the leaderboard pulls leads at this fraction
# of the top's pull. 1.0 means "ignore the leaderboard" (everyone equal); lower
# widens the gap between the best and worst rep's average lead score. 0.78 gives
# roughly a 10-point spread top-to-bottom (e.g. best rep ~70, worst ~60).
TOPUP_W_MIN = 0.78


def release_dead_assignments(conn):
    """Strip the AE name off any lead that is no longer live and workable —
    anything not ready_for_swipe / approved / archived (e.g. a lead the pipeline
    later screened out). Such a lead must not sit in an AE's pile or count as
    'assigned'. Uses the caller's connection; returns how many were released."""
    result = conn.execute(
        update(sales_leads)
        .where(
            sales_leads.c.assigned_ae_username.isnot(None)
            & sales_leads.c.status.notin_(["ready_for_swipe", "approved", "archived"])
        )
        .values(assigned_ae_username=None, assigned_date=None)
    )
    return result.rowcount


def _ae_rank_weights():
    """Map every team member to a draft weight in [TOPUP_W_MIN .. 1.0] by their
    leaderboard standing: top of the board -> 1.0, bottom -> TOPUP_W_MIN, spaced
    evenly by rank. Admins are always treated as top-ranked (1.0)."""
    from leaderboard import compute_points  # local import keeps leads.py UI-free at load
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT u.username, u.role,
                   COALESCE(s.urls_added, 0)   AS urls_added,
                   COALESCE(s.leads_swiped, 0) AS leads_swiped,
                   COALESCE(s.leads_saved, 0)  AS leads_saved
            FROM users u LEFT JOIN ae_stats s ON s.username = u.username
        """)).mappings().fetchall()

    aes = [r for r in rows if r["role"] != "admin"]
    ranked = sorted(
        aes,
        key=lambda r: compute_points(r["urls_added"], r["leads_swiped"], r["leads_saved"]),
        reverse=True,
    )
    weights = {}
    n = len(ranked)
    for i, r in enumerate(ranked):
        frac = 1.0 if n <= 1 else 1 - i / (n - 1)      # 1.0 at the top, 0.0 at the bottom
        weights[r["username"]] = TOPUP_W_MIN + (1 - TOPUP_W_MIN) * frac
    for r in rows:
        if r["role"] == "admin":
            weights[r["username"]] = 1.0                 # admin = always top-rated
    return weights


def top_up_allocation(target=PENDING_TARGET, commit=True):
    """Top every team member back up to `target` PENDING leads from the unassigned
    qualified pool. Each person needs (target - their current pending); leads are
    drafted best-score-first, weighted by leaderboard standing so higher-ranked
    reps get higher-scoring leads on average, while everyone is still filled to
    target as far as the pool stretches. With commit=False it only projects the
    result (no writes). Returns a per-person summary (AE, Assigned, Avg Score,
    Now Pending), best-ranked first."""
    bar = get_qualify_bar()
    weights = _ae_rank_weights()
    now = datetime.utcnow()

    ctx = engine.begin() if commit else engine.connect()
    with ctx as conn:
        if commit:
            release_dead_assignments(conn)   # reclaim names off screened-out leads first

        pending = {
            r["ae"]: r["n"] for r in conn.execute(text("""
                SELECT assigned_ae_username AS ae, COUNT(*) AS n
                FROM sales_leads
                WHERE status = 'ready_for_swipe' AND assigned_ae_username IS NOT NULL
                GROUP BY assigned_ae_username
            """)).mappings().fetchall()
        }

        need = {ae: target - pending.get(ae, 0)
                for ae in weights if target - pending.get(ae, 0) > 0}
        if not need:
            return []

        pool = conn.execute(text("""
            SELECT id, lead_score FROM sales_leads
            WHERE status = 'ready_for_swipe' AND assigned_ae_username IS NULL
              AND lead_score >= :bar
            ORDER BY lead_score DESC
        """), {"bar": bar}).mappings().fetchall()

        # Weighted draft: each lead (highest score first) goes to the still-hungry
        # rep with the highest (weight - fraction already filled), so top-ranked
        # reps take the better leads while everyone fills toward target.
        assigned = {ae: [] for ae in need}
        remaining = dict(need)
        for lead in pool:
            if not remaining:
                break
            ae = max(remaining, key=lambda a: weights[a] - len(assigned[a]) / need[a])
            assigned[ae].append(lead)
            remaining[ae] -= 1
            if remaining[ae] == 0:
                del remaining[ae]

        if commit:
            for ae, leads in assigned.items():
                ids = [l["id"] for l in leads]
                if ids:
                    conn.execute(
                        update(sales_leads).where(sales_leads.c.id.in_(ids))
                        .values(assigned_ae_username=ae, assigned_date=now)
                    )

    summary = []
    for ae in sorted(assigned, key=lambda a: weights[a], reverse=True):
        scores = [l["lead_score"] for l in assigned[ae]]
        if not scores:
            continue
        summary.append({
            "AE": ae,
            "Assigned": len(scores),
            "Avg Score": round(sum(scores) / len(scores)),
            "Now Pending": pending.get(ae, 0) + len(scores),
        })
    return summary


def clear_database():
    """Wipe the working pool — sourced / ready_for_swipe / passed leads — but
    PRESERVE approved pipeline leads. Returns how many working leads were removed.
    """
    print("Clearing working pool (approved pipeline preserved)...")
    with engine.begin() as connection:
        # is_distinct_from keeps NULL-status rows in the 'delete' set too.
        result = connection.execute(
            delete(sales_leads).where(sales_leads.c.status.is_distinct_from('approved'))
        )
        print(f"SUCCESS: Removed {result.rowcount} working leads; approved pipeline kept.")
        return result.rowcount


def clear_all_data():
    print("UI Triggered: Clearing working pool...")
    wiped = clear_database()
    return f"Cleared {wiped} working leads. Approved pipeline preserved."


def clear_pipeline():
    """Snapshot approved pipeline leads into pipeline_archive, then remove them
    from the live table. Returns how many were archived + cleared."""
    print("Archiving + clearing approved pipeline...")
    snapshot_cols = [c.name for c in sales_leads.c]
    with engine.begin() as connection:
        # 1. Copy approved leads into the permanent archive.
        connection.execute(
            insert(pipeline_archive).from_select(
                snapshot_cols,
                select(sales_leads).where(sales_leads.c.status == 'approved'),
            )
        )
        # 2. Remove them from the live pipeline.
        result = connection.execute(
            delete(sales_leads).where(sales_leads.c.status == 'approved')
        )
        print(f"SUCCESS: Archived + cleared {result.rowcount} pipeline leads.")
        return result.rowcount


def clear_pipeline_data():
    print("UI Triggered: Clearing pipeline...")
    moved = clear_pipeline()
    return f"Archived + cleared {moved} pipeline leads."


# ==========================================
# AE ACTIVITY (leaderboard points)
# ==========================================
def award_activity(conn, username, urls_added=0, leads_swiped=0, leads_saved=0):
    """Increment an AE's activity counters (upsert). Takes the caller's `conn`
    so the points commit atomically with the swipe/approve/save that earned them."""
    if not (urls_added or leads_swiped or leads_saved):
        return
    stmt = pg_insert(ae_stats).values(
        username=username,
        urls_added=urls_added,
        leads_swiped=leads_swiped,
        leads_saved=leads_saved,
    ).on_conflict_do_update(
        index_elements=['username'],
        set_={
            'urls_added': ae_stats.c.urls_added + urls_added,
            'leads_swiped': ae_stats.c.leads_swiped + leads_swiped,
            'leads_saved': ae_stats.c.leads_saved + leads_saved,
        },
    )
    conn.execute(stmt)
