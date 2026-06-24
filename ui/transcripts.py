"""
Transcripts & Presentations sub-tab (SNL panel, docs/SNL-BUILD-PLAN.md §10).

  • Investor presentations = 8-K EX-99 exhibits, reusing the Key Exhibits
    fetch (one EDGAR parse, two views).
  • Earnings-call transcripts = FMP full-text transcripts (endpoint verified
    in-plan on the Premium key 2026-06-24): a quarter picker over the available
    calls, rendering the full call text with speaker turns. Empty/honest when a
    bank has no coverage.
"""
from __future__ import annotations

import html as _h
import re

import streamlit as st

from data.bank_mapping import get_cik, get_name
from data.fmp_transcripts import get_transcript_dates, get_transcript
from ui.chrome import title_bar

# A line that begins with "Speaker Name:" — bold the name, keep the rest inline.
_SPEAKER_RE = re.compile(r"^([A-Z][^:]{1,60}?):\s+(.*)$")


def _transcript_body_html(content: str) -> str:
    """Format the raw transcript text into readable paragraphs, bolding the
    leading "Speaker:" on each turn. Everything is HTML-escaped first."""
    paras = []
    for raw in content.split("\n"):
        line = raw.strip()
        if not line:
            continue
        m = _SPEAKER_RE.match(line)
        if m:
            paras.append(
                f'<p style="margin:0 0 11px;"><strong>{_h.escape(m.group(1))}:</strong> '
                f'{_h.escape(m.group(2))}</p>')
        else:
            paras.append(f'<p style="margin:0 0 11px;">{_h.escape(line)}</p>')
    return (
        '<div style="max-height:620px;overflow-y:auto;padding:10px 16px;'
        'border:1px solid var(--border-default,#e2e8f0);border-radius:8px;'
        'background:var(--bg-surface,#fff);font-size:var(--fs-sm,0.86rem);'
        f'line-height:1.55;">{"".join(paras)}</div>')


def render_transcripts(ticker: str):
    cik = get_cik(ticker)
    if not cik:
        st.warning(f"No SEC CIK found for **{ticker}**. "
                   "This bank may not file with the SEC.")
        return

    title_bar(f"{get_name(ticker) or ticker} ({ticker})", "Transcripts & Presentations")

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

    # ── Earnings-call transcripts (FMP full text) ────────────────────────
    st.markdown("#### Earnings-Call Transcripts")
    dates = get_transcript_dates(ticker)
    if not dates:
        st.info("No earnings-call transcripts are available for this company "
                "from the transcript provider.")
        return

    labels = [
        f"Q{d['quarter']} {d['year']}" + (f"  ·  {d['date']}" if d.get("date") else "")
        for d in dates
    ]
    choice = st.selectbox("Earnings call", labels, index=0, key=f"tx_pick_{ticker}")
    sel = dates[labels.index(choice)]

    with st.spinner("Loading transcript…"):
        tx = get_transcript(ticker, sel["year"], sel["quarter"])
    if not tx or not tx.get("content"):
        st.info("The transcript text for this call is unavailable.")
        return

    words = len(tx["content"].split())
    asof = f"  ·  {tx['date']}" if tx.get("date") else ""
    st.caption(f"Q{sel['quarter']} {sel['year']} earnings call{asof}  ·  "
               f"~{words:,} words  ·  source: Financial Modeling Prep")
    st.markdown(_transcript_body_html(tx["content"]), unsafe_allow_html=True)
