"""The "why now" filing triggers (enrichment.fetch_filing_triggers).

The CH HTTP layer is mocked, so these run offline and pin the PARSING against
real Companies House response shapes (the same fixtures the CH engine tests use).
Live verification happens on deploy — the local CH key is currently dead (401),
which is itself why the fail-safe test below matters.
"""
import os
from datetime import date, datetime, timedelta

os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("SUPABASE_HOST", "localhost")
os.environ.setdefault("SUPABASE_USER", "test")

import enrichment  # noqa: E402


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def _mock_ch(monkeypatch, *, filing=None, charges=None):
    """Route CH GETs to canned (status, json) responses by URL."""
    def fake_get(url, **kwargs):
        if "filing-history" in url:
            return _Resp(*(filing or (404, {})))
        if "charges" in url:
            return _Resp(*(charges or (404, {})))
        return _Resp(404, {})
    monkeypatch.setattr(enrichment.requests, "get", fake_get)


def test_sh01_date_extracted_from_real_shape(monkeypatch, load_fixture):
    """SH01 date parsed from the real capital filing-history shape."""
    _mock_ch(monkeypatch, filing=(200, load_fixture("filing_history_sh01_eur.json")))
    t = enrichment.fetch_filing_triggers("X")
    assert t["last_capital_raise"] == date(2026, 7, 3)
    assert t["last_charge"] is None
    assert t["charge_recent"] is False


def test_no_capital_filing_means_no_raise(monkeypatch, load_fixture):
    _mock_ch(monkeypatch, filing=(200, load_fixture("filing_history_no_capital.json")))
    t = enrichment.fetch_filing_triggers("X")
    assert t["last_capital_raise"] is None
    assert t["capital_raise_recent"] is False


def test_mr01_charge_extracted_and_most_recent_wins(monkeypatch):
    charges = {"items": [
        {"created_on": "2024-01-10", "status": "outstanding"},
        {"created_on": "2026-06-30", "status": "outstanding"},  # newest
    ]}
    _mock_ch(monkeypatch, charges=(200, charges))
    t = enrichment.fetch_filing_triggers("X")
    assert t["last_charge"] == date(2026, 6, 30)


def test_recent_flag_is_a_window(monkeypatch):
    """Recency is TRIGGER_RECENT_DAYS wide — test with dates relative to now so
    it never rots as the calendar moves."""
    fresh = (datetime.now().date() - timedelta(days=10)).isoformat()
    stale = (datetime.now().date() - timedelta(days=enrichment.TRIGGER_RECENT_DAYS + 30)).isoformat()

    _mock_ch(monkeypatch, filing=(200, {"items": [{"type": "SH01", "date": fresh}]}))
    assert enrichment.fetch_filing_triggers("X")["capital_raise_recent"] is True

    _mock_ch(monkeypatch, filing=(200, {"items": [{"type": "SH01", "date": stale}]}))
    t = enrichment.fetch_filing_triggers("X")
    assert t["last_capital_raise"] == date.fromisoformat(stale)  # date still recorded
    assert t["capital_raise_recent"] is False                    # but not "recent"


def test_capital_allotment_by_description_not_just_type(monkeypatch):
    """Some SH01s arrive typed oddly but describe a capital-allotment — the CH
    engine keys off both, and so do we."""
    _mock_ch(monkeypatch, filing=(200, {"items": [
        {"type": "OTHER", "description": "capital-allotment-shares", "date": "2026-07-01"},
    ]}))
    assert enrichment.fetch_filing_triggers("X")["last_capital_raise"] == date(2026, 7, 1)


def test_a_dead_key_or_api_error_is_safe(monkeypatch):
    """A 401 (exactly today's local situation) or a network blow-up must yield
    all-None, never raise — enrichment can't break on a trigger lookup."""
    _mock_ch(monkeypatch, filing=(401, {}), charges=(401, {}))
    t = enrichment.fetch_filing_triggers("X")
    assert t == {"last_capital_raise": None, "capital_raise_recent": False,
                 "last_charge": None, "charge_recent": False}

    def boom(url, **kwargs):
        raise ConnectionError("network down")
    monkeypatch.setattr(enrichment.requests, "get", boom)
    t = enrichment.fetch_filing_triggers("X")  # must not raise
    assert t["capital_raise_recent"] is False and t["charge_recent"] is False
