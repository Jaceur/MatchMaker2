"""Shadow-mode scorer: the fail-safe guarantees, without a DB or a model file.

The single most important property here is that model_scorer NEVER breaks the
pipeline — a missing/broken model or an odd lead must yield None, not an
exception. These tests force each failure path.
"""
import os

os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("SUPABASE_HOST", "localhost")
os.environ.setdefault("SUPABASE_USER", "test")

import model_scorer  # noqa: E402


def _stage_c_lead(**over):
    lead = {"website_score": 40, "linkedin_score": 30, "confidence_score": 50,
            "turnover": 2_000_000, "employee_count": 20, "account_type": "small",
            "sic_codes": "62012", "incorporation_date": "2020-01-01"}
    lead.update(over)
    return lead


def test_no_model_file_returns_none(monkeypatch):
    """The shadow guarantee: with no model deployed, scoring is silently null."""
    monkeypatch.setattr(model_scorer._load_bundle, "__wrapped__", lambda: None)
    model_scorer._load_bundle.cache_clear()
    monkeypatch.setattr(model_scorer, "_load_bundle", lambda: None)
    assert model_scorer.score_lead_model(_stage_c_lead()) is None
    assert model_scorer.model_available() is False


def test_pre_stage_c_lead_is_declined(monkeypatch):
    """A lead with no web features never reached Stage C — out-of-distribution,
    so decline (None) rather than feed the model zeros it never trained on. This
    holds EVEN with a model loaded."""
    monkeypatch.setattr(model_scorer, "_load_bundle", lambda: {"model": object(), "features": []})
    lead = {"turnover": 2_000_000, "employee_count": 20,
            "website_score": None, "linkedin_score": None, "confidence_score": None}
    assert model_scorer.score_lead_model(lead) is None


def test_scoring_exception_is_swallowed(monkeypatch):
    """If predict blows up (version skew, bad row), return None — never raise."""
    class Boom:
        def predict_proba(self, X):
            raise RuntimeError("model kaboom")
    monkeypatch.setattr(model_scorer, "_load_bundle",
                        lambda: {"model": Boom(), "features": ["turnover"]})
    assert model_scorer.score_lead_model(_stage_c_lead()) is None


def test_reached_stage_c_detection():
    assert model_scorer._reached_stage_c({"website_score": 10}) is True
    assert model_scorer._reached_stage_c({"confidence_score": 0}) is True  # 0 is a real score
    assert model_scorer._reached_stage_c({"website_score": None}) is False
    assert model_scorer._reached_stage_c({}) is False
