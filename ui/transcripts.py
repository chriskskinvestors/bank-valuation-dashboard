"""
Transcripts & Presentations sub-tab (SNL panel, docs/SNL-BUILD-PLAN.md §10).

Minimal and honest:
  • Investor presentations = 8-K EX-99 exhibits, reusing the Key Exhibits
    fetch (one EDGAR parse, two views).
  • Earnings-call transcripts: NOT wired — the FMP transcript endpoint is
    pending plan-tier verification (the Starter plan denies several
    endpoints). A labeled placeholder, never fake data.
"""
from __future__ import annotations

import streamlit as st

from data.bank_mapping import get_cik, get_name


def render_transcripts(ticker: str):
    cik = get_cik(ticker)
    if not cik:
        st.warning(f"No SEC CIK found for **{ticker}**. "
                   "This bank may not file with the SEC.")
        return

    st.markdown(f"**Transcripts & Presentations — {get_name(ticker) or ticker}** "
                f"({ticker})")

    # ── Investor presentations: 8-K EX-99 exhibits ───────────────────────
    st.markdown("#### Investor Presentations (8-K · EX-99)")
    st.caption("EX-99 exhibits attached to recent 8-K filings — earnings "
               "releases, investor decks and other furnished materials.")

    from ui.key_exhibits import fetch_key_exhibits, _exhibit_table
    with st.spinner("Scanning recent 8-K filing indexes on EDGAR..."):
        exhibits = fetch_key_exhibits(cik)

    decks = [e for e in exhibits
             if e["family"] == "Press Release / Presentation"
             and e["form"].startswith("8-K")]
    if decks:
        st.markdown(_exhibit_table(decks), unsafe_allow_html=True)
        st.caption(f"{len(decks)} EX-99 exhibit{'s' if len(decks) != 1 else ''} "
                   "from recent 8-Ks · source: SEC EDGAR filing indexes")
    else:
        st.info("No EX-99 exhibits found in this bank's recent 8-K filings, "
                "or EDGAR index pages could not be fetched.")

    # ── Transcripts: pending integration, clearly labeled ────────────────
    st.markdown("#### Earnings-Call Transcripts")
    st.info(
        "Transcript integration is pending: the FMP earnings-transcript "
        "endpoint requires plan-tier verification (the current Starter plan "
        "denies several endpoints). No transcript data is shown until that "
        "access is confirmed — this panel will never display placeholder "
        "transcripts."
    )
