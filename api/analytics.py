"""Analytics computations for the admin board.

All read-only, derived from tables the app already fills. The spine is
`sales_leads.status`: a *decided* lead is approved or archived (passed), so
approval = status == 'approved'. Features (SIC, financials, score) come straight
off sales_leads; the CRM-status breakdown joins ml_pipeline_analytics.

NB: this reads the current working pool — leads cleared to pipeline_archive
aren't included. Good enough for a first cut; move features into screening_log
later if we want it to survive a Clear Pipeline.
"""
import pandas as pd
from sqlalchemy import text

from database import engine

# Numeric features we test for correlation with approval + summarise per band.
NUMERIC_FEATURES = {
    "Cash": "cash_at_bank",
    "Turnover": "turnover",
    "Staff": "employee_count",
    "FX": "foreign_exchange",
    "Debtors": "trade_debtors",
    "Creditors": "trade_creditors",
    "Lead score": "lead_score",
    "Confidence": "confidence_score",
}
BOOL_FEATURES = {
    "Imports": "import_activity",
    "Exports": "export_activity",
    "Recent director change": "director_change_recent",
}


def _decided_frame() -> pd.DataFrame:
    """One row per decided lead (approved/passed), with features + crm_status."""
    query = text("""
        SELECT sl.id, sl.sic_codes, sl.status, sl.lead_score, sl.confidence_score,
               sl.turnover, sl.cash_at_bank, sl.foreign_exchange,
               sl.trade_debtors, sl.trade_creditors, sl.employee_count,
               sl.import_activity, sl.export_activity, sl.director_change_recent,
               m.crm_status
        FROM sales_leads sl
        LEFT JOIN (
            SELECT DISTINCT ON (lead_id) lead_id, crm_status
            FROM ml_pipeline_analytics
            WHERE crm_status IS NOT NULL
            ORDER BY lead_id, created_at DESC
        ) m ON m.lead_id = sl.id
        WHERE sl.status IN ('approved', 'archived')
    """)
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)
    if not df.empty:
        df["approved"] = (df["status"] == "approved").astype(int)
    return df


def _sic_labels() -> dict:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT code, description FROM sic_lookup")).fetchall()
    return {str(code): desc for code, desc in rows}


def _sic_breakdown(df: pd.DataFrame, labels: dict) -> list[dict]:
    """Top 20 primary SIC codes by volume, with approval rate."""
    if df.empty:
        return []
    d = df.copy()
    d["sic"] = (
        d["sic_codes"].fillna("").astype(str).str.split(",").str[0].str.strip()
    )
    d = d[d["sic"] != ""]
    if d.empty:
        return []
    grp = d.groupby("sic").agg(total=("id", "size"), approved=("approved", "sum"))
    grp = grp.sort_values("total", ascending=False).head(20).reset_index()
    return [
        {
            "sic": r["sic"],
            "label": labels.get(r["sic"], "Unknown"),
            "total": int(r["total"]),
            "approved": int(r["approved"]),
            "rate": round(r["approved"] / r["total"] * 100),
        }
        for _, r in grp.iterrows()
    ]


def _correlations(df: pd.DataFrame) -> list[dict]:
    """Point-biserial correlation (Pearson vs the 0/1 approval label) for each
    feature. Positive = higher value → more approvals."""
    if df.empty:
        return []
    out = []
    for label, col in {**NUMERIC_FEATURES, **BOOL_FEATURES}.items():
        if col not in df.columns:
            continue
        sub = df[[col, "approved"]].copy()
        sub[col] = pd.to_numeric(sub[col], errors="coerce")
        sub = sub.dropna()
        if len(sub) < 5 or sub[col].nunique() < 2:
            continue
        c = sub[col].corr(sub["approved"].astype(float))
        if pd.notna(c):
            out.append({"feature": label, "corr": round(float(c), 3), "n": int(len(sub))})
    out.sort(key=lambda r: abs(r["corr"]), reverse=True)
    return out


def _crm_breakdown(df: pd.DataFrame) -> list[dict]:
    """Average key features per CRM status (classified leads only)."""
    if df.empty or "crm_status" not in df.columns:
        return []
    cls = df[df["crm_status"].notna()]
    if cls.empty:
        return []
    keys = {"cash": "cash_at_bank", "staff": "employee_count", "fx": "foreign_exchange",
            "turnover": "turnover", "score": "lead_score"}
    out = []
    for status, g in cls.groupby("crm_status"):
        row = {"crm_status": status, "count": int(len(g))}
        for short, col in keys.items():
            val = pd.to_numeric(g[col], errors="coerce").mean()
            row[f"avg_{short}"] = None if pd.isna(val) else round(float(val))
        out.append(row)
    out.sort(key=lambda r: r["count"], reverse=True)
    return out


def _score_bands(df: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    """Per 10-point lead-score band: approval rate (calibration) and the average
    features that might explain it."""
    if df.empty:
        return [], []
    d = df.copy()
    d["lead_score"] = pd.to_numeric(d["lead_score"], errors="coerce")
    d = d.dropna(subset=["lead_score"])
    if d.empty:
        return [], []
    d["band_start"] = (d["lead_score"] // 10 * 10).astype(int)
    calibration, factors = [], []
    keys = {"cash": "cash_at_bank", "staff": "employee_count", "fx": "foreign_exchange",
            "turnover": "turnover", "debtors": "trade_debtors", "creditors": "trade_creditors"}
    for start, g in d.groupby("band_start"):
        decided = int(len(g))
        approved = int(g["approved"].sum())
        band = f"{int(start)}–{int(start) + 9}"
        calibration.append({
            "band": band,
            "decided": decided,
            "approved": approved,
            "rate": round(approved / decided * 100) if decided else 0,
        })
        row = {"band": band, "decided": decided, "rate": round(approved / decided * 100) if decided else 0}
        for short, col in keys.items():
            val = pd.to_numeric(g[col], errors="coerce").mean()
            row[f"avg_{short}"] = None if pd.isna(val) else round(float(val))
        factors.append(row)
    return calibration, factors


def _coverage() -> list[dict]:
    """% of enriched leads (anything past 'sourced') that have each field."""
    query = text("""
        SELECT
          COUNT(*) AS total,
          COUNT(employee_count) AS staff,
          COUNT(turnover) AS turnover,
          COUNT(cash_at_bank) AS cash,
          COUNT(foreign_exchange) AS fx,
          COUNT(website_url) AS website,
          COUNT(linkedin_url) AS linkedin,
          COUNT(*) FILTER (WHERE directors_enriched IS TRUE) AS directors,
          COUNT(*) FILTER (WHERE second_enriched IS TRUE) AS accounts
        FROM sales_leads
        WHERE status <> 'sourced'
    """)
    with engine.connect() as conn:
        row = conn.execute(query).mappings().fetchone() or {}
    total = row.get("total", 0) or 0
    fields = [
        ("Website", "website"), ("LinkedIn", "linkedin"), ("Staff", "staff"),
        ("Turnover", "turnover"), ("Cash", "cash"), ("FX", "fx"),
        ("Accounts parsed", "accounts"), ("Directors", "directors"),
    ]
    return [
        {
            "field": label,
            "populated": int(row.get(key, 0) or 0),
            "total": int(total),
            "pct": round((row.get(key, 0) or 0) / total * 100) if total else 0,
        }
        for label, key in fields
    ]


def compute_analytics() -> dict:
    df = _decided_frame()
    labels = _sic_labels()
    decided = int(len(df))
    approved = int(df["approved"].sum()) if not df.empty else 0
    calibration, factors = _score_bands(df)
    return {
        "totals": {
            "decided": decided,
            "approved": approved,
            "approval_rate": round(approved / decided * 100) if decided else 0,
        },
        "sic": _sic_breakdown(df, labels),
        "feature_correlations": _correlations(df),
        "crm_breakdown": _crm_breakdown(df),
        "score_calibration": calibration,
        "score_factors": factors,
        "coverage": _coverage(),
    }
