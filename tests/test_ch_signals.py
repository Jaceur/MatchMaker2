"""Signal detection against recorded Companies House JSON fixtures."""
from datetime import date

import ch_signals


# --- PSC (foreign-parent signal) -------------------------------------------

def test_foreign_corporate_psc(load_fixture):
    foreign, uk = ch_signals.psc_signals(load_fixture("psc_foreign_corp.json"))
    assert foreign is True
    assert uk is False


def test_uk_corporate_psc_and_individuals_ignored(load_fixture):
    # Fixture has a UK corporate PSC and an individual — only the corporate
    # one counts, and it counts as UK.
    foreign, uk = ch_signals.psc_signals(load_fixture("psc_uk_corp.json"))
    assert foreign is False
    assert uk is True


def test_missing_psc_is_neutral():
    assert ch_signals.psc_signals(None) == (False, False)
    assert ch_signals.psc_signals({"items": []}) == (False, False)


def test_uk_country_detection():
    for uk_name in ("United Kingdom", "england", "SCOTLAND", "Wales",
                    "Northern Ireland", "England And Wales", None, ""):
        assert ch_signals.is_uk_country(uk_name) is True
    for foreign in ("Germany", "United States", "Jersey", "Ireland"):
        assert ch_signals.is_uk_country(foreign) is False


# --- Officers ----------------------------------------------------------------

def test_active_director_count_excludes_resigned_and_secretaries(load_fixture):
    directors = ch_signals.active_directors(load_fixture("officers_two_directors.json"))
    assert len(directors) == 2


def test_foreign_correspondence_officer(load_fixture):
    officers = load_fixture("officers_two_directors.json")
    assert ch_signals.has_foreign_correspondence_officer(officers) is True
    # Drop the German director: no foreign correspondence left.
    officers["items"] = [o for o in officers["items"] if "MULLER" not in o["name"]]
    assert ch_signals.has_foreign_correspondence_officer(officers) is False


def test_officer_id_parsed_from_appointments_link(load_fixture):
    first = load_fixture("officers_two_directors.json")["items"][0]
    assert ch_signals.officer_id_from_item(first) == "aBcD123eFgH"
    assert ch_signals.officer_id_from_item({"links": {}}) is None


# --- SIC ---------------------------------------------------------------------

def test_target_sic(load_fixture):
    target, passive = ch_signals.sic_flags(load_fixture("profile_target.json")["sic_codes"])
    assert target is True
    assert passive is False


def test_passive_sic_only(load_fixture):
    target, passive = ch_signals.sic_flags(load_fixture("profile_holding.json")["sic_codes"])
    assert target is False
    assert passive is True


def test_passive_plus_trading_sic_is_not_passive_only():
    _, passive = ch_signals.sic_flags(["64209", "46210"])
    assert passive is False


def test_missing_sic_is_neutral():
    assert ch_signals.sic_flags(None) == (False, False)
    assert ch_signals.sic_flags([]) == (False, False)
    assert ch_signals.sic_flags(["None Supplied"]) == (False, False)


# --- Address normalisation + hot addresses -----------------------------------

def test_normalise_address_drops_units_and_punctuation():
    a = ch_signals.normalise_address("Suite 4B, 71-75 Shelton Street, Covent Garden, London WC2H 9JQ")
    b = ch_signals.normalise_address("71-75 SHELTON STREET COVENT GARDEN LONDON WC2H 9JQ")
    assert a == b


def test_normalise_address_drops_ordinal_floors():
    a = ch_signals.normalise_address("3rd Floor, 86-90 Paul Street, London, EC2A 4NE")
    b = ch_signals.normalise_address("86-90 Paul Street, London, EC2A 4NE")
    assert a == b


def test_formation_agent_fixture_hits_seed_list(load_fixture):
    profile = load_fixture("profile_holding.json")
    key = ch_signals.normalise_address(profile["registered_office_address"])
    assert key in ch_signals.SEED_HOT_ADDRESSES


def test_normal_address_not_hot(load_fixture):
    profile = load_fixture("profile_target.json")
    key = ch_signals.normalise_address(profile["registered_office_address"])
    assert key not in ch_signals.SEED_HOT_ADDRESSES


def test_normalise_address_accepts_dict_and_none():
    assert ch_signals.normalise_address(None) == ""
    assert "MANCHESTER" in ch_signals.normalise_address(
        {"address_line_1": "1 Test Way", "locality": "Manchester"})


# --- Serial-director patterns -------------------------------------------------

def test_spv_farm_detected(load_fixture):
    appts = load_fixture("appointments_spv_farm.json")
    prior = ch_signals.prior_appointments(appts, "16000001")
    assert len(prior) == 12                      # current company excluded
    assert ch_signals.is_spv_farm(prior) is True


def test_normal_history_is_not_spv_farm(load_fixture):
    appts = load_fixture("appointments_normal.json")
    prior = ch_signals.prior_appointments(appts, "16000001")
    assert ch_signals.is_spv_farm(prior) is False


def test_quality_company_check():
    as_of = date(2026, 7, 6)
    good = {"company_status": "active", "date_of_creation": "2015-04-01",
            "accounts": {"last_accounts": {"type": "full"}}}
    assert ch_signals.is_quality_company(good, as_of) is True

    micro = dict(good, accounts={"last_accounts": {"type": "micro-entity"}})
    assert ch_signals.is_quality_company(micro, as_of) is False

    too_young = dict(good, date_of_creation="2025-01-01")
    assert ch_signals.is_quality_company(too_young, as_of) is False

    dissolved = dict(good, company_status="dissolved")
    assert ch_signals.is_quality_company(dissolved, as_of) is False

    assert ch_signals.is_quality_company(None, as_of) is False
