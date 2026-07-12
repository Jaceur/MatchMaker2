"""Swipe endpoints: the AE's assigned queue, plus Pass and Approve actions."""
from fastapi import APIRouter, BackgroundTasks, Depends

from leads import get_pending_leads
from directors import enrich_lead_directors

from ..schemas import PassRequest, ApproveRequest
from ..security import get_current_user, CurrentUser
from ..services import pass_lead, approve_lead

router = APIRouter(prefix="/leads", tags=["leads"])


@router.get("/pending")
def pending(user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    """This AE's assigned, qualified leads awaiting a swipe (best fit first)."""
    return get_pending_leads(user.username)


@router.post("/{lead_id}/pass", status_code=204)
def pass_(lead_id: int, body: PassRequest, user: CurrentUser = Depends(get_current_user)):
    pass_lead(lead_id, user.username, body)


@router.post("/{lead_id}/approve", status_code=204)
def approve(
    lead_id: int,
    body: ApproveRequest,
    background: BackgroundTasks,
    user: CurrentUser = Depends(get_current_user),
):
    crn = approve_lead(lead_id, user.username, body)
    # Enrich directors right after approval (the slow CH calls run in the
    # background so the swipe stays snappy). The pipeline's "Add to pipeline"
    # gate remains a manual fallback if this doesn't complete.
    background.add_task(enrich_lead_directors, lead_id, crn)
