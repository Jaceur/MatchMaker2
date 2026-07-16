"""SIC reference data: CSV parsing, code normalisation, and lead-string display.

Like the other suites here these are PURE — no database. sic_data imports the
shared `database` module at import time, so dummy connection settings are set
below before importing it; SQLAlchemy builds engines lazily, so nothing connects.
The DB-touching parts (load_sic_lookup) are deliberately not covered here.
"""
import os

os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("SUPABASE_HOST", "localhost")
os.environ.setdefault("SUPABASE_USER", "test")

import pytest  # noqa: E402

import sic_data  # noqa: E402
from sic_data import (  # noqa: E402
    CH_EXTRA_CODES,
    _normalise_code,
    describe_sic_codes,
    parse_sic_codes,
    read_sic_csv,
)


# ==========================================
# CODE NORMALISATION
# ==========================================
# The source CSV drops the leading zero on codes below 10000 ("1110"), but
# Companies House always reports them 5-digit zero-padded ("01110"). Without
# padding, every agriculture/mining lead silently fails to resolve.
@pytest.mark.parametrize("raw,expected", [
    ("1110", "01110"),      # 4-digit CSV form -> CH form
    ("5101", "05101"),
    ("62012", "62012"),     # already 5 digits, untouched
    ("99999", "99999"),
    (" 62012 ", "62012"),   # incidental whitespace
    (1110, "01110"),        # non-string input
])
def test_normalise_code(raw, expected):
    assert _normalise_code(raw) == expected


def test_normalise_leaves_non_numeric_alone():
    assert _normalise_code("None") == "None"


# ==========================================
# THE CSV
# ==========================================
def test_csv_codes_are_all_five_digits():
    assert {len(c) for c in read_sic_csv()} == {5}


def test_csv_has_full_list_with_groups():
    records = read_sic_csv()
    assert len(records) == 728
    # Every code carries a description and our business grouping.
    assert all(desc and section for desc, section in records.values())


def test_csv_low_code_is_padded_and_resolves():
    desc, section = read_sic_csv()["01110"]
    assert desc.startswith("Growing of cereals")
    assert section == "Agriculture, Forestry and Fishing"


def test_ch_extras_are_absent_from_the_official_csv():
    """The reason CH_EXTRA_CODES exists: Companies House issues 74990, 99999 and
    98000 outside the official SIC list, and all three appear on real leads
    (74990 alone was on 111 at the 2026-07-16 load). If a future CSV adds them,
    the extras become redundant rather than load-bearing."""
    records = read_sic_csv()
    for code in CH_EXTRA_CODES:
        assert code not in records


def test_ch_extras_cover_every_special_code():
    """Regression guard: 74990 was missed on the first pass and left 111 leads
    rendering a bare code. These are the full set CH issues outside SIC 2007."""
    assert set(CH_EXTRA_CODES) == {"74990", "98000", "99999"}


# ==========================================
# LEAD SIC STRINGS
# ==========================================
@pytest.mark.parametrize("raw,expected", [
    ("62012, 62020", ["62012", "62020"]),   # the ", ".join form sourcing.py writes
    ("62012,62020", ["62012", "62020"]),
    ("68209,68310,", ["68209", "68310"]),   # trailing comma
    ("01110", ["01110"]),                   # CH's own zero-padded form, untouched
    (None, []),
    ("", []),
])
def test_parse_sic_codes(raw, expected):
    assert parse_sic_codes(raw) == expected


def test_parse_does_not_pad_legacy_sic_2003_codes():
    """The CSV reader pads 4-digit codes, but lead data must NOT be padded: CH
    sends 5-digit SIC 2007, so a 4-digit code on a lead is a retired SIC 2003 one
    (7414/7487 are both live in the pool). Padding would invent a code — and
    SIC 2003 '1110' (crude petroleum) would pad into SIC 2007 '01110' (growing
    cereals) and render a confidently wrong description."""
    assert parse_sic_codes("7414") == ["7414"]
    assert parse_sic_codes("1110") == ["1110"]


def test_describe_sic_codes_keeps_order_and_resolves(monkeypatch):
    monkeypatch.setattr(sic_data, "get_sic_records", lambda: {
        "62012": {"description": "Business and domestic software development",
                  "section": "Software/Data"},
        "56302": {"description": "Public houses and bars",
                  "section": "Restaurants/Pubs/Food Service"},
    })
    # Order is Companies House's own — first listed is the primary code.
    assert describe_sic_codes("62012, 56302") == [
        {"code": "62012", "description": "Business and domestic software development",
         "section": "Software/Data"},
        {"code": "56302", "description": "Public houses and bars",
         "section": "Restaurants/Pubs/Food Service"},
    ]


def test_describe_sic_codes_keeps_unknown_codes(monkeypatch):
    """An unknown code still renders as a bare code on the card rather than
    vanishing — dropping it would hide the company's stated business."""
    monkeypatch.setattr(sic_data, "get_sic_records", lambda: {})
    assert describe_sic_codes("12345") == [
        {"code": "12345", "description": None, "section": None},
    ]
