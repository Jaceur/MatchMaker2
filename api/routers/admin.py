"""Admin control centre: qualification bar, allocation, pipeline jobs, cleanup,
and pipeline-health metrics. All endpoints require the admin role."""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import insert, select, text

from database import engine
from models import pipeline_jobs, sales_leads
from settings import get_qualify_percent, set_qualify_percent, get_qualify_bar
from leads import top_up_allocation, clear_all_data, clear_pipeline_data

from ..schemas import QualifyPercentRequest, PipelineJobRequest, AllocationRequest
from ..security import require_admin, CurrentUser

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/settings")
def get_settings() -> dict:
    return {"qualify_percent": get_qualify_percent(), "qualify_bar": get_qualify_bar()}


@router.put("/settings/qualify-percent")
def set_percent(body: QualifyPercentRequest) -> dict:
    pct = max(0, min(100, int(body.percent)))
    set_qualify_percent(pct)
    return {"qualify_percent": pct, "qualify_bar": get_qualify_bar()}


@router.post("/allocation/topup")
def allocation_topup(body: AllocationRequest) -> list[dict]:
    """Top every AE back up to the pending target from the qualified pool. With
    commit=False it only projects the result."""
    target = body.target or 20
    return top_up_allocation(target=target, commit=body.commit)


@router.post("/pipeline-job")
def queue_pipeline_job(body: PipelineJobRequest, user: CurrentUser = Depends(require_admin)) -> dict:
    """Queue a source+enrich job for the Railway worker to pick up."""
    with engine.begin() as conn:
        result = conn.execute(
            insert(pipeline_jobs).values(
                job_type="source_enrich",
                requested=int(body.count),
                status="pending",
                requested_by=user.username,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            ).returning(pipeline_jobs.c.id)
        )
        job_id = result.scalar()
    return {"job_id": job_id, "status": "pending", "requested": body.count}


@router.get("/pipeline-jobs")
def recent_jobs() -> list[dict]:
    """Recent pipeline jobs with progress, newest first."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(pipeline_jobs).order_by(pipeline_jobs.c.id.desc()).limit(20)
        ).mappings().fetchall()
    return [dict(r) for r in rows]


@router.get("/health")
def pipeline_health() -> dict:
    """Status breakdown of the working pool + the last screening run's outcomes."""
    with engine.connect() as conn:
        status_counts = {
            r["status"]: r["n"] for r in conn.execute(text("""
                SELECT COALESCE(status, 'unknown') AS status, COUNT(*) AS n
                FROM sales_leads GROUP BY 1
            """)).mappings().fetchall()
        }
        screening = [dict(r) for r in conn.execute(text("""
            SELECT qualified, is_holdout, COUNT(*) AS n
            FROM screening_log GROUP BY 1, 2
        """)).mappings().fetchall()]
        screen_reasons = [dict(r) for r in conn.execute(text("""
            SELECT screen_reason, COUNT(*) AS n FROM sales_leads
            WHERE status = 'screened_out' GROUP BY 1 ORDER BY 2 DESC LIMIT 15
        """)).mappings().fetchall()]
    return {
        "status_counts": status_counts,
        "screening": screening,
        "screen_reasons": screen_reasons,
        "qualify_bar": get_qualify_bar(),
    }


@router.get("/leads/latest")
def latest_leads() -> list[dict]:
    """Latest 100 leads for the admin preview table."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(sales_leads).order_by(sales_leads.c.id.desc()).limit(100)
        ).mappings().fetchall()
    return [dict(r) for r in rows]


@router.post("/clear/working")
def clear_working() -> dict:
    return {"message": clear_all_data()}


@router.post("/clear/pipeline")
def clear_pipeline() -> dict:
    return {"message": clear_pipeline_data()}
