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

from api.routers.new_incorps import (  # noqa: E402
    MAX_ON_SCREEN, _Broker, _sse, is_high_value, is_zone1,
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
    assert is_high_value({"starting_capital": 30000}) is True          # > £25k
    assert is_high_value({"corporate_owner": True}) is True            # corporate
    assert is_high_value({"postcode": "EC1V 0AA"}) is True             # zone 1
    # none of them:
    assert is_high_value({"starting_capital": 1000, "postcode": "M1 1AE"}) is False


def test_capital_threshold_is_strictly_above():
    assert is_high_value({"starting_capital": 25000}) is False
    assert is_high_value({"starting_capital": 25001}) is True


def test_capital_empty_or_garbage_is_safe():
    assert is_high_value({"starting_capital": ""}) is False
    assert is_high_value({"starting_capital": None}) is False
    assert is_high_value({"starting_capital": "n/a"}) is False


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
