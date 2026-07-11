"""AE leaderboard: activity points, ranked."""
from fastapi import APIRouter, Depends
from sqlalchemy import text

from database import engine
from leaderboard import compute_points

from ..security import get_current_user, CurrentUser

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


@router.get("")
def leaderboard(user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    """All AEs (admins excluded) ranked by points, highest first."""
    query = text("""
        SELECT u.username AS ae,
               COALESCE(s.urls_added, 0)   AS urls_added,
               COALESCE(s.leads_swiped, 0) AS leads_swiped,
               COALESCE(s.leads_saved, 0)  AS leads_saved
        FROM users u
        LEFT JOIN ae_stats s ON s.username = u.username
        WHERE u.role IS DISTINCT FROM 'admin'
    """)
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(query).mappings().fetchall()]
    for r in rows:
        r["points"] = compute_points(r["urls_added"], r["leads_swiped"], r["leads_saved"])
    rows.sort(key=lambda r: r["points"], reverse=True)
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return rows
