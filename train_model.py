"""Offline trainer for the lead-approval model — the eventual replacement for the
hand-written rules in scoring.score_lead().

Builds the labelled dataset (features the pipeline saw ⋈ the AE's approve/pass
verdict), trains a gradient-boosted tree, and measures three things:
  1. DISCRIMINATION — can it rank approvals above passes, and does it beat the rules?
  2. HOLDOUT — the same, on the unbiased 5% holdout (the honest test).
  3. CALIBRATION — do its probabilities mean what they say (so score = prob×100)?
It writes a CALIBRATED `lead_model.pkl`. Nothing here touches the live app.

    python train_model.py

Needs the Supabase creds (secrets.toml or SUPABASE_*/DB_PASSWORD env vars) and,
beyond the app's deps: scikit-learn (>=1.4) and joblib.
"""
import numpy as np
import pandas as pd
from sqlalchemy import text

from database import engine

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


def load_dataset() -> pd.DataFrame:
    query = text("""
        WITH latest_features AS (
            SELECT DISTINCT ON (lead_id) *
            FROM screening_log
            WHERE lead_id IS NOT NULL
            ORDER BY lead_id, created_at DESC
        ),
        verdicts AS (
            SELECT lead_id, BOOL_OR(is_worth_it) AS approved
            FROM ml_pipeline_analytics
            WHERE lead_id IS NOT NULL AND is_worth_it IS NOT NULL
            GROUP BY lead_id
        )
        SELECT f.*, v.approved
        FROM latest_features f
        JOIN verdicts v ON v.lead_id = f.lead_id
    """)
    with engine.connect() as conn:
        return pd.read_sql(query, conn)


def _safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    """num/den, but only where den > 0 — zero/negative/missing → NaN (which the
    tree handles natively). Avoids divide-by-zero blowing up a ratio."""
    return pd.to_numeric(num, errors="coerce") / pd.to_numeric(den, errors="coerce").where(lambda d: d > 0)


def engineer(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
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
        df[c] = df[c].astype("category")
    return df


def _new_model():
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(
        categorical_features="from_dtype", class_weight="balanced",
        learning_rate=0.05, max_iter=400, l2_regularization=1.0,
        early_stopping=True, random_state=42,
    )


def main():
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.model_selection import cross_validate, StratifiedKFold, train_test_split
    from sklearn.metrics import roc_auc_score, average_precision_score, brier_score_loss
    import joblib

    df = engineer(load_dataset())
    if df.empty:
        print("No labelled leads yet — keep swiping.")
        return

    y = df["approved"].fillna(False).astype(int)
    X = df[FEATURES]
    # Drop constant / too-sparse features — no signal, and an all-empty column
    # in a fold trips the tree's binning (needs >=2 distinct values to bin).
    usable = [c for c in FEATURES if X[c].notna().sum() >= 10 and X[c].nunique(dropna=True) >= 2]
    if len(usable) < len(FEATURES):
        print(f"(skipping constant/empty features: {', '.join(c for c in FEATURES if c not in usable)})")
    X = X[usable]
    n, pos = len(y), int(y.sum())
    hold = df.get("is_holdout")
    hold = hold.fillna(False).astype(bool) if hold is not None else pd.Series(False, index=df.index)
    print(f"\nLabelled leads: {n}   approvals: {pos} ({pos / n:.0%})   holdout: {int(hold.sum())}")

    if n < 150 or pos < 40:
        print("⚠️  Sparse data — metrics below are noisy. Keep swiping.\n")

    if pos < 10 or (n - pos) < 10:
        print("Too few of one class to evaluate. Stopping.")
        return

    folds = min(5, pos, n - pos)
    cv = StratifiedKFold(folds, shuffle=True, random_state=42)
    model = _new_model()

    # --- 1. Discrimination (whole set, cross-validated) ---
    res = cross_validate(model, X, y, cv=cv, scoring=["roc_auc", "average_precision"])
    print("DISCRIMINATION  (rank approvals above passes — higher is better)")
    print(f"  MODEL  ROC-AUC {res['test_roc_auc'].mean():.3f}   PR-AUC {res['test_average_precision'].mean():.3f}   ({folds}-fold CV)")
    if df["lead_score"].notna().any():
        s = df["lead_score"].fillna(df["lead_score"].median())
        print(f"  RULES  ROC-AUC {roc_auc_score(y, s):.3f}   PR-AUC {average_precision_score(y, s):.3f}   (the bar to beat)")

    # --- 2. Holdout (train on the rest, test on the unbiased holdout) ---
    print("\nHOLDOUT  (unbiased — the honest test)")
    nh = int(hold.sum())
    if nh >= 10 and y[hold].nunique() == 2:
        m = _new_model().fit(X[~hold], y[~hold])
        p = m.predict_proba(X[hold])[:, 1]
        print(f"  MODEL  ROC-AUC {roc_auc_score(y[hold], p):.3f}   PR-AUC {average_precision_score(y[hold], p):.3f}   (n={nh}, treat as rough)")
        if df.loc[hold, "lead_score"].notna().any():
            sh = df.loc[hold, "lead_score"].fillna(df["lead_score"].median())
            print(f"  RULES  ROC-AUC {roc_auc_score(y[hold], sh):.3f}   PR-AUC {average_precision_score(y[hold], sh):.3f}")
    else:
        print(f"  Too small to score yet (n={nh}, need ~10+ with both approve & pass). Grows as you swipe.")

    # --- 3. Calibration — a simple fit / calibrate / test split (NOT nested CV,
    #        whose tiny folds trip the tree's binning on sparse features). ---
    print("\nCALIBRATION  (does a predicted 60% really approve ~60% of the time?)")
    try:
        Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25, stratify=y, random_state=1)
        cvc = StratifiedKFold(folds, shuffle=True, random_state=1)  # SHUFFLED — no all-empty folds
        cal = CalibratedClassifierCV(_new_model(), method="sigmoid", cv=cvc).fit(Xtr, ytr)
        p = cal.predict_proba(Xte)[:, 1]
        print(f"  Brier score {brier_score_loss(yte, p):.3f}   (lower is better; 0 = perfect; test n={len(yte)})")
        rep = pd.DataFrame({"pred": p, "actual": yte.to_numpy()})
        for band, g in rep.groupby(pd.cut(rep["pred"], [0, .2, .4, .6, .8, 1.0], include_lowest=True), observed=True):
            if len(g):
                print(f"    predicted {band}: model says {g['pred'].mean():.0%}, actually {g['actual'].mean():.0%}  (n={len(g)})")
    except Exception as e:
        print(f"  (skipped — {e})")

    # --- Save the CALIBRATED model — internal CV, but with a SHUFFLED splitter
    #     (unlike the default cv=int) so no fold lands all-empty on a sparse
    #     feature. Uses all the data. ---
    final = CalibratedClassifierCV(
        _new_model(), method="sigmoid",
        cv=StratifiedKFold(folds, shuffle=True, random_state=7),
    ).fit(X, y)
    joblib.dump(
        {"model": final, "features": usable,
         "numeric": NUMERIC, "ratios": RATIOS, "boolean": BOOLEAN, "categorical": CATEGORICAL},
        "lead_model.pkl",
    )
    print("\nSaved lead_model.pkl (calibrated). Not wired into score_lead yet.")


if __name__ == "__main__":
    main()
