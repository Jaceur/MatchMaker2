"""Streamlit-free reimplementations of the AE actions that used to live inside
the page files (swipe_page.pass/approve, ae_dashboard.classify_lead).

Each is a single DB transaction that mirrors the original behaviour exactly:
update the live lead, write the ML training row, award leaderboard points. The
routers call these; the Streamlit app keeps its own copies until it's retired.
"""
from fastapi import HTTPException, status
from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import engine
from models import (
    sales_leads,
    ml_pipeline_analytics,
    director_emails as director_emails_table,
)
from leads import build_ml_row, award_activity


def get_lead_for_ae(lead_id: int, username: str) -> dict:
    """Fetch one lead and confirm it belongs to this AE. 404 if missing, 403 if
    it's assigned to someone else — an AE can only act on their own pile."""
    with engine.connect() as conn:
        row = conn.execute(
            select(sales_leads).where(sales_leads.c.id == lead_id)
        ).mappings().fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
    lead = dict(row)
    if lead.get("assigned_ae_username") != username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This lead is not assigned to you",
        )
    return lead


def _corrected_columns(corrected_website_url, corrected_linkedin_url) -> dict:
    """Only write a corrected-URL column when the AE actually supplied one, so we
    never null out an existing correction."""
    out = {}
    if corrected_website_url:
        out["corrected_website_url"] = corrected_website_url.strip()
    if corrected_linkedin_url:
        out["corrected_linkedin_url"] = corrected_linkedin_url.strip()
    return out


def pass_lead(lead_id: int, username: str, req) -> None:
    """Archive a lead the AE passed on, log the ML row, award points. Mirrors
    swipe_page.pass_control."""
    lead = get_lead_for_ae(lead_id, username)
    corrected = _corrected_columns(req.corrected_website_url, req.corrected_linkedin_url)
    with engine.begin() as conn:
        conn.execute(
            update(sales_leads).where(sales_leads.c.id == lead_id).values(
                status="archived",
                rejection_reason=req.rejection_reason,
                **corrected,
            )
        )
        conn.execute(
            pg_insert(ml_pipeline_analytics).values(**build_ml_row(
                lead, username,
                website_valid=req.website_valid, linkedin_valid=req.linkedin_valid,
                corrected_website_url=(req.corrected_website_url or lead.get("corrected_website_url")),
                corrected_linkedin_url=(req.corrected_linkedin_url or lead.get("corrected_linkedin_url")),
                is_worth_it=False, rejection_reason=req.rejection_reason,
                dwell_time_seconds=req.dwell_time_seconds,
            ))
        )
        award_activity(conn, username, urls_added=len(corrected), leads_swiped=1)


def approve_lead(lead_id: int, username: str, req) -> str:
    """Mark a lead approved and stash the AE's source-validation verdicts. Mirrors
    swipe_page's Approve button. Returns the lead's crn so the caller can kick off
    post-approval director enrichment."""
    lead = get_lead_for_ae(lead_id, username)
    corrected = _corrected_columns(req.corrected_website_url, req.corrected_linkedin_url)
    with engine.begin() as conn:
        conn.execute(
            update(sales_leads).where(sales_leads.c.id == lead_id).values(
                status="approved",
                website_accurate=req.website_valid,
                linkedin_accurate=req.linkedin_valid,
                # Parked here until classify writes the ML row (no ML row exists
                # yet at approve time — classify is where the label lands).
                approve_dwell_seconds=req.dwell_time_seconds,
                **corrected,
            )
        )
        award_activity(conn, username, urls_added=len(corrected), leads_swiped=1)
    return lead["crn"]


def classify_lead(lead_id: int, username: str, crm_status: str, email_verdicts) -> None:
    """Commit a CRM-status decision: ML row, Won flag, director-email verdicts,
    points. Mirrors ae_dashboard.classify_lead."""
    lead = get_lead_for_ae(lead_id, username)
    email_rows = [
        {
            "lead_id": lead_id,
            "crn": lead["crn"],
            "director_name": v.director_name,
            "pattern": v.pattern,
            "email": v.email,
            "selected": bool(v.selected),
            "swiped_by": username,
        }
        for v in (email_verdicts or [])
    ]
    with engine.begin() as conn:
        conn.execute(
            pg_insert(ml_pipeline_analytics).values(**build_ml_row(
                lead, username,
                website_valid=lead.get("website_accurate"),
                linkedin_valid=lead.get("linkedin_accurate"),
                corrected_website_url=lead.get("corrected_website_url"),
                corrected_linkedin_url=lead.get("corrected_linkedin_url"),
                is_worth_it=True, crm_status=crm_status,
                # The swipe-time dwell, parked on the lead by approve_lead.
                dwell_time_seconds=lead.get("approve_dwell_seconds"),
            ))
        )
        # "Won" was retired (GDPR): it's now "Existing Account - Already Claimed",
        # so classify no longer flags is_nabd. The column stays for old rows.
        if email_rows:
            conn.execute(insert(director_emails_table), email_rows)
        award_activity(conn, username, leads_saved=1)
