"""Experiment: does adding the SIC-code APPROVAL RATE as a feature help?

The model already uses SIC as a category (sic_division). This tests the stronger
version: target-encode the full SIC code into its historical approval rate (the
"approval by SIC" signal from the analytics board), leak-free via sklearn's
TargetEncoder (each training fold learns the rates from the other folds only).

    python experiment_sic.py

Prints the model WITHOUT vs WITH the SIC-rate feature, then the actual SIC rates
it's leaning on. Same env as train_model.py (scikit-learn, joblib).
"""
import warnings

import pandas as pd

from train_model import load_dataset, engineer, NUMERIC, RATIOS, BOOLEAN

warnings.filterwarnings("ignore")  # quiet the sklearn deprecation notices


def main():
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.preprocessing import TargetEncoder
    from sklearn.compose import ColumnTransformer
    from sklearn.pipeline import Pipeline
    from sklearn.model_selection import cross_validate, StratifiedKFold

    df = engineer(load_dataset())
    if df.empty:
        print("No labelled leads yet.")
        return

    y = df["approved"].fillna(False).astype(int)
    df["sic_primary"] = (
        df.get("sic_codes", "").fillna("").astype(str)
        .str.split(",").str[0].str.strip().replace("", "unknown")
    )
    df["account_type"] = df["account_type"].astype(str).replace("nan", "unknown").fillna("unknown")

    # Numeric/boolean features that aren't constant/too-sparse (avoids the binning
    # crash and drops no-signal columns).
    num = [c for c in NUMERIC + RATIOS + BOOLEAN
           if df[c].notna().sum() >= 10 and df[c].nunique(dropna=True) >= 2]

    n, pos = len(y), int(y.sum())
    print(f"\n{n} leads, {pos} approvals ({pos / n:.0%})\n")
    if pos < 40:
        print("⚠️  Sparse — numbers are noisy.\n")

    def evaluate(cat_cols, label):
        X = df[num + cat_cols].copy()
        pre = ColumnTransformer(
            [("te", TargetEncoder(target_type="auto"), cat_cols)],
            remainder="passthrough",
        )
        clf = HistGradientBoostingClassifier(
            class_weight="balanced", learning_rate=0.05, max_iter=400,
            l2_regularization=1.0, early_stopping=True, random_state=42,
        )
        pipe = Pipeline([("pre", pre), ("clf", clf)])
        cv = StratifiedKFold(5, shuffle=True, random_state=42)
        res = cross_validate(pipe, X, y, cv=cv, scoring=["roc_auc", "average_precision"])
        print(f"  {label:<24} ROC-AUC {res['test_roc_auc'].mean():.3f}   PR-AUC {res['test_average_precision'].mean():.3f}")

    print("Effect of adding the SIC approval rate (target-encoded):")
    evaluate(["account_type"], "WITHOUT SIC rate")
    evaluate(["account_type", "sic_primary"], "WITH SIC rate")

    # The signal itself: which SICs actually approve well (min 5 leads).
    g = df.groupby("sic_primary")["approved"].agg(["size", "mean"])
    g = g[g["size"] >= 5].sort_values("mean", ascending=False)
    if not g.empty:
        print("\nHighest-approving SICs (>=5 leads):")
        for sic, r in g.head(6).iterrows():
            print(f"  {sic}: {r['mean']:.0%}  (n={int(r['size'])})")
        print("Lowest-approving SICs:")
        for sic, r in g.tail(6).iterrows():
            print(f"  {sic}: {r['mean']:.0%}  (n={int(r['size'])})")


if __name__ == "__main__":
    main()
