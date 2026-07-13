"""Corporate Governance sub-tab (Overview section) — SNL plan §12.

Three panes, each honestly sourced:
1. Charter/bylaw provisions — AI-extracted from the latest DEF 14A with the
   evidence-quote guard (data/governance), labeled + source-linked.
2. State corporate law — the curated, citation-first reference for the
   company's state of incorporation (data/state_corp_law).
3. Federal banking overlay — the control statutes that bind every banking
   organization regardless of state.
"""
from __future__ import annotations

import html as _h

import streamlit as st

from data.bank_mapping import get_name, get_cik
from data.state_corp_law import (
    get_state_reference,
    FEDERAL_BANKING_OVERLAY,
    REVIEWED,
)
from ui.chrome import title_bar


def _provision_status(v) -> str:
    if v is True:
        return '<span style="color:#059669;font-weight:600;">Yes</span>'
    if v is False:
        return "No"
    return "n/a"


def _statute_cell(entry) -> str:
    """One state-statute family → status + citation + note."""
    if not isinstance(entry, dict) or "has" not in entry:
        return "n/a"
    if not entry["has"]:
        out = "None"
    else:
        out = '<span style="font-weight:600;">Yes</span>'
        if entry.get("cite"):
            out += f' — {_h.escape(entry["cite"])}'
    if entry.get("note"):
        out += f' <span style="color:#64748b;">({_h.escape(entry["note"])})</span>'
    return out


def render_corporate_governance(ticker: str):
    from data.governance import get_governance_provisions, PROVISIONS
    from data.sec_client import get_filing_info

    title_bar(f"{get_name(ticker) or ticker} ({ticker})", "Corporate Governance")

    cik = get_cik(ticker)

    # ── 1. Charter/bylaw provisions (proxy extraction) ───────────────────
    gov = None
    if cik:
        with st.spinner("Reading the latest proxy statement (first view runs "
                        "the extraction; later views are cached)…"):
            gov = get_governance_provisions(cik, ticker)
    st.markdown("#### Charter & Bylaw Provisions")
    if gov and gov.get("provisions"):
        p = gov["provisions"]
        body = ""
        for key, label in PROVISIONS:
            entry = p.get(key) or {}
            quote = entry.get("quote")
            evidence = (f'<span style="color:#64748b;">“{_h.escape(quote)}”</span>'
                        if quote else "—")
            body += ("<tr>"
                     f'<td style="text-align:left;">{_h.escape(label)}</td>'
                     f'<td style="text-align:left;">{_provision_status(entry.get("value"))}</td>'
                     f'<td style="text-align:left;">{evidence}</td>'
                     "</tr>")
        st.markdown(
            '<div class="ksk-grid"><table><thead><tr>'
            '<th style="text-align:left;">Provision</th>'
            '<th style="text-align:left;">Status</th>'
            '<th style="text-align:left;">Evidence (verbatim from the proxy)</th>'
            f"</tr></thead><tbody>{body}</tbody></table></div>",
            unsafe_allow_html=True)
        src = gov.get("source_url")
        link = f" [DEF 14A filed {gov.get('filed')}]({src})" if src else ""
        st.caption("AI-extracted and evidence-guarded: a status shows only "
                   "when the supporting quote verifies verbatim against the "
                   "filing; n/a means the proxy is silent or the evidence "
                   f"didn't verify. Source:{link}.")
    else:
        st.info("No verified charter/bylaw extraction is available for this "
                "company (needs a DEF 14A on EDGAR and the summarizer API)."
                if cik else
                "No SEC filer mapping — charter/bylaw extraction needs a "
                "proxy on EDGAR.")

    # ── 2. State corporate law ───────────────────────────────────────────
    state = None
    if cik:
        info = get_filing_info(cik) or {}
        state = (info.get("state_of_incorp") or "").strip().upper() or None
    st.markdown("#### State Corporate Law")
    ref = get_state_reference(state)
    if ref:
        rows = [
            ("Business combination statute", _statute_cell(ref.get("business_combination"))),
            ("Control share statute", _statute_cell(ref.get("control_share"))),
            ("Fair price statute", _statute_cell(ref.get("fair_price"))),
            ("Cumulative voting default",
             "Yes" if ref.get("cumulative_voting_default") else "No"),
        ]
        body = "".join(
            "<tr>"
            f'<td style="text-align:left;">{_h.escape(k)}</td>'
            f'<td style="text-align:left;">{v}</td>'
            "</tr>" for k, v in rows)
        st.markdown(
            f'<div class="ksk-grid"><table><thead><tr>'
            f'<th style="text-align:left;">Incorporated in {_h.escape(ref["name"])}</th>'
            '<th style="text-align:left;">Provision</th>'
            f"</tr></thead><tbody>{body}</tbody></table></div>",
            unsafe_allow_html=True)
        notes = ref.get("notes")
        tail = f" {notes}" if notes else ""
        st.caption(f"Curated statutory reference (reviewed {REVIEWED}) — "
                   "citations provided for verification; statutes often allow "
                   f"charter opt-outs. Not legal advice.{tail}")
    elif state:
        st.caption(f"State of incorporation: {state} — statutory reference "
                   "not yet curated for this state (no guess rendered).")
    else:
        st.caption("State of incorporation unavailable from SEC submissions.")

    # ── 3. Federal banking overlay ───────────────────────────────────────
    st.markdown("#### Federal Banking Control Overlay")
    body = "".join(
        "<tr>"
        f'<td style="text-align:left;font-weight:600;">{_h.escape(name)}</td>'
        f'<td style="text-align:left;">{_h.escape(desc)}</td>'
        "</tr>" for name, desc in FEDERAL_BANKING_OVERLAY)
    st.markdown(
        '<div class="ksk-grid"><table><tbody>'
        f"{body}</tbody></table></div>",
        unsafe_allow_html=True)
    st.caption("Applies to every banking organization regardless of state — "
               "in practice the binding constraint on control accumulation.")
