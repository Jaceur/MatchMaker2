"""Capital parsing — the messiest CH data. Statement of capital hides in
description_values.capital, sometimes only on an incorporation bundle's
associated_filings, with figures as comma'd strings."""
from decimal import Decimal

import ch_signals


def test_newinc_capital_found_via_associated_filings(load_fixture):
    rows = ch_signals.extract_capital_statements(load_fixture("filing_history_newinc.json"))
    assert len(rows) == 1
    row = rows[0]
    assert row["filing_type"] == "NEWINC"          # attributed to the bundle
    assert row["currency"] == "GBP"
    assert row["figure"] == Decimal("50000")       # '50,000' parsed
    assert row["filing_date"] == "2026-06-30"


def test_sh01_multi_currency_capital(load_fixture):
    rows = ch_signals.extract_capital_statements(load_fixture("filing_history_sh01_eur.json"))
    assert len(rows) == 2
    by_ccy = {r["currency"]: r["figure"] for r in rows}
    assert by_ccy["EUR"] == Decimal("100000")
    assert by_ccy["GBP"] == Decimal("12000.50")
    assert all(r["filing_type"] == "SH01" for r in rows)


def test_missing_capital_yields_no_rows(load_fixture):
    rows = ch_signals.extract_capital_statements(load_fixture("filing_history_no_capital.json"))
    assert rows == []


def test_empty_and_none_filing_history():
    assert ch_signals.extract_capital_statements(None) == []
    assert ch_signals.extract_capital_statements({"items": []}) == []


def test_summarise_capital_best_gbp_and_foreign_flag(load_fixture):
    rows = (ch_signals.extract_capital_statements(load_fixture("filing_history_newinc.json"))
            + ch_signals.extract_capital_statements(load_fixture("filing_history_sh01_eur.json")))
    best_gbp, foreign = ch_signals.summarise_capital(rows)
    assert best_gbp == Decimal("50000")            # the larger of the two GBP figures
    assert foreign is True                         # the EUR line


def test_summarise_capital_with_nothing():
    assert ch_signals.summarise_capital([]) == (None, False)


def test_figure_parsing_tolerates_junk():
    assert ch_signals._parse_figure("1,000") == Decimal("1000")
    assert ch_signals._parse_figure("50000.00") == Decimal("50000.00")
    assert ch_signals._parse_figure(None) is None
    assert ch_signals._parse_figure("not a number") is None
