"""The "My Pipeline" sheet tab: turning a lead into rows an AE can work from.

Pure mapping — no DB, no HTTP. The rules worth protecting are that every lead
produces at least one row (an invisible lead is worse than an ugly one), that the
email cells open Mailmeteor while still reading as plain addresses, and that the
row key stays stable so a re-sync updates rather than duplicates.
"""
import os

os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("SUPABASE_HOST", "localhost")
os.environ.setdefault("SUPABASE_USER", "test")

import pytest  # noqa: E402

from api.config import settings  # noqa: E402
from api.sheet_pipeline import (  # noqa: E402
    AE_COLUMNS, CRM_STATUS_OPTIONS, KEY_COLUMN, MAX_EMAILS, PIPELINE_COLUMNS,
    PipelineSync, rows_for_lead, tab_for,
)

LEAD = {
    "id": 42,
    "crn": "12345678",
    "company_name": "ACME TRADING LTD",
    "lead_score": 61,
    "website_url": "https://www.acme.co.uk/about",
    "linkedin_url": "https://www.linkedin.com/company/acme",
    "directors_info": [
        {"name": "Jane Smith", "appointments": 3,
         "url": "https://find-and-update.company-information.service.gov.uk/officers/abc/appointments"},
        {"name": "Robert Jones", "appointments": 1, "url": None},
    ],
    "active_directors": "Jane Smith, Robert Jones",
    "capital_raise_recent": True,
    "last_capital_raise": "2026-07-20",
    "charge_recent": False,
    "last_charge": None,
}


def test_one_row_per_director():
    """Five email guesses are per PERSON, so a two-director lead needs two rows."""
    rows = rows_for_lead(LEAD)
    assert [r["Director"] for r in rows] == ["Jane Smith", "Robert Jones"]


def test_lead_columns_repeat_on_every_row():
    """Each row has to stand on its own — sorting or filtering the tab must not
    strand a director from their company."""
    for row in rows_for_lead(LEAD):
        assert row["Company"] == "ACME TRADING LTD"
        assert row["Company number"] == "12345678"
        assert row["Companies House"].endswith("/company/12345678")
        assert row["Fit score"] == 61


def test_a_lead_with_no_directors_still_gets_a_row():
    """A lead you can't see is worse than a lead with a blank director."""
    rows = rows_for_lead({**LEAD, "directors_info": [], "active_directors": ""})
    assert len(rows) == 1
    assert rows[0]["Director"] == ""
    assert rows[0]["Company"] == "ACME TRADING LTD"


def test_emails_are_the_five_guesses_linked_to_mailmeteor():
    row = rows_for_lead(LEAD)[0]
    assert row["Email 1"] == (
        '=HYPERLINK("https://mailmeteor.com/email-checker?email=jane.smith@acme.co.uk",'
        '"jane.smith@acme.co.uk")')
    # All five patterns, in the app's order (most popular first).
    assert all(row[f"Email {i}"] for i in range(1, MAX_EMAILS + 1))


def test_no_website_means_no_email_guesses_not_broken_ones():
    row = rows_for_lead({**LEAD, "website_url": None})[0]
    assert [row[f"Email {i}"] for i in range(1, MAX_EMAILS + 1)] == [""] * MAX_EMAILS
    assert row["Business search"] == ""


def test_business_search_shows_the_bare_domain_but_links_to_the_site():
    """The cell has to COPY as 'acme.co.uk' (that's what Salesforce's Business
    Search wants) while still being clickable."""
    row = rows_for_lead(LEAD)[0]
    assert row["Business search"] == '=HYPERLINK("https://www.acme.co.uk/about","acme.co.uk")'


def test_a_schemeless_website_still_links():
    """Real data, 2026-08-05: some stored URLs are bare domains. HYPERLINK reads
    a schemeless string as a RELATIVE link, so the cell would be dead."""
    row = rows_for_lead({**LEAD, "website_url": "greatfashion.co.uk"})[0]
    assert row["Business search"] == '=HYPERLINK("https://greatfashion.co.uk","greatfashion.co.uk")'


def test_other_companies_excludes_this_one():
    """The card says "2 other companies" for 3 appointments — one of them is this
    lead."""
    rows = rows_for_lead(LEAD)
    assert rows[0]["Other companies"] == 2
    assert rows[1]["Other companies"] == 0


def test_why_now_carries_the_opener():
    assert rows_for_lead(LEAD)[0]["Why now"] == "Raised capital (2026-07-20)"
    quiet = rows_for_lead({**LEAD, "capital_raise_recent": False})
    assert quiet[0]["Why now"] == ""


def test_row_key_is_stable_and_unique_per_director():
    """The key is what makes a re-sync an UPDATE instead of a duplicate."""
    first, second = rows_for_lead(LEAD)
    assert first[KEY_COLUMN] == "12345678|jane smith"
    assert first[KEY_COLUMN] != second[KEY_COLUMN]
    assert rows_for_lead(LEAD)[0][KEY_COLUMN] == first[KEY_COLUMN]


def test_falls_back_to_the_plain_director_names():
    """Leads enriched before directors_info existed only have the names string."""
    rows = rows_for_lead({**LEAD, "directors_info": None})
    assert [r["Director"] for r in rows] == ["Jane Smith", "Robert Jones"]
    assert rows[0]["Other companies"] == ""       # unknown, not zero


def test_quotes_in_a_company_name_cannot_break_the_formula():
    row = rows_for_lead({**LEAD, "website_url": 'https://a.com/"x'})[0]
    assert row["Business search"].count('""') >= 1     # escaped, not terminated


def test_rows_never_contain_the_ae_columns():
    """The one-way boundary: Outcome and Notes are yours, so we must not send
    keys that could overwrite them."""
    for row in rows_for_lead(LEAD):
        for owned in AE_COLUMNS:
            assert owned not in row


def test_every_row_key_is_a_declared_column():
    """A key with no column is silently dropped by the Apps Script."""
    for row in rows_for_lead(LEAD):
        assert set(row) <= set(PIPELINE_COLUMNS)


# ==========================================
# SYNC CONFIG
# ==========================================
def test_sync_is_off_without_users(monkeypatch):
    monkeypatch.setattr(settings, "sheet_webhook_url", "https://script.example/exec")
    monkeypatch.setattr(settings, "sheet_webhook_key", "k" * 40)
    monkeypatch.setattr(settings, "sheet_pipeline_users", "")
    assert PipelineSync().enabled is False


def test_sync_needs_the_webhook_too(monkeypatch):
    """Same rule as the feed: half-configured is OFF, not on."""
    monkeypatch.setattr(settings, "sheet_webhook_url", "https://script.example/exec")
    monkeypatch.setattr(settings, "sheet_webhook_key", "")
    monkeypatch.setattr(settings, "sheet_pipeline_users", "josh")
    assert PipelineSync().enabled is False


def test_one_user_gets_the_plain_tab_several_get_one_each(monkeypatch):
    monkeypatch.setattr(settings, "sheet_tab_pipeline", "My Pipeline")
    assert tab_for("josh", ["josh"]) == "My Pipeline"
    assert tab_for("josh", ["josh", "emma"]) == "My Pipeline - josh"


def test_payload_declares_sync_mode_and_the_dropdown(monkeypatch):
    monkeypatch.setattr(settings, "sheet_pipeline_users", "josh")
    payload = PipelineSync()._payload({"My Pipeline": []})
    assert payload["mode"] == "sync"
    assert payload["keyColumn"] == KEY_COLUMN
    assert payload["validation"]["Outcome"] == CRM_STATUS_OPTIONS
    assert payload["presence"] == {"column": "In pipeline", "absent": "Left"}
    assert payload["aeColumns"] == AE_COLUMNS


@pytest.mark.parametrize("status", CRM_STATUS_OPTIONS)
def test_won_is_not_offered(status):
    """Retired for GDPR — it must not creep back in via the sheet dropdown."""
    assert status != "Won"
