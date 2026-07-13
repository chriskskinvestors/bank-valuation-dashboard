"""People Summary sub-tab (Overview section) — SNL plan §12.

Directors & executive officers extracted from the latest DEF 14A by the
guarded summarizer pipeline (data/people), labeled as AI-extracted and
source-linked, plus the Section 16 insider roster from Form 4 activity.
"""
from __future__ import annotations

import html as _h

import pandas as pd
import streamlit as st

from data.bank_mapping import get_name, get_cik
from ui.chrome import title_bar, table_export


def _yn(v) -> str:
    if v is True:
        return "Yes"
    if v is False:
        return "No"
    return "n/a"


def render_people_summary(ticker: str):
    from data.people import get_proxy_people, get_insider_roster

    title_bar(f"{get_name(ticker) or ticker} ({ticker})", "People Summary")

    cik = get_cik(ticker)
    if not cik:
        st.info("No SEC filer mapping for this company — the proxy-based "
                "people roster needs a CIK (FDIC-only banks have no proxy "
                "on EDGAR).")
        return

    with st.spinner("Reading the latest proxy statement (first view runs "
                    "the extraction; later views are cached)…"):
        proxy = get_proxy_people(cik, ticker)

    if proxy and proxy.get("people"):
        people = proxy["people"]
        st.markdown("#### Directors & Executive Officers")
        body = ""
        for p in people:
            committees = ", ".join(p["committees"]) if p.get("committees") else "n/a"
            body += ("<tr>"
                     f'<td style="text-align:left;">{_h.escape(p["name"])}</td>'
                     f'<td style="text-align:right;">{p["age"] if p["age"] is not None else "n/a"}</td>'
                     f'<td style="text-align:left;">{_h.escape(p["position"] or "n/a")}</td>'
                     f'<td style="text-align:right;">{p["director_since"] or "n/a"}</td>'
                     f'<td style="text-align:left;">{_yn(p["independent"])}</td>'
                     f'<td style="text-align:left;">{_h.escape(committees)}</td>'
                     "</tr>")
        st.markdown(
            '<div class="ksk-grid"><table><thead><tr>'
            '<th style="text-align:left;">Name</th>'
            '<th style="text-align:right;">Age</th>'
            '<th style="text-align:left;">Position</th>'
            '<th style="text-align:right;">Director Since</th>'
            '<th style="text-align:left;">Independent</th>'
            '<th style="text-align:left;">Committees</th>'
            f"</tr></thead><tbody>{body}</tbody></table></div>",
            unsafe_allow_html=True)

        src = proxy.get("source_url")
        link = f" [DEF 14A filed {proxy.get('filed')}]({src})" if src else ""
        st.caption("AI-extracted from the proxy statement and guarded "
                   "(names verified verbatim against the filing; anything the "
                   "proxy doesn't state is n/a, never inferred). May be "
                   f"incomplete — verify against the source:{link}.")

        bios = [p for p in people if p.get("bio")]
        if bios:
            with st.expander("One-line bios (from the proxy)"):
                for p in bios:
                    st.markdown(f"**{_h.escape(p['name'])}** — {_h.escape(p['bio'])}")

        df = pd.DataFrame([{
            "Name": p["name"], "Age": p["age"], "Position": p["position"],
            "Role": p["role"], "Director Since": p["director_since"],
            "Independent": p["independent"],
            "Committees": ", ".join(p["committees"] or []),
            "Bio": p["bio"],
        } for p in people])
        table_export(df, f"{ticker}_people", key=f"exp_people_{ticker}")
    else:
        st.info("No proxy-based roster is available for this company — the "
                "extraction needs a DEF 14A on EDGAR and the summarizer API. "
                "The Section 16 roster below still reflects insider filings.")

    # ── Section 16 roster (Form 4 activity) ──────────────────────────────
    roster = get_insider_roster(cik)
    if roster:
        st.markdown("#### Section 16 Insiders (recent Form 4 filers)")
        body = "".join(
            "<tr>"
            f'<td style="text-align:left;">{_h.escape(r["name"])}</td>'
            f'<td style="text-align:left;">{_h.escape(r["role"])}</td>'
            f'<td style="text-align:left;">{_h.escape(r["latest_date"] or "n/a")}</td>'
            "</tr>" for r in roster)
        st.markdown(
            '<div class="ksk-grid"><table><thead><tr>'
            '<th style="text-align:left;">Name</th>'
            '<th style="text-align:left;">Role (per latest Form 4)</th>'
            '<th style="text-align:left;">Latest Filing</th>'
            f"</tr></thead><tbody>{body}</tbody></table></div>",
            unsafe_allow_html=True)
        st.caption("Insiders with Form 4 activity in the trailing 12 months — "
                   "an activity roster, not the complete officer/director "
                   "list (that's the proxy table above).")
