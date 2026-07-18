"""Personal AE dashboard: headline stats and change-password."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text, update

from database import engine
from models import ae_stats, users_table
from leaderboard import compute_points

from ..security import get_current_user, CurrentUser, verify_password, hash_password

router = APIRouter(prefix="/me", tags=["me"])


@router.get("/stats")
def stats(user: CurrentUser = Depends(get_current_user)) -> dict:
    """Headline numbers for the AE home page: pipeline size, how many are logged
    into the CRM, and the AE's leaderboard points."""
    with engine.connect() as conn:
        pipeline_count = conn.execute(text("""
            SELECT COUNT(*) FROM sales_leads
            WHERE assigned_ae_username = :u AND status = 'approved'
        """), {"u": user.username}).scalar() or 0
        # "Into CRM" = classified (has a crm_status). A bare row-existence test
        # would count every approve since labels are written at swipe (2026-07-18),
        # and a plain JOIN double-counts leads with multiple label rows.
        into_crm = conn.execute(text("""
            SELECT COUNT(*) FROM sales_leads sl
            WHERE sl.assigned_ae_username = :u AND sl.status = 'approved'
              AND EXISTS (SELECT 1 FROM ml_pipeline_analytics m
                          WHERE m.lead_id = sl.id AND m.crm_status IS NOT NULL)
        """), {"u": user.username}).scalar() or 0
        row = conn.execute(
            select(ae_stats).where(ae_stats.c.username == user.username)
        ).mappings().fetchone()
    counts = row or {"urls_added": 0, "leads_swiped": 0, "leads_saved": 0}
    points = compute_points(
        counts.get("urls_added", 0) or 0,
        counts.get("leads_swiped", 0) or 0,
        counts.get("leads_saved", 0) or 0,
    )
    return {
        "pipeline_count": pipeline_count,
        "into_crm": into_crm,
        "points": points,
        "urls_added": counts.get("urls_added", 0) or 0,
        "leads_swiped": counts.get("leads_swiped", 0) or 0,
        "leads_saved": counts.get("leads_saved", 0) or 0,
    }


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/change-password")
def change_password(body: ChangePasswordRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    if not body.new_password:
        raise HTTPException(status_code=400, detail="New password can't be blank.")
    with engine.connect() as conn:
        row = conn.execute(
            select(users_table).where(users_table.c.username == user.username)
        ).fetchone()
    if not row or not verify_password(row.password, body.current_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    with engine.begin() as conn:
        conn.execute(
            update(users_table).where(users_table.c.id == row.id)
            .values(password=hash_password(body.new_password))
        )
    return {"message": "Password updated."}
