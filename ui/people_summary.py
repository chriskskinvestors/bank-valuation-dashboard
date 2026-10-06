"""People Summary sub-tab (Overview section) — SNL plan §12.

Directors & executive officers extracted from the latest DEF 14A by the
guarded summarizer pipeline (data/people), labeled as AI-extracted and
source-linked, plus the Section 16 insider roster from Form 4 activity.
"""
from __future__ import annotations

import html as _h
import re

import pandas as pd
import streamlit as st

from data.bank_mapping import get_name, get_cik
from ui.chrome import title_bar, table_export


def _yn(v) -> str:
    if v is True:
        return "Yes"
    if v is False:
        return "No"
    return "—"


_GENERATIONAL = {"ii", "iii", "iv"}


def _cap_part(p: str) -> str:
    """One alphabetic run, capitalized; a "Mc" prefix keeps its inner capital
    ("MCGRATH" → "McGrath"). "Mac" is left alone — Mack/Macy/Mackay are not
    MacKay, and the all-caps source can't tell them apart."""
    c = p.capitalize()
    return "Mc" + c[2:].capitalize() if len(c) > 2 and c.startswith("Mc") else c


def _person_name(name: str) -> str:
    """Form 4 reporting-owner names arrive as EDGAR stores them — some ALL CAPS
    ("BACON ASHLEY"), some mixed ("Leopold Robin"). Title-case only the
    shouting ones (generational suffixes stay upper); mixed-case input is the
    filer's own casing and is kept (UX-P2-18)."""
    s = (name or "").strip()
    if s != s.upper():
        return s
    return " ".join(
        w.upper() if w.lower().strip(".,") in _GENERATIONAL
        else re.sub(r"[A-Za-z]+", lambda m: _cap_part(m.group(0)), w)
        for w in s.split())


def _is_issuer(name: str, ticker: str) -> bool:
    """True when a Form 4 reporting owner IS the issuer (a company-filed Form 4
    lists the registrant as the owner) — not a person, so it leaves the
    insider roster. Both names go through format_bank_name so EDGAR's
    ALL-CAPS entityName matches the curated display name (UX-P2-18)."""
    from utils.formatting import format_bank_name
    own = (get_name(ticker) or "").upper()
    return bool(own) and format_bank_name(name, ticker).upper() == own


def render_people_summary(ticker: str):
    from data.people import get_proxy_people, get_insider_roster

    title_bar(f"{get_name(ticker) or ticker} ({ticker})", "People Summary")

    cik = get_cik(ticker)
    if not cik:
        from ui.states import empty_state
        empty_state('No SEC filer mapping for this company — the proxy-based people roster needs a CIK (FDIC-only banks have no proxy on EDGAR)')
        return

    with st.spinner("Reading the latest proxy statement (first view runs "
                    "the extraction; later views are cached)…"):
        proxy = get_proxy_people(cik, ticker)

    if proxy and proxy.get("people"):
        people = proxy["people"]
        st.markdown('<div class="ksk-sec">Directors &amp; Executive Officers</div>',
                    unsafe_allow_html=True)
        body = ""
        for p in people:
            committees = ", ".join(p["committees"]) if p.get("committees") else "—"
            body += ("<tr>"
                     f'<td style="text-align:left;">{_h.escape(p["name"])}</td>'
                     f'<td style="text-align:right;">{p["age"] if p["age"] is not None else "—"}</td>'
                     f'<td style="text-align:left;">{_h.escape(p["position"] or "—")}</td>'
                     f'<td style="text-align:right;">{p["director_since"] or "—"}</td>'
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
                   "proxy doesn't state shows —, never inferred). May be "
                   f"incomplete — verify against the source:{link}.")

        bios = [p for p in people if p.get("bio")]
        if bios:
            with st.expander("One-line bios (from the proxy)"):
                for p in bios:
                    # Plain markdown: two dollar amounts in one bio ("oversaw
                    # $500 million and $2 billion portfolios") would render as
                    # inline LaTeX and swallow the text between them, so
                    # neutralize $ the way the other prose sinks do.
                    st.markdown(f"**{_h.escape(p['name'])}** — "
                                f"{_h.escape(p['bio'])}".replace("$", "\\$"))

        df = pd.DataFrame([{
            "Name": p["name"], "Age": p["age"], "Position": p["position"],
            "Role": p["role"], "Director Since": p["director_since"],
            "Independent": p["independent"],
            "Committees": ", ".join(p["committees"] or []),
            "Bio": p["bio"],
        } for p in people])
        filed = proxy.get("filed")
        table_export(df, f"{ticker}_people" + (f"_{filed}" if filed else ""),
                     key=f"exp_people_{ticker}",
                     sheet="Directors and officers",
                     formats={"Age": "int", "Director Since": "0"},   # a year: no thousands separator
                     provenance={"Page": "Company Analysis › Overview › People Summary",
                                 "Ticker": ticker, "CIK": cik,
                                 "Source": "DEF 14A proxy statement (AI-extracted, "
                                           "names verified verbatim against the "
                                           "filing; anything the proxy doesn't state "
                                           "is n/a, never inferred)",
                                 "Proxy filed": filed,
                                 "Source URL": src})
    else:
        from ui.states import empty_state
        empty_state('No proxy statement has been processed for this company yet.',
                    'The Section 16 roster below still reflects insider filings')

    # ── Section 16 roster (Form 4 activity) ──────────────────────────────
    # The issuer's own Form 4s (registrant as reporting owner) are not people.
    roster = [r for r in get_insider_roster(cik) if not _is_issuer(r["name"], ticker)]
    if roster:
        st.markdown('<div class="ksk-sec">Section 16 Insiders (recent Form 4 filers)</div>',
                    unsafe_allow_html=True)
        body = "".join(
            "<tr>"
            f'<td style="text-align:left;">{_h.escape(_person_name(r["name"]))}</td>'
            f'<td style="text-align:left;">{_h.escape(r["role"])}</td>'
            f'<td style="text-align:left;">{_h.escape(r["latest_date"] or "—")}</td>'
            "</tr>" for r in roster)
        st.markdown(
            '<div class="ksk-grid"><table><thead><tr>'
            '<th style="text-align:left;">Name</th>'
            '<th style="text-align:left;">Role (per latest Form 4)</th>'
            '<th style="text-align:left;">Latest Filing</th>'
            f"</tr></thead><tbody>{body}</tbody></table></div>",
            unsafe_allow_html=True)
        # A truncated Form 4 walk (very active filers) misses older filers.
        from data.form4_client import fetch_insider_history
        since = fetch_insider_history(int(cik))["complete_since"]
        st.caption("Insiders with Form 4 activity "
                   + (f"since {since} (history truncated: the fetch keeps the "
                      "30 most recent Form 4s)" if since
                      else "in the trailing 12 months")
                   + " — an activity roster, not the complete officer/director "
                   "list (that's the proxy table above).")
