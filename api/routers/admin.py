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


@router.get("/stats")
def pipeline_stats() -> dict:
    """Pipeline-health counts + how many scored leads clear the current bar."""
    bar = get_qualify_bar()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT
              COUNT(*) AS total,
              COUNT(*) FILTER (WHERE status = 'screened_out') AS screened_out,
              COUNT(*) FILTER (WHERE status = 'sourced') AS awaiting_enrichment,
              COUNT(*) FILTER (WHERE status = 'ready_for_swipe' AND lead_score >= :bar) AS qualified,
              -- Must mirror the allocation pool in leads.top_up_allocation, holdout
              -- exemption included, or this reads 0 while distribute hands out 170.
              COUNT(*) FILTER (WHERE status = 'ready_for_swipe'
                               AND assigned_ae_username IS NULL
                               AND (lead_score >= :bar OR is_holdout IS TRUE)) AS awaiting_allocation,
              AVG(lead_score) FILTER (WHERE status = 'ready_for_swipe' AND lead_score >= :bar) AS avg_qualified,
              COUNT(*) FILTER (WHERE lead_score IS NOT NULL) AS scored,
              COUNT(*) FILTER (WHERE lead_score >= :bar) AS passing
            FROM sales_leads
        """), {"bar": bar}).mappings().fetchone() or {}
    return {
        "total": row.get("total") or 0,
        "screened_out": row.get("screened_out") or 0,
        "awaiting_enrichment": row.get("awaiting_enrichment") or 0,
        "qualified": row.get("qualified") or 0,
        "awaiting_allocation": row.get("awaiting_allocation") or 0,
        "avg_qualified": round(float(row.get("avg_qualified") or 0)),
        "scored": row.get("scored") or 0,
        "passing": row.get("passing") or 0,
        "bar": bar,
        "qualify_percent": get_qualify_percent(),
    }


def _auc(pairs: list[tuple[float, int]]) -> float | None:
    """ROC-AUC via the Mann-Whitney rank-sum identity — no sklearn needed (the
    API image doesn't ship it). pairs = (score, label 0/1). None if a class is
    empty. Ties get averaged ranks, so it matches sklearn's roc_auc_score."""
    pos = [s for s, y in pairs if y == 1]
    neg = [s for s, y in pairs if y == 0]
    if not pos or not neg:
        return None
    order = sorted(pairs, key=lambda p: p[0])
    ranks, i, n = {}, 0, len(order)
    while i < n:
        j = i
        while j < n and order[j][0] == order[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0  # 1-based, averaged over the tie block
        for k in range(i, j):
            ranks[k] = avg_rank
        i = j
    rank_sum_pos = sum(
        r for idx, r in ranks.items() if order[idx][1] == 1
    )
    n_pos, n_neg = len(pos), len(neg)
    return (rank_sum_pos - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


@router.get("/shadow-model")
def shadow_model() -> dict:
    """Shadow-mode scoreboard: how the trained model (model_score) compares to the
    rules (lead_score) on real decided leads. Evidence only — nothing is wired in.

    Reads the DURABLE log (screening_log ⋈ ml_pipeline_analytics), never the live
    pool, so 'Clear working pool' can't inflate it (see the analytics §6 warning).
    """
    with engine.connect() as conn:
        # Deployment coverage: what share of the CURRENT swipe pool the model has
        # scored (a null means the lead predates shadow scoring — run rescore).
        cov = conn.execute(text("""
            SELECT COUNT(*) AS pool,
                   COUNT(model_score) AS scored
            FROM sales_leads
            WHERE status IN ('ready_for_swipe', 'approved')
        """)).mappings().fetchone() or {}

        # Decided leads that carry BOTH a model score and a human verdict.
        rows = conn.execute(text("""
            WITH lf AS (
                SELECT DISTINCT ON (lead_id) lead_id, lead_score, model_score
                FROM screening_log
                WHERE lead_id IS NOT NULL AND model_score IS NOT NULL
                ORDER BY lead_id, created_at DESC
            ),
            v AS (
                SELECT lead_id, BOOL_OR(is_worth_it) AS approved
                FROM ml_pipeline_analytics
                WHERE lead_id IS NOT NULL AND is_worth_it IS NOT NULL
                GROUP BY lead_id
            )
            SELECT lf.lead_score, lf.model_score, v.approved::int AS approved
            FROM lf JOIN v ON v.lead_id = lf.lead_id
        """)).mappings().fetchall()

    model_pairs = [(float(r["model_score"]), r["approved"]) for r in rows]
    rules_pairs = [(float(r["lead_score"]), r["approved"]) for r in rows if r["lead_score"] is not None]
    n = len(rows)
    approvals = sum(r["approved"] for r in rows)

    # Precision @ top quintile: of the 20% each scorer ranks highest, what share
    # were approved? The deployment-relevant question — "would ranking by the
    # model put better leads at the top of the AE's pile?"
    def precision_at_top(pairs, frac=0.2):
        if len(pairs) < 5:
            return None
        k = max(1, round(len(pairs) * frac))
        top = sorted(pairs, key=lambda p: p[0], reverse=True)[:k]
        return round(100 * sum(y for _, y in top) / k)

    # Approval rate by model_score band, for a calibration read on live decisions.
    bands = []
    for lo in range(0, 100, 20):
        seg = [y for s, y in model_pairs if lo <= s < lo + 20]
        if seg:
            bands.append({"band": f"{lo}-{lo+19}", "n": len(seg),
                          "approval_rate": round(100 * sum(seg) / len(seg))})

    return {
        "model_deployed": bool(cov.get("scored")),
        "coverage": {
            "pool": cov.get("pool") or 0,
            "scored": cov.get("scored") or 0,
            "pct": round(100 * (cov.get("scored") or 0) / cov["pool"]) if cov.get("pool") else 0,
        },
        "decided_scored": n,
        "approvals": approvals,
        "model_auc": round(_auc(model_pairs), 3) if _auc(model_pairs) is not None else None,
        "rules_auc": round(_auc(rules_pairs), 3) if _auc(rules_pairs) is not None else None,
        "model_precision_at_top": precision_at_top(model_pairs),
        "rules_precision_at_top": precision_at_top(rules_pairs),
        "model_bands": bands,
    }


@router.get("/ae-performance")
def ae_performance() -> list[dict]:
    """Per-AE workload: leads remaining to swipe, total assigned, approvals, and
    SF entries (leads classified into a CRM status)."""
    query = text("""
        SELECT u.username AS ae,
               COALESCE(la.total_assigned, 0) AS total_assigned,
               COALESCE(la.remaining, 0)      AS remaining,
               COALESCE(la.approved, 0)       AS approved,
               COALESCE(sf.sf_entry, 0)       AS sf_entry
        FROM users u
        LEFT JOIN (
            SELECT assigned_ae_username AS ae,
                   SUM(CASE WHEN status IN ('ready_for_swipe','approved','archived') THEN 1 ELSE 0 END) AS total_assigned,
                   SUM(CASE WHEN status = 'ready_for_swipe' THEN 1 ELSE 0 END) AS remaining,
                   SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved
            FROM sales_leads WHERE assigned_ae_username IS NOT NULL GROUP BY 1
        ) la ON la.ae = u.username
        LEFT JOIN (
            SELECT swiped_by AS ae, COUNT(DISTINCT lead_id) AS sf_entry
            FROM ml_pipeline_analytics WHERE crm_status IS NOT NULL GROUP BY 1
        ) sf ON sf.ae = u.username
        ORDER BY remaining DESC, u.username
    """)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(query).mappings().fetchall()]


@router.put("/settings/qualify-percent")
def set_percent(body: QualifyPercentRequest) -> dict:
    pct = max(0, min(100, int(body.percent)))
    set_qualify_percent(pct)
    return {"qualify_percent": pct, "qualify_bar": get_qualify_bar()}


@router.post("/allocation/topup")
def allocation_topup(body: AllocationRequest) -> list[dict]:
    """Top every AE back up to the pending target from the qualified pool. With
    commit=False it only projects the result. No target in the request -> the
    runtime default resolved inside top_up_allocation (one source of truth)."""
    return top_up_allocation(target=body.target, commit=body.commit)


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


@router.post("/pipeline-jobs/{job_id}/cancel")
def cancel_job(job_id: int) -> dict:
    """Cancel a queued/running job (the worker checks status between leads)."""
    with engine.begin() as conn:
        result = conn.execute(text("""
            UPDATE pipeline_jobs SET status = 'cancelled', updated_at = now()
            WHERE id = :i AND status IN ('pending', 'running')
        """), {"i": job_id})
    return {"cancelled": result.rowcount > 0}


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
