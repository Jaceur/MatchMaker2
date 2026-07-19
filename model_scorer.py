"""Serve the trained lead-approval model — SHADOW MODE.

`score_lead_model(lead)` returns the model's approval probability × 100 (0-100),
or None when it can't score. The pipeline stores this as `model_score` ALONGSIDE
the rules `lead_score`; nothing gates or ranks on it yet. The point is to gather
model-vs-rules evidence on live leads before trusting it (see HANDOVER §8).

Design rules:
- FAIL SAFE. Any problem — missing model file, an odd lead, a version mismatch —
  returns None and never raises. Shadow scoring must never break enrichment.
- Post-Stage-C only. The model was trained on leads that all had web-presence
  features; a lead without website_score/linkedin_score/confidence_score is
  out-of-distribution, so we decline to score it (return None) rather than feed
  the model zeros it never saw at that rate.
- Feature parity via ml_features.engineer — the exact columns training used.

The model file (lead_model.pkl) is produced by train_model.py and COMMITTED to
the repo so the Railway worker (which builds from git) has it. Retraining =
re-run train_model.py, commit the new .pkl, push.
"""
import functools
import os
from typing import Mapping, Optional

import pandas as pd

from ml_features import engineer

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lead_model.pkl")

# The raw lead columns engineer() needs as input (it derives age_months,
# sic_division and the ratios from these). A lead dict / sales_leads row carries
# all of them; anything absent becomes NaN, which the tree handles.
_RAW_INPUTS = [
    "incorporation_date", "sic_codes", "account_type",
    "employee_count", "turnover", "cash_at_bank", "foreign_exchange",
    "trade_debtors", "trade_creditors",
    "confidence_score", "website_score", "linkedin_score",
    "import_activity", "export_activity", "director_change_recent",
]

# The web-presence features that only exist post-Stage-C. If ALL are missing the
# lead never reached Stage C and is out-of-distribution for this model.
_STAGE_C_FEATURES = ["website_score", "linkedin_score", "confidence_score"]


@functools.lru_cache(maxsize=1)
def _load_bundle():
    """The joblib bundle {model, features, ...}, or None if unavailable. Cached;
    call _load_bundle.cache_clear() after dropping in a new model file."""
    if not os.path.exists(MODEL_PATH):
        print(f"model_scorer: no model file at {MODEL_PATH} — shadow scores will be null.")
        return None
    try:
        import joblib
        return joblib.load(MODEL_PATH)
    except Exception as e:
        print(f"model_scorer: could not load model ({e}) — shadow scores will be null.")
        return None


def model_available() -> bool:
    return _load_bundle() is not None


def _reached_stage_c(lead: Mapping) -> bool:
    return any(lead.get(f) is not None for f in _STAGE_C_FEATURES)


def score_lead_model(lead: Mapping) -> Optional[int]:
    """Model approval probability × 100 for one lead (a dict / mapping with the
    screening columns), or None if it can't/shouldn't be scored."""
    bundle = _load_bundle()
    if bundle is None or not _reached_stage_c(lead):
        return None
    try:
        row = {col: lead.get(col) for col in _RAW_INPUTS}
        df = engineer(pd.DataFrame([row]))
        X = df[bundle["features"]]
        prob = bundle["model"].predict_proba(X)[0, 1]
        return int(round(prob * 100))
    except Exception as e:
        print(f"model_scorer: scoring failed ({e}) — returning null for this lead.")
        return None
