"""Feature engineering for the lead-approval model — SHARED by training and
serving, so the two can never drift.

train_model.py fits on these columns; model_scorer.py builds the exact same ones
at serve time. If a feature is added, it changes here once and both sides move
together. Pure pandas/numpy (no sklearn, no DB) so the worker can import it
without the training stack.

The model can only be scored on leads that reached Stage C of the pipeline —
website_score / linkedin_score / confidence_score are set there. Every labelled
lead in training had them (only Stage-C survivors get swiped), so scoring a
pre-Stage-C lead would be out-of-distribution. model_scorer enforces this.
"""
import numpy as np
import pandas as pd

# Raw numeric features. lead_score (the rules output) is EXCLUDED on purpose —
# feeding it back in would just teach the model to copy the rules.
NUMERIC = [
    "employee_count", "turnover", "cash_at_bank", "foreign_exchange",
    "trade_debtors", "trade_creditors", "confidence_score", "website_score",
    "linkedin_score", "age_months",
]
# Engineered ratios — often carry more signal than the raw figures, ~free to add.
RATIOS = [
    "cash_to_turnover", "turnover_per_employee", "debtors_to_turnover",
    "creditors_to_turnover", "fx_to_turnover",
]
BOOLEAN = ["import_activity", "export_activity", "director_change_recent"]
CATEGORICAL = ["account_type", "sic_division"]
FEATURES = NUMERIC + RATIOS + BOOLEAN + CATEGORICAL


def _safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    """num/den, but only where den > 0 — zero/negative/missing → NaN (which the
    tree handles natively). Avoids divide-by-zero blowing up a ratio."""
    return pd.to_numeric(num, errors="coerce") / pd.to_numeric(den, errors="coerce").where(lambda d: d > 0)


# Raw numeric inputs used in arithmetic below. Coerced to numeric so a SQL NULL
# (Python None, giving an object column in a one-row serve frame) doesn't blow up
# .abs() / division. On a training frame these are already numeric, so coercion
# is a no-op there — train and serve stay identical, no retrain needed.
_NUMERIC_INPUTS = [
    "cash_at_bank", "turnover", "employee_count",
    "trade_debtors", "trade_creditors", "foreign_exchange",
]


def engineer(df: pd.DataFrame) -> pd.DataFrame:
    """Add the engineered columns (age, sic_division, ratios) and coerce the
    boolean/categorical dtypes. Operates on a frame that already carries the raw
    screening columns; returns the same frame with FEATURES available."""
    df = df.copy()
    for col in _NUMERIC_INPUTS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    inc = pd.to_datetime(df.get("incorporation_date"), errors="coerce")
    df["age_months"] = ((pd.Timestamp.now() - inc).dt.days // 30).clip(lower=0)
    df["sic_division"] = (
        df.get("sic_codes", "").fillna("").astype(str)
        .str.split(",").str[0].str.strip().str[:2].replace("", np.nan)
    )
    # Ratios
    df["cash_to_turnover"] = _safe_div(df["cash_at_bank"], df["turnover"])
    df["turnover_per_employee"] = _safe_div(df["turnover"], df["employee_count"])
    df["debtors_to_turnover"] = _safe_div(df["trade_debtors"], df["turnover"])
    df["creditors_to_turnover"] = _safe_div(df["trade_creditors"], df["turnover"])
    df["fx_to_turnover"] = _safe_div(df["foreign_exchange"].abs(), df["turnover"])
    for b in BOOLEAN:
        df[b] = df[b].map({True: 1.0, False: 0.0})
    for c in CATEGORICAL:
        # Missing → an explicit "__missing__" LEVEL, so the column is uniformly
        # strings. sklearn's categorical encoder rejects a mix of strings and
        # NaN/NA (it can't sort them), which bit both the training fit (float NaN)
        # and single-row serving (pandas <NA>). A sentinel string sidesteps the
        # whole class of bug and is fine for a tree — "unknown" is just a level.
        df[c] = df[c].astype("string").fillna("__missing__").astype("category")
    return df
