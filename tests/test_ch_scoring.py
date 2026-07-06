"""Scoring: every weight in §4.1/§4.2, tier boundaries, missing-data
neutrality, and the event-trigger promotion (Phase 3 acceptance)."""
from decimal import Decimal

from ch_scoring import WEIGHTS, score_company, tier_for, top_signals


def test_every_positive_signal_scores_its_weight():
    cases = {
        "foreign_corporate_psc": {"foreign_corporate_psc": True},
        "uk_corporate_psc": {"uk_corporate_psc": True},
        "target_sic": {"target_sic": True},
        "quality_serial_director": {"quality_serial_director": True},
        "two_plus_directors": {"active_director_count": 2},
        "officer_foreign_address": {"officer_foreign_address": True},
        "foreign_currency_capital": {"has_foreign_currency_capital": True},
    }
    for signal, inputs in cases.items():
        score, _, breakdown = score_company(inputs)
        assert breakdown == {signal: WEIGHTS[signal]}, signal
        assert score == WEIGHTS[signal], signal


def test_capital_bands():
    score, _, b = score_company({"best_gbp_capital": Decimal(50000)})
    assert b == {"capital_50k_plus": 30} and score == 30

    score, _, b = score_company({"best_gbp_capital": Decimal(10000)})
    assert b == {"capital_10k_to_50k": 20} and score == 20

    # Below £10k: no capital points, but no penalty either (director count unknown).
    score, _, b = score_company({"best_gbp_capital": Decimal(100)})
    assert b == {} and score == 0


def test_missing_capital_is_neutral_never_negative():
    # A single-director company with UNKNOWN capital must not get the -15
    # combo penalty — missing data contributes 0, not negative.
    score, _, breakdown = score_company(
        {"active_director_count": 1, "best_gbp_capital": None})
    assert score == 0
    assert breakdown == {}


def test_single_director_low_capital_penalty_needs_both_facts():
    score, tier, breakdown = score_company(
        {"active_director_count": 1, "best_gbp_capital": Decimal(1)})
    assert breakdown == {"single_director_low_capital": -15}
    assert tier == 3


def test_negative_signals():
    score, tier, b = score_company({"formation_agent_address": True})
    assert b == {"formation_agent_address": -25} and tier == 3

    score, tier, b = score_company({"passive_sic_only": True})
    assert b == {"passive_sic_only": -20} and tier == 3

    score, tier, b = score_company({"spv_farm_director": True})
    assert b == {"spv_farm_director": -10} and tier == 3


def test_disqualified_scores_zero_tier_three():
    score, tier, breakdown = score_company(
        {"disqualified": "status: dissolved", "foreign_corporate_psc": True})
    assert (score, tier) == (0, 3)
    assert breakdown == {"disqualified": "status: dissolved"}


def test_tier_boundaries():
    assert tier_for(60) == 1
    assert tier_for(59) == 2
    assert tier_for(30) == 2
    assert tier_for(29) == 3
    assert tier_for(-25) == 3


def test_foreign_parent_newco_outscores_one_pound_company():
    # The scoring philosophy in one test: a foreign-parented NewCo with
    # foreign-currency capital vs a single-director £1 company.
    strong, tier_strong, _ = score_company({
        "foreign_corporate_psc": True,
        "has_foreign_currency_capital": True,
        "target_sic": True,
        "active_director_count": 2,
    })
    weak, tier_weak, _ = score_company({
        "active_director_count": 1,
        "best_gbp_capital": Decimal(1),
        "formation_agent_address": True,
    })
    assert strong == 40 + 35 + 15 + 10 and tier_strong == 1
    assert weak == -40 and tier_weak == 3


def test_event_promotes_tier2_company_to_tier1():
    # Phase 3 acceptance: a Tier 2 company + a replayed SH01 event -> Tier 1.
    base = {"foreign_corporate_psc": True}                    # 40 -> Tier 2
    score, tier, _ = score_company(base)
    assert tier == 2

    score, tier, breakdown = score_company(base, event_types=["sh01_raise"])
    assert tier == 1
    assert score == 40 + WEIGHTS["event_bonus"]
    assert breakdown["event_bonus"] == WEIGHTS["event_bonus"]
    assert breakdown["events"] == ["sh01_raise"]


def test_event_promotes_even_a_weak_company():
    score, tier, _ = score_company({}, event_types=["mr01_charge"])
    assert tier == 1
    assert score == WEIGHTS["event_bonus"]


def test_top_signals_orders_positives_only():
    _, _, breakdown = score_company({
        "foreign_corporate_psc": True,
        "target_sic": True,
        "active_director_count": 2,
        "formation_agent_address": True,   # negative: never in top signals
    })
    top = top_signals(breakdown, n=2)
    assert top == ["foreign_corporate_psc +40", "target_sic +15"]
