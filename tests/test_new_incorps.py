"""The live new-incorps stream: the in-memory broker (two channels + claims) and
the High-Value criteria (Zone-1 postcodes, capital, corporate ownership).

Pure asyncio / pure functions (asyncio.run so no pytest-asyncio), no HTTP, no DB.
"""
import asyncio
import os

os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("SUPABASE_HOST", "localhost")
os.environ.setdefault("SUPABASE_USER", "test")

import pytest  # noqa: E402

from datetime import datetime, timedelta, timezone  # noqa: E402

from api.config import settings  # noqa: E402
from api.routers.new_incorps import (  # noqa: E402
    MAX_ON_SCREEN, _Broker, _sse, capital_threshold, is_high_value, is_zone1,
    stream_lag_seconds,
)


# ==========================================
# HIGH-VALUE CRITERIA
# ==========================================
@pytest.mark.parametrize("pc", [
    "EC1V 0AA", "WC2N 5DU", "W1D 3QF", "SW1A 1AA",
    "SE1 9SG", "NW1 4RY", "N1 9GU", "N1C 4XX", "E1 6AN", "E1W 3SS",
])
def test_zone1_includes_broad_central(pc):
    assert is_zone1(pc) is True


@pytest.mark.parametrize("pc", [
    "W10 6TR", "N10 3AB", "SE10 8XJ", "NW10 7XX", "SW11 1AA",
    "E14 5AB", "E10 7QP", "M1 1AE", "", None,
])
def test_zone1_excludes_neighbours_and_junk(pc):
    """The district-number guard: W1 but not W10, N1 but not N10, E1 but not E14."""
    assert is_zone1(pc) is False


def test_high_value_is_any_one_criterion():
    assert is_high_value({"starting_capital": 30000}) is True          # over the bar
    assert is_high_value({"corporate_owner": True}) is True            # corporate
    assert is_high_value({"postcode": "EC1V 0AA"}) is True             # zone 1
    # none of them:
    assert is_high_value({"starting_capital": 1000, "postcode": "M1 1AE"}) is False


def test_capital_threshold_is_strictly_above():
    """£10k as of 2026-08-05 (was £25k) — and it's the bar itself that must not
    qualify, or "over £10k" quietly means "£10k and up"."""
    assert capital_threshold() == 10_000
    assert is_high_value({"starting_capital": 10_000}) is False
    assert is_high_value({"starting_capital": 10_001}) is True


def test_threshold_is_tunable_without_a_deploy(monkeypatch):
    """It's read per call from config, so a Railway variable change + restart
    retunes it. £15k leads used to be invisible; they aren't now."""
    monkeypatch.setattr(settings, "high_value_capital_threshold", 50_000)
    assert is_high_value({"starting_capital": 30_000}) is False
    monkeypatch.setattr(settings, "high_value_capital_threshold", 5_000)
    assert is_high_value({"starting_capital": 30_000}) is True


def test_capital_empty_or_garbage_is_safe():
    assert is_high_value({"starting_capital": ""}) is False
    assert is_high_value({"starting_capital": None}) is False
    assert is_high_value({"starting_capital": "n/a"}) is False


# ==========================================
# STREAM LAG (CH publish -> our ingest)
# ==========================================
NOW = datetime(2026, 8, 11, 9, 0, 0, tzinfo=timezone.utc)


def test_lag_is_measured_from_the_ch_publish_time():
    ev = {"ch_published_at": (NOW - timedelta(seconds=4.5)).isoformat()}
    assert stream_lag_seconds(ev, NOW) == 4.5


def test_z_suffix_is_parsed():
    """CH sends '...Z', which fromisoformat rejected before 3.11 and which we
    normalise rather than depend on the runtime for."""
    assert stream_lag_seconds({"ch_published_at": "2026-08-11T08:59:50Z"}, NOW) == 10.0


def test_a_naive_timestamp_is_assumed_utc():
    """No offset must not mean "local time" — that would silently add hours."""
    assert stream_lag_seconds({"ch_published_at": "2026-08-11T08:59:00"}, NOW) == 60.0


def test_missing_publish_time_is_none_not_zero():
    """An older CHStream doesn't send it. Reporting 0 would flatter the numbers
    by pretending those companies arrived instantly."""
    assert stream_lag_seconds({}, NOW) is None
    assert stream_lag_seconds({"ch_published_at": None}, NOW) is None
    assert stream_lag_seconds({"ch_published_at": ""}, NOW) is None


def test_garbage_never_raises():
    """Ingest must not 500 because a timestamp was malformed."""
    assert stream_lag_seconds({"ch_published_at": "not-a-date"}, NOW) is None


def test_clock_skew_cannot_produce_a_negative_lag():
    future = (NOW + timedelta(seconds=30)).isoformat()
    assert stream_lag_seconds({"ch_published_at": future}, NOW) == 0.0


# ==========================================
# BROKER — channels
# ==========================================
def test_high_value_leads_go_to_both_channels_others_only_to_all():
    async def scenario():
        b = _Broker()
        b.publish_incorp({"company_number": "1", "corporate_owner": True})  # high value
        b.publish_incorp({"company_number": "2"})                            # regular
        assert [e["company_number"] for e in b.snapshot("all")] == ["1", "2"]
        assert [e["company_number"] for e in b.snapshot("high_value")] == ["1"]
    asyncio.run(scenario())


def test_subscriber_only_hears_its_channel():
    async def scenario():
        b = _Broker()
        q_hv = b.subscribe("high_value")
        b.publish_incorp({"company_number": "reg"})            # not high value
        assert q_hv.empty()                                    # HV subscriber hears nothing
        b.publish_incorp({"company_number": "hv", "starting_capital": 99999})
        kind, ev = await q_hv.get()
        assert kind == "incorp" and ev["company_number"] == "hv"
    asyncio.run(scenario())


def test_phase_two_replaces_phase_one_in_place():
    """Two-phase ingest: the same company arrives bare then enriched. It must
    stay ONE tile, keep its position (so it doesn't jump under the AE's cursor),
    and end up with the enriched details."""
    async def scenario():
        b = _Broker()
        b.publish_incorp({"company_number": "A", "postcode": "EC1V 0AA"})   # phase 1
        b.publish_incorp({"company_number": "B"})                            # a later company
        b.publish_incorp({"company_number": "A", "postcode": "EC1V 0AA",
                          "director_last_name": "Smith"})                    # phase 2 of A
        snap = b.snapshot("all")
        assert [e["company_number"] for e in snap] == ["A", "B"]   # no duplicate, order held
        assert snap[0]["director_last_name"] == "Smith"            # enriched won
    asyncio.run(scenario())


def test_a_phase_two_that_gains_high_value_reaches_the_hv_channel():
    """Zone-1 is knowable at phase 1, but corporate ownership only appears after
    enrichment — such a lead must still arrive on the high-value channel."""
    async def scenario():
        b = _Broker()
        b.publish_incorp({"company_number": "C", "postcode": "M1 1AE"})      # not HV yet
        assert b.snapshot("high_value") == []
        b.publish_incorp({"company_number": "C", "postcode": "M1 1AE", "corporate_owner": True})
        assert [e["company_number"] for e in b.snapshot("high_value")] == ["C"]
    asyncio.run(scenario())


def test_buffer_caps_at_25_per_channel():
    async def scenario():
        b = _Broker()
        for i in range(40):
            b.publish_incorp({"company_number": str(i)})
        assert len(b.snapshot("all")) == MAX_ON_SCREEN
    asyncio.run(scenario())


# ==========================================
# BROKER — claims
# ==========================================
def test_claim_broadcasts_to_every_channel_and_marks_claimed():
    async def scenario():
        b = _Broker()
        b.publish_incorp({"company_number": "42", "corporate_owner": True})
        q_all, q_hv = b.subscribe("all"), b.subscribe("high_value")

        b.register_claim("42", "alice")
        for q in (q_all, q_hv):
            kind, data = await q.get()
            assert kind == "claim" and data == {"company_number": "42", "claimed_by": "alice"}

        # a NEW subscriber's replay shows it already claimed (grey-out survives)
        assert b.is_claimed("42") == "alice"
        snap = {e["company_number"]: e.get("claimed_by") for e in b.snapshot("all")}
        assert snap["42"] == "alice"
    asyncio.run(scenario())


def test_load_claims_seeds_the_set():
    b = _Broker()
    b.load_claims({"99": "bob"})
    assert b.is_claimed("99") == "bob"
    assert b.is_claimed("nope") is None


def test_sse_framing_incorp_vs_claim():
    incorp = _sse("incorp", {"company_number": "1"})
    assert incorp.startswith("data: ") and incorp.endswith("\n\n")
    claim = _sse("claim", {"company_number": "1", "claimed_by": "x"})
    assert claim.startswith("event: claim\ndata: ") and claim.endswith("\n\n")
