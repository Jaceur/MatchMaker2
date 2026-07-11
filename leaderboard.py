"""AE Leaderboard: ranks AEs by activity points.

Points are derived from the raw per-AE counters in ae_stats (written by
leads.award_activity as AEs work). Tune the weights here.
"""
# Streamlit is optional: the FastAPI backend imports compute_points() from this
# module without Streamlit installed. The UI helpers below still need it.
try:
    import streamlit as st
except ImportError:
    st = None
import pandas as pd
from sqlalchemy import text


def _cache_data(**_kwargs):
    """Passthrough stand-in for st.cache_data when Streamlit isn't present."""
    def decorator(func):
        return func
    return decorator


_cache = st.cache_data if st is not None else _cache_data

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


@_cache(ttl=60)
def _get_leaderboard(_engine):
    """All AEs (admins excluded) with their raw activity counts; 0 if no activity."""
    query = text("""
        SELECT u.username AS "AE",
               COALESCE(s.urls_added, 0)   AS urls_added,
               COALESCE(s.leads_swiped, 0) AS leads_swiped,
               COALESCE(s.leads_saved, 0)  AS leads_saved
        FROM users u
        LEFT JOIN ae_stats s ON s.username = u.username
        WHERE u.role IS DISTINCT FROM 'admin'
    """)
    with _engine.connect() as conn:
        return pd.read_sql(query, conn)


def render_leaderboard(engine):
    st.title("🏆 AE Leaderboard")
    st.caption(
        f"{POINTS_PER_URL} pts per URL added · {POINTS_PER_SAVE} pts per lead into "
        f"Salesforce · {POINTS_PER_SWIPE_BLOCK} pts per {SWIPES_PER_BLOCK} leads swiped."
    )

    df = _get_leaderboard(engine)
    if df.empty:
        st.info("No AEs to rank yet.")
        if st.button("Refresh"):
            _get_leaderboard.clear()
            st.rerun()
        return

    df["Points"] = compute_points(df["urls_added"], df["leads_swiped"], df["leads_saved"])
    df = df.sort_values("Points", ascending=False).reset_index(drop=True)
    df.insert(0, "Rank", df.index + 1)

    # Highlight the current user's standing.
    me = st.session_state.get("username")
    if me in set(df["AE"]):
        my_row = df[df["AE"] == me].iloc[0]
        st.metric(f"Your rank: #{int(my_row['Rank'])}", f"{int(my_row['Points'])} pts")

    display = df.rename(columns={
        "urls_added": "URLs Added",
        "leads_swiped": "Leads Swiped",
        "leads_saved": "Into Salesforce",
    })[["Rank", "AE", "Points", "URLs Added", "Leads Swiped", "Into Salesforce"]]

    st.dataframe(
        display,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Points": st.column_config.NumberColumn("Points", format="%d 🏅"),
        },
    )

    if st.button("Refresh"):
        _get_leaderboard.clear()
        st.rerun()
