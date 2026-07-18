"""My Pipeline endpoints: classify approved leads, enrich directors, view the
classified summary. Mirrors ae_dashboard.py without the Streamlit caching."""
from fastapi import APIRouter, Depends
from sqlalchemy import text

from database import engine
from directors import enrich_lead_directors, email_candidates, domain_from_url
from sic_data import with_sic_detail

from ..schemas import ClassifyRequest
from ..security import get_current_user, CurrentUser
from ..services import get_lead_for_ae, classify_lead

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.get("/unclassified")
def unclassified(user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    """Approved leads still needing a CRM status.

    "Unclassified" means NO CRM STATUS — not "no ML row". Approves write their
    label row at swipe time now (2026-07-18), so testing for row-existence would
    make every approved lead vanish from this list the moment it was swiped."""
    query = text("""
        SELECT sl.*
        FROM sales_leads sl
        WHERE assigned_ae_username = :username
          AND status = 'approved'
          AND NOT EXISTS (
              SELECT 1 FROM ml_pipeline_analytics m
              WHERE m.lead_id = sl.id AND m.crm_status IS NOT NULL
          )
        ORDER BY updated_at DESC, sl.id DESC
    """)
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(query, {"username": user.username}).mappings().fetchall()]
    return with_sic_detail(rows)


@router.get("/classified")
def classified(user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    """Approved leads that already have a CRM status.

    Joins the latest ML row WITH a crm_status (approve-time label rows have
    none), so a lead never shows here — or twice — just because label rows
    exist."""
    query = text("""
        SELECT
            sl.id,
            sl.company_name,
            sl.confidence_score,
            COALESCE(sl.corrected_website_url, sl.website_url)   AS website_url,
            COALESCE(sl.corrected_linkedin_url, sl.linkedin_url) AS linkedin_url,
            m.crm_status,
            sl.active_directors,
            DATE(sl.updated_at) AS date_approved
        FROM sales_leads sl
        JOIN (
            SELECT DISTINCT ON (lead_id) lead_id, crm_status
            FROM ml_pipeline_analytics
            WHERE crm_status IS NOT NULL
            ORDER BY lead_id, created_at DESC
        ) m ON m.lead_id = sl.id
        WHERE sl.assigned_ae_username = :username
          AND sl.status = 'approved'
        ORDER BY sl.updated_at DESC
    """)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(query, {"username": user.username}).mappings().fetchall()]


@router.post("/{lead_id}/enrich-directors")
def enrich_directors(lead_id: int, user: CurrentUser = Depends(get_current_user)) -> dict:
    """Fetch the lead's directors from Companies House (the slow call, deferred
    until the AE adds the lead to their pipeline). Returns the enriched lead."""
    lead = get_lead_for_ae(lead_id, user.username)
    enrich_lead_directors(lead_id, lead["crn"])
    # Re-read so the caller gets active_directors / directors_enriched.
    from sqlalchemy import select
    from models import sales_leads
    with engine.connect() as conn:
        row = conn.execute(select(sales_leads).where(sales_leads.c.id == lead_id)).mappings().fetchone()
    return with_sic_detail(dict(row))


@router.get("/{lead_id}/email-candidates")
def email_candidates_for_lead(lead_id: int, user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    """For each director on the lead: their total company appointments, a link to
    their Companies House officer page, and the email-format guesses to vet."""
    lead = get_lead_for_ae(lead_id, user.username)
    domain = domain_from_url(lead.get("corrected_website_url") or lead.get("website_url"))

    # Prefer the richer directors_info (name + appointments + officer url); fall
    # back to the plain names string for leads enriched before that existed.
    info = lead.get("directors_info") or []
    if info:
        base = [
            {"name": d.get("name"), "appointments": d.get("appointments"), "officer_url": d.get("url")}
            for d in info if d.get("name")
        ]
    else:
        base = [
            {"name": n.strip(), "appointments": None, "officer_url": None}
            for n in (lead.get("active_directors") or "").split(",") if n.strip()
        ]

    return [
        {
            "director_name": d["name"],
            "appointments": d["appointments"],
            "officer_url": d["officer_url"],
            "candidates": [
                {"pattern": pattern, "email": email}
                for pattern, email in email_candidates(d["name"], domain)
            ],
        }
        for d in base
    ]


@router.post("/{lead_id}/classify", status_code=204)
def classify(lead_id: int, body: ClassifyRequest, user: CurrentUser = Depends(get_current_user)):
    classify_lead(lead_id, user.username, body.crm_status, body.email_verdicts)
