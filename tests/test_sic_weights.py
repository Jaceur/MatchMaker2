"""SIC-group score weighting: the shrinkage maths, and how it lands on a score.

Pure — no database. sic_weights imports `database` at module load for the live
query, so dummy connection settings are set before importing; SQLAlchemy builds
engines lazily, so nothing connects. compute_sic_multipliers() (the DB read) is
deliberately not covered here; shrink_multiplier() is where the thinking is.
"""
import os

os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("SUPABASE_HOST", "localhost")
os.environ.setdefault("SUPABASE_USER", "test")

import pytest  # noqa: E402

from scoring import LeadFeatures, score_lead  # noqa: E402
from sic_weights import (  # noqa: E402
    MIN_GROUP_N, MULT_MAX, MULT_MIN, SHRINK_K, shrink_multiplier,
)

BASE = 0.34  # the observed baseline approval rate on the labelled log


# ==========================================
# SHRINKAGE
# ==========================================
def test_no_history_means_no_opinion():
    assert shrink_multiplier(0.9, n=0, baseline=BASE) == 1.0


@pytest.mark.parametrize("n", [1, 4, 9, MIN_GROUP_N - 1])
def test_tiny_samples_never_move_the_score_however_extreme(n):
    """The floor, and the reason it exists. Shrinkage alone let "100% approval
    from 4 leads" earn a 1.27x boost, and "10% from 10" a 0.80x penalty — both
    samples whose true rate could sit anywhere across the baseline. Below
    MIN_GROUP_N the answer must be exactly 1.0, not merely close to it."""
    for rate in (0.0, 0.30, 1.0):
        assert shrink_multiplier(rate, n=n, baseline=BASE) == 1.0


def test_a_group_at_the_baseline_is_untouched_at_any_sample_size():
    """Construction sits at ~33% against a ~34% baseline. It should come out at
    1.0 however much data backs it — the DISTANCE from baseline drives the
    multiplier, the sample size only damps it."""
    for n in (5, 50, 500):
        assert shrink_multiplier(BASE, n=n, baseline=BASE) == pytest.approx(1.0)


def test_more_evidence_is_needed_to_move_further():
    """Just past the floor a group barely moves; the same rate with real data
    behind it moves a lot. This is the property the whole design rests on."""
    just_over = shrink_multiplier(0.06, n=MIN_GROUP_N, baseline=BASE)
    well_evidenced = shrink_multiplier(0.06, n=51, baseline=BASE)
    assert 1.0 > just_over > well_evidenced


def test_big_weak_sample_penalises():
    """Restaurants/Pubs: ~6% over ~51 leads — a real signal, and well below the
    baseline, so it should be pushed down hard."""
    assert shrink_multiplier(0.06, n=51, baseline=BASE) < 0.7


def test_big_strong_sample_rewards():
    """Software/Data: ~81% over ~21 leads."""
    assert shrink_multiplier(0.81, n=21, baseline=BASE) > 1.2


def test_more_evidence_moves_further_from_1():
    """The same rate, believed more as n grows — that's the 'evolves with the
    sample size' requirement, expressed as a monotonic property."""
    seen = [shrink_multiplier(0.06, n=n, baseline=BASE) for n in (25, 100, 1000)]
    assert seen == sorted(seen, reverse=True)
    assert seen[0] > seen[-1]


def test_weight_is_half_at_k():
    """At n == SHRINK_K the group is believed exactly as much as the baseline —
    the definition of the prior strength."""
    got = shrink_multiplier(1.0, n=SHRINK_K, baseline=0.5)
    # effective = 0.5*1.0 + 0.5*0.5 = 0.75  ->  0.75/0.5 = 1.5
    assert got == pytest.approx(1.5)


@pytest.mark.parametrize("rate", [0.0, 1.0])
def test_multiplier_is_clamped_even_at_the_extremes(rate):
    m = shrink_multiplier(rate, n=100_000, baseline=BASE)
    assert MULT_MIN <= m <= MULT_MAX


def test_zero_baseline_is_survivable():
    """No approvals at all yet — don't divide by zero, just have no opinion."""
    assert shrink_multiplier(0.0, n=50, baseline=0.0) == 1.0


# ==========================================
# HOW IT LANDS ON A SCORE
# ==========================================
def _strong_lead():
    # Comfortably over several minimums -> a high base score.
    return LeadFeatures(turnover=5_000_000, cash_at_bank=2_000_000, employee_count=30)


def test_multiplier_defaults_to_no_change():
    """Every pre-existing caller passes no multiplier and must be unaffected."""
    f = _strong_lead()
    assert score_lead(f) == score_lead(f, 1.0)


def test_penalty_scales_the_score_down_but_not_to_zero():
    f = _strong_lead()
    base = score_lead(f)
    penalised = score_lead(f, 0.5)
    assert penalised == round(base * 0.5)
    assert penalised > 0


def test_a_strong_lead_in_a_weak_industry_can_still_beat_the_bar():
    """The 'screen, but not completely' rule: the penalty is proportional, so
    genuinely strong financials survive it."""
    assert score_lead(_strong_lead(), 0.5) >= 40


def test_a_weak_lead_in_a_weak_industry_is_pushed_under():
    weak = LeadFeatures(turnover=600_000, employee_count=4)
    assert score_lead(weak) >= 30
    assert score_lead(weak, 0.5) < 30


def test_boost_cannot_exceed_100():
    huge = LeadFeatures(turnover=20_000_000, cash_at_bank=25_000_000,
                        employee_count=200, trade_debtors=6_000_000,
                        trade_creditors=6_000_000, foreign_exchange=6_000_000)
    assert score_lead(huge, MULT_MAX) <= 100


def test_dormant_stays_zero_however_good_its_industry():
    """Dormancy is a hard disqualification; no industry boost may rescue it."""
    dormant = LeadFeatures(account_type="dormant", turnover=5_000_000)
    assert score_lead(dormant, MULT_MAX) == 0
