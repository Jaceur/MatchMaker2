"""Shared lead-card UI — the Tinder-style profile block used by both the swipe
page and the My Pipeline classify cards, so the two views stay consistent.

Takes a lead dict (any mapping with the sales_leads columns) and renders the
banner, chips and metric tiles. No DB or page-specific state.
"""
import streamlit as st

from scoring import account_tier
from sic_data import describe_sic_codes


def fmt_money(v):
    """£1,234,567 / -£15,400 / — (when None)."""
    if v is None:
        return "—"
    return f"-£{abs(v):,}" if v < 0 else f"£{v:,}"


def _size_chip(account_type):
    """(label, chip-class) for the company-size tag, using the scorer's shared
    account_tier so the card and the score always agree (small/medium = target
    market, green; micro amber; larger neutral; dormant red)."""
    t = (account_type or "").lower()
    if not t:
        return ("Size n/a", "")
    if "dormant" in t:
        return ("Dormant", "no")
    tier = account_tier(account_type)           # micro / small / large / unknown
    if tier == "micro":
        return ("Micro", "warn")
    if tier == "small":
        return ("Medium", "ok") if "medium" in t else ("Small", "ok")
    if tier == "large":
        return ("Large", "")
    return (account_type.title(), "")


CARD_CSS = """
<style>
.mm-banner {
  background: linear-gradient(135deg, #2a2a2e 0%, #45454d 50%, #303036 100%);
  color: #f4f4f6; padding: 20px 24px; border-radius: 16px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.10), inset 0 -1px 0 rgba(0,0,0,.25);
}
.mm-banner .mm-name { font-size: 1.5rem; font-weight: 800; line-height: 1.15; color:#fff; }
.mm-banner .mm-sub  { opacity: .85; font-size: .88rem; margin-top: 6px; }
.mm-chips { margin: 12px 0 18px 0; }
.mm-chip {
  display: inline-block; background: #eef0f4; color: #3a3f47;
  border-radius: 999px; padding: 4px 12px; margin: 6px 6px 0 0;
  font-size: .82rem; font-weight: 600;
}
.mm-chip.ok   { background: #e6f7ed; color: #137a3b; }
.mm-chip.no   { background: #fdebec; color: #b02230; }
.mm-chip.warn { background: #fff3e0; color: #ad6800; }
.mm-label {
  font-size: .72rem; font-weight: 700; letter-spacing: .07em;
  text-transform: uppercase; color: #9097a1; margin: 8px 0 2px 2px;
}
</style>
"""


def render_profile(lead):
    """Render the read-only profile: name banner, chips (Size · Import · Export ·
    Director change) and metric tiles (score, size, financials)."""
    st.markdown(CARD_CSS, unsafe_allow_html=True)

    st.markdown(
        "<div class='mm-banner'>"
        f"<div class='mm-name'>🏢 {lead['company_name']}</div>"
        f"<div class='mm-sub'>📅 Incorporated {lead['incorporation_date']} · 🆔 {lead['crn']}</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    size_label, size_cls = _size_chip(lead.get('account_type'))
    imp_ok = lead.get('import_activity')
    exp_ok = lead.get('export_activity')
    chips = [
        f"<span class='mm-chip {size_cls}'>🏢 {size_label}</span>",
        f"<span class='mm-chip {'ok' if imp_ok else 'no'}'>{'✅' if imp_ok else '❌'} Import</span>",
        f"<span class='mm-chip {'ok' if exp_ok else 'no'}'>{'✅' if exp_ok else '❌'} Export</span>",
    ]
    if lead.get('director_change_recent'):
        chips.append(
            "<span class='mm-chip warn'>🔄 Director change "
            f"{lead.get('last_director_change')}</span>"
        )
    st.markdown(f"<div class='mm-chips'>{''.join(chips)}</div>", unsafe_allow_html=True)

    score = lead.get('confidence_score') or 0
    lead_score = lead.get('lead_score') or 0
    emp = lead.get('employee_count')
    m1, m2, m3 = st.columns(3)
    m1.metric("🎯 Lead Score", f"{lead_score}/100", help=f"Data confidence: {score}%")
    m2.metric("👥 Employees", emp if emp is not None else "—")
    m3.metric("💷 Turnover", fmt_money(lead.get('turnover')))

    st.markdown("<div class='mm-label'>Financials — from filed accounts</div>",
                unsafe_allow_html=True)
    f1, f2, f3 = st.columns(3)
    f1.metric("🏦 Cash at bank", fmt_money(lead.get('cash_at_bank')))
    f2.metric("📥 Trade debtors", fmt_money(lead.get('trade_debtors')))
    f3.metric("📤 Trade creditors", fmt_money(lead.get('trade_creditors')))
    g1, g2, g3 = st.columns(3)
    g1.metric("🧾 Admin expenses", fmt_money(lead.get('admin_expenses')))
    g2.metric("🏛️ Bank loans", fmt_money(lead.get('bank_loans_overdrafts')))
    g3.metric("💱 FX gain/loss", fmt_money(lead.get('foreign_exchange')))

    # Nature of business — SIC codes with their Companies House descriptions.
    codes = describe_sic_codes(lead.get('sic_codes'))
    if codes:
        st.markdown("<div class='mm-label'>Nature of business — SIC codes</div>",
                    unsafe_allow_html=True)
        for c in codes:
            st.markdown(
                f"<div style='font-size:.85rem; margin-top:2px'>🏭 <b>{c['code']}</b> — "
                f"{c['description'] or 'description not loaded'}</div>",
                unsafe_allow_html=True,
            )
