"""The BETA GP score: does this company look like FX, card spend and balances?

Pure functions — no DB, no network, no clock. The tests are written around the
DECISIONS the weights encode rather than the numbers themselves, so retuning a
weight doesn't break the suite but reversing an intent does.
"""
import os

os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("SUPABASE_HOST", "localhost")
os.environ.setdefault("SUPABASE_USER", "test")

import pytest  # noqa: E402

from api.gp_scoring import (  # noqa: E402
    DEFAULT_THRESHOLD, capital_points, is_overseas, qualifies, score_lead,
)


def why(event) -> str:
    return " | ".join(score_lead(event)[1])


# ==========================================
# SECTOR — free at phase 1, so it's what makes the fast path possible
# ==========================================
def test_fx_sectors_outrank_card_sectors():
    """Both are good; FX is the higher-margin line, so it must rank above."""
    fx = score_lead({"sic_codes": ["46900"]})[0]        # wholesale
    card = score_lead({"sic_codes": ["73110"]})[0]      # advertising
    neutral = score_lead({"sic_codes": ["96090"]})[0]
    assert fx > card > neutral == 0


@pytest.mark.parametrize("sic", ["68209", "68100", "41100", "98000", "99999", "74990", "64209"])
def test_zero_gp_sectors_are_suppressed_not_merely_ignored(sic):
    """A property SPV or dormant company cannot generate card, FX or balance GP.
    Scoring them 0 would let other signals carry them in; they must go NEGATIVE."""
    assert score_lead({"sic_codes": [sic]})[0] < 0


def test_a_dormant_company_cannot_be_rescued_by_ownership():
    """The old high_value flag passes dormant + corporate-owned straight through.
    That's 76 dormant companies a fortnight; the score must refuse them."""
    dormant = {"sic_codes": ["99999"], "corporate_owner": True,
               "owner_name": "SOMETHING HOLDINGS LIMITED"}
    assert score_lead(dormant)[0] < 0
    assert not qualifies(dormant)


def test_sic_is_read_however_chstream_sends_it():
    assert score_lead({"sic_codes": ["46900"]})[0] == score_lead({"sic_codes": "46900, 12345"})[0]
    assert score_lead({"sic_codes": None})[0] == 0


# ==========================================
# CAPITAL — deliberately weak, log-scaled, never a penalty
# ==========================================
def test_capital_is_log_scaled_across_the_stated_range():
    """The brief: £100k is a must-have, £1,000 should be near zero."""
    assert capital_points(1_000) == 0
    assert capital_points(100_000) == 40
    assert 0 < capital_points(10_000) < capital_points(100_000)


def test_a_hundred_k_alone_clears_the_bar():
    """"100k is a must have" — it has to qualify on its own, with no sector help."""
    assert qualifies({"starting_capital": 100_000})


def test_a_nominal_one_pound_is_never_a_penalty():
    """A company can hold £1 of share capital and still be well funded (one
    investor, one share). Absence of evidence must not become evidence of
    absence — these score zero, not negative."""
    assert capital_points(1) == 0
    assert capital_points("") == 0
    assert capital_points(None) == 0
    assert capital_points("nonsense") == 0
    # ...and it must not drag down an otherwise good lead.
    good = {"sic_codes": ["46900"], "director_residence": "China"}
    assert score_lead({**good, "starting_capital": 1})[0] == score_lead(good)[0]


def test_a_huge_outlier_cannot_swamp_everything_else():
    """Linear scaling would let one £2m company outrank every real signal."""
    assert capital_points(2_000_000) == capital_points(100_000)


# ==========================================
# THE FX SIGNALS
# ==========================================
def test_overseas_director_is_the_strongest_single_person_signal():
    base = {"sic_codes": ["62012"]}
    assert score_lead({**base, "director_residence": "China"})[0] > score_lead(base)[0]


@pytest.mark.parametrize("residence", ["United Kingdom", "England", "Scotland", "Wales", ""])
def test_uk_residence_is_not_overseas(residence):
    assert is_overseas(residence) is False


def test_a_foreign_parent_stacks_on_top_of_corporate_ownership():
    """A UK entity owned by a GmbH means inbound funding and ongoing cross-border
    settlement — the best FX signal available at incorporation."""
    uk_parent = {"corporate_owner": True, "owner_name": "ACME HOLDINGS LIMITED"}
    de_parent = {"corporate_owner": True, "owner_name": "ACME BETEILIGUNGS GmbH"}
    assert score_lead(de_parent)[0] > score_lead(uk_parent)[0]
    assert "Foreign parent" in why(de_parent)


def test_trade_words_are_picked_up_from_the_name_alone():
    """Available at phase 1, before any lookup."""
    assert "Trade name" in why({"company_name": "VERTEX INTERNATIONAL TRADING LTD"})
    assert "Trade name" not in why({"company_name": "VERTEX CONSULTING LTD"})


def test_a_nominee_director_is_a_penalty():
    """20+ directorships is a corporate service provider, not a founder — the
    company is administered rather than run, and tends to be a shell."""
    base = {"sic_codes": ["62012"]}
    assert score_lead({**base, "director_other_companies": 40})[0] < score_lead(base)[0]
    assert score_lead({**base, "director_other_companies": 2})[0] == score_lead(base)[0]


# ==========================================
# PHASE GATING — the speed win
# ==========================================
def test_a_strong_lead_can_qualify_on_phase_1_fields_alone():
    """Sector + name + postcode all arrive in the stream event, so this lead is
    on screen seconds before any Companies House call runs."""
    phase1 = {"sic_codes": ["47910"], "company_name": "MHH TRADERS LTD",
              "postcode": "WC2H 9JQ"}
    assert qualifies(phase1)


def test_a_lead_needing_enrichment_waits_for_phase_2():
    """Software alone isn't enough; software run from Latvia is."""
    phase1 = {"sic_codes": ["62012"], "company_name": "NAURUM LTD"}
    assert not qualifies(phase1)
    assert qualifies({**phase1, "director_residence": "Latvia"})


def test_reasons_explain_the_score_with_signs():
    score, reasons = score_lead({"sic_codes": ["46900"], "director_residence": "China"})
    assert any("+30" in r for r in reasons) and any("+25" in r for r in reasons)
    assert score == 55


def test_threshold_is_the_documented_default():
    assert DEFAULT_THRESHOLD == 40
