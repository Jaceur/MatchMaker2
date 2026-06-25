"""SIC code reference data + loader.

SIC (Standard Industrial Classification) codes are what Companies House uses to
describe a company's nature of business. The full condensed SIC 2007 list has
~730 codes; this seeds the common ones into the sic_lookup table. Add more
entries here (or bulk-load the official gov.uk CSV) and re-run the loader — the
swipe card shows the description for any code it knows, and the raw code otherwise.
"""
import streamlit as st
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from database import engine
from models import sic_lookup

SIC_DESCRIPTIONS = {
    "01450": "Raising of sheep and goats",
    "10710": "Manufacture of bread and fresh pastry goods and cakes",
    "16290": "Manufacture of other products of wood, cork, straw and plaiting materials",
    "18120": "Printing (other than printing of newspapers)",
    "25620": "Machining",
    "31090": "Manufacture of other furniture",
    "33120": "Repair of machinery",
    "41100": "Development of building projects",
    "41201": "Construction of commercial buildings",
    "41202": "Construction of domestic buildings",
    "43210": "Electrical installation",
    "43220": "Plumbing, heat and air-conditioning installation",
    "43290": "Other construction installation",
    "43310": "Plastering",
    "43320": "Joinery installation",
    "43330": "Floor and wall covering",
    "43341": "Painting",
    "43390": "Other building completion and finishing",
    "43999": "Other specialised construction activities not elsewhere classified",
    "45112": "Sale of used cars and light motor vehicles",
    "45200": "Maintenance and repair of motor vehicles",
    "46900": "Non-specialised wholesale trade",
    "47110": "Retail sale in non-specialised stores with food, beverages or tobacco predominating",
    "47190": "Other retail sale in non-specialised stores",
    "47910": "Retail sale via mail order houses or via Internet",
    "47990": "Other retail sale not in stores, stalls or markets",
    "49410": "Freight transport by road",
    "55100": "Hotels and similar accommodation",
    "56101": "Licensed restaurants",
    "56103": "Take-away food shops and mobile food stands",
    "56210": "Event catering activities",
    "56302": "Public houses and bars",
    "59112": "Video production activities",
    "62012": "Business and domestic software development",
    "62020": "Information technology consultancy activities",
    "62090": "Other information technology service activities",
    "63110": "Data processing, hosting and related activities",
    "64209": "Activities of other holding companies not elsewhere classified",
    "68100": "Buying and selling of own real estate",
    "68209": "Other letting and operating of own or leased real estate",
    "68310": "Real estate agencies",
    "68320": "Management of real estate on a fee or contract basis",
    "69109": "Activities of legal practice not elsewhere classified",
    "69201": "Accounting and auditing activities",
    "69202": "Bookkeeping activities",
    "69203": "Tax consultancy",
    "70210": "Public relations and communications activities",
    "70221": "Financial management",
    "70229": "Management consultancy activities other than financial management",
    "71111": "Architectural activities",
    "71121": "Engineering design activities for industrial process and production",
    "71122": "Engineering related scientific and technical consulting activities",
    "73110": "Advertising agencies",
    "74100": "Specialised design activities",
    "74201": "Portrait photographic activities",
    "74909": "Other professional, scientific and technical activities not elsewhere classified",
    "77110": "Renting and leasing of cars and light motor vehicles",
    "78109": "Other activities of employment placement agencies",
    "81210": "General cleaning of buildings",
    "81221": "Window cleaning services",
    "82990": "Other business support service activities not elsewhere classified",
    "85590": "Other education not elsewhere classified",
    "86900": "Other human health activities",
    "90030": "Artistic creation",
    "93110": "Operation of sports facilities",
    "93120": "Activities of sport clubs",
    "93130": "Fitness facilities",
    "96020": "Hairdressing and other beauty treatment",
    "96090": "Other service activities not elsewhere classified",
    "98000": "Residents property management",
    "99999": "Dormant company",
}


def load_sic_lookup():
    """Upsert the seed SIC descriptions into the sic_lookup table. Idempotent."""
    rows = [{"code": c, "description": d} for c, d in SIC_DESCRIPTIONS.items()]
    if not rows:
        return 0
    stmt = pg_insert(sic_lookup).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["code"],
        set_={"description": stmt.excluded.description},
    )
    with engine.begin() as conn:
        conn.execute(stmt)
    return len(rows)


@st.cache_data(ttl=3600)
def get_sic_lookup():
    """code -> description, read from the table and cached for the session."""
    try:
        with engine.connect() as conn:
            rows = conn.execute(select(sic_lookup)).mappings().fetchall()
        return {r["code"]: r["description"] for r in rows}
    except Exception:
        return dict(SIC_DESCRIPTIONS)   # fall back to the seed if the table read fails
