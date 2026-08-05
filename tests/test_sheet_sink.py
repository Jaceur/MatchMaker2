"""The one-way Google Sheet feed: row mapping, tab routing, the flush policy and
failure isolation.

No HTTP and no DB — the sink's only outbound call (`_post`) is monkeypatched, so
these run anywhere. What they're really guarding is the promise ingest depends
on: **a broken sheet must never break the stream.**
"""
import asyncio
import os

os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("SUPABASE_HOST", "localhost")
os.environ.setdefault("SUPABASE_USER", "test")

import pytest  # noqa: E402

from api.config import settings  # noqa: E402
from api.routers.new_incorps import high_value_reasons  # noqa: E402
from api.sheet_sink import (  # noqa: E402
    ALL_FLUSH_SECONDS, HV_FLUSH_SECONDS, MAX_BATCH, MAX_PENDING,
    SheetSink, row_for, should_flush,
)


@pytest.fixture
def configured(monkeypatch):
    """A sink pointed at a fake webhook, with both tabs enabled."""
    monkeypatch.setattr(settings, "sheet_webhook_url", "https://script.example/exec")
    monkeypatch.setattr(settings, "sheet_webhook_key", "k" * 40)
    monkeypatch.setattr(settings, "sheet_send_all", True)
    monkeypatch.setattr(settings, "sheet_tab_all", "New Incorps")
    monkeypatch.setattr(settings, "sheet_tab_high_value", "High Value")
    return SheetSink()


EVENT = {
    "company_number": "12345678",
    "company_name": "ACME TRADING LTD",
    "date_of_creation": "2026-08-04",
    "sic_codes": ["62012", "62020"],
    "city": "London",
    "postcode": "EC1V 0AA",
    "starting_capital": 50000,
    "corporate_owner": True,
    "owner_name": "ACME HOLDINGS LTD",
    "director_first_name": "Jane",
    "director_last_name": "Smith",
    "director_other_companies": 3,
    "received_at": "2026-08-04T09:30:00+00:00",
}


# ==========================================
# ROW MAPPING
# ==========================================
def test_row_maps_the_fields_an_ae_needs():
    row = row_for(EVENT, high_value_reasons(EVENT))
    assert row["Company"] == "ACME TRADING LTD"
    assert row["Company number"] == "12345678"
    assert row["SIC codes"] == "62012, 62020"
    assert row["Starting capital"] == 50000
    assert row["Corporate owner"] == "Yes"
    assert row["Received"] == "2026-08-04 09:30:00"
    assert row["Companies House"].endswith("/company/12345678")
    assert "Jane%20Smith" in row["LinkedIn"]


def test_absent_capital_stays_blank_not_zero():
    """CHStream sends "" when there's no capital statement at all — writing 0
    would read as "incorporated with £0", which is a different fact."""
    assert row_for({"company_number": "1", "starting_capital": ""})["Starting capital"] == ""
    assert row_for({"company_number": "1"})["Starting capital"] == ""
    assert row_for({"company_number": "1", "starting_capital": "n/a"})["Starting capital"] == ""
    assert row_for({"company_number": "1", "starting_capital": "2500"})["Starting capital"] == 2500


def test_why_high_value_names_every_rule_that_fired():
    row = row_for(EVENT, high_value_reasons(EVENT))
    assert row["High value"] == "Yes"
    assert row["Why high value"] == (
        "Capital £50,000; Corporate owner (ACME HOLDINGS LTD); Zone 1 (EC1V)")


def test_regular_lead_is_not_marked_high_value():
    plain = {"company_number": "9", "postcode": "M1 1AE", "starting_capital": 100}
    row = row_for(plain, high_value_reasons(plain))
    assert row["High value"] == "" and row["Why high value"] == ""


def test_row_never_contains_the_ae_owned_columns():
    """The AEs' own columns are the one-way boundary — we must not send keys that
    could overwrite what they typed."""
    row = row_for(EVENT, [])
    for owned in ("Claimed by", "Status", "Notes"):
        assert owned not in row


# ==========================================
# TAB ROUTING
# ==========================================
def test_high_value_goes_to_both_tabs(configured):
    configured.enqueue(EVENT, high_value_reasons(EVENT))
    tabs = [item[1] for item in configured._pending]
    assert tabs == ["New Incorps", "High Value"]


def test_regular_goes_only_to_the_all_tab(configured):
    plain = {"company_number": "9", "postcode": "M1 1AE"}
    configured.enqueue(plain, high_value_reasons(plain))
    assert [item[1] for item in configured._pending] == ["New Incorps"]


def test_send_all_off_keeps_only_high_value(configured, monkeypatch):
    monkeypatch.setattr(settings, "sheet_send_all", False)
    plain = {"company_number": "9", "postcode": "M1 1AE"}
    configured.enqueue(plain, high_value_reasons(plain))
    assert list(configured._pending) == []
    configured.enqueue(EVENT, high_value_reasons(EVENT))
    assert [item[1] for item in configured._pending] == ["High Value"]


def test_disabled_sink_queues_nothing():
    """No SHEET_WEBHOOK_URL = the feed is simply off (the default everywhere it
    hasn't been configured)."""
    sink = SheetSink()
    sink.enqueue(EVENT, high_value_reasons(EVENT))
    assert list(sink._pending) == [] and sink.stats["queued"] == 0


def test_the_same_company_is_only_sent_once(configured):
    """CH re-sends a company on every update; CHStream's own seen-set resets on
    restart. Two identical events must not become two rows."""
    configured.enqueue(EVENT, high_value_reasons(EVENT))
    configured.enqueue(EVENT, high_value_reasons(EVENT))
    assert len(configured._pending) == 2      # one per tab, not four


# ==========================================
# FLUSH POLICY
# ==========================================
def test_nothing_pending_never_flushes():
    assert should_flush(0, 9999, True) is False


def test_high_value_flushes_fast_regular_waits():
    """The whole point of the two tiers: high value is a first-to-contact race."""
    assert should_flush(1, HV_FLUSH_SECONDS, True) is True
    assert should_flush(1, HV_FLUSH_SECONDS, False) is False
    assert should_flush(1, ALL_FLUSH_SECONDS, False) is True


def test_a_full_batch_flushes_immediately():
    assert should_flush(MAX_BATCH, 0, False) is True


def test_backlog_is_capped(configured):
    for i in range(MAX_PENDING + 50):
        configured.enqueue({"company_number": str(i)}, [])
    assert len(configured._pending) <= MAX_PENDING
    assert configured.stats["dropped"] >= 50


# ==========================================
# SENDING / FAILURE ISOLATION
# ==========================================
def _flush(sink):
    asyncio.run(sink._flush())


def test_flush_posts_one_batch_grouped_by_tab(configured):
    sent = {}

    def fake_post(batch):
        sent.update(batch)
        return True, None

    configured._post = fake_post
    configured.enqueue(EVENT, high_value_reasons(EVENT))
    _flush(configured)

    assert set(sent) == {"New Incorps", "High Value"}
    assert sent["High Value"][0]["Company number"] == "12345678"
    assert configured.stats["sent"] == 2 and not configured._pending


def test_a_failed_batch_is_recorded_not_retried_forever(configured):
    configured._post = lambda batch: (False, "HTTP 500")
    configured.enqueue(EVENT, high_value_reasons(EVENT))
    _flush(configured)
    assert configured.stats["failed_batches"] == 1
    assert configured.stats["last_error"] == "HTTP 500"
    assert not configured._pending          # dropped — stale leads aren't replayed


def test_enqueue_never_raises_even_on_a_broken_event(configured):
    """Ingest calls this inline. It must swallow everything."""
    configured.enqueue(None, [])            # type: ignore[arg-type]
    assert configured.stats["last_error"].startswith("enqueue:")


def test_post_refuses_without_a_shared_key(configured, monkeypatch):
    """A published Apps Script URL is public — never post to one unauthenticated."""
    monkeypatch.setattr(settings, "sheet_webhook_key", "")
    ok, error = configured._post({"New Incorps": [row_for(EVENT, [])]})
    assert ok is False and "SHEET_WEBHOOK_KEY" in error


def test_status_reports_the_feed_health(configured):
    configured.enqueue(EVENT, high_value_reasons(EVENT))
    status = configured.status()
    assert status["enabled"] is True and status["pending"] == 2
    assert status["queued"] == 2 and status["last_error"] is None
