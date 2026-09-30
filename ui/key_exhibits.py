"""
Key Exhibits — notable exhibits parsed from EDGAR filing index pages
(SNL "Key Exhibits" panel, docs/SNL-BUILD-PLAN.md §10).

For each recent 10-K / 10-Q / 8-K / DEF 14A, fetch the accession's
``<accession>-index.html`` on www.sec.gov/Archives and parse the document
table (Seq / Description / Document / Type / Size). Only the exhibit
families an analyst actually hunts for are kept:

  EX-21   subsidiaries of the registrant
  EX-3.*  articles of incorporation / bylaws
  EX-4.*  capital stock & debt instrument descriptions
  EX-10.* material agreements (credit agreements, comp plans)
  EX-99.* press releases / investor presentations

Family matching is boundary-aware: "EX-3" must NOT match EX-31/EX-32
(SOX certifications) and "EX-10" must NOT match EX-101 (XBRL instance).
"""
from __future__ import annotations

import html as _html
import re

import streamlit as st

from data.bank_mapping import get_cik, get_name
from data.sec_client import HEADERS, get_filing_info
from ui.chrome import title_bar

# (regex on the exhibit type, human label, badge color)
_EXHIBIT_FAMILIES = [
    (re.compile(r"^EX-21(\.|$)"), "Subsidiaries",                "#059669"),
    (re.compile(r"^EX-3(\.|$)"),  "Charter / Bylaws",            "#9333ea"),
    (re.compile(r"^EX-4(\.|$)"),  "Capital Stock / Debt",        "var(--brand-accent)"),
    (re.compile(r"^EX-10(\.|$)"), "Material Agreement",          "#d97706"),
    (re.compile(r"^EX-99(\.|$)"), "Press Release / Presentation", "#0891b2"),
]

_FORMS_WITH_KEY_EXHIBITS = ("10-K", "10-K/A", "10-Q", "10-Q/A",
                            "8-K", "8-K/A", "DEF 14A")

# One <tr> of the EDGAR filing-index document table:
# Seq | Description | Document (link) | Type | Size
_ROW_RE = re.compile(
    r"<tr[^>]*>\s*"
    r"<td[^>]*>.*?</td>\s*"                          # Seq
    r"<td[^>]*>(?P<desc>.*?)</td>\s*"                # Description
    r'<td[^>]*><a href="(?P<href>[^"]+)"[^>]*>.*?</td>\s*'  # Document
    r"<td[^>]*>(?P<type>EX-[^<\s]*)\s*</td>",        # Type (exhibits only)
    re.IGNORECASE | re.DOTALL,
)


def _family_of(ex_type: str) -> tuple[str, str] | None:
    """(label, color) for a notable exhibit type, else None."""
    for rx, label, color in _EXHIBIT_FAMILIES:
        if rx.match(ex_type):
            return label, color
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_key_exhibits(cik: int, max_filings: int = 10) -> list[dict]:
    """Notable exhibits from the most recent filings' EDGAR index pages.

    Returns a list of dicts (newest filing first):
      form, filed, type, family, color, description, url
    Empty list when nothing is found — never fabricated rows."""
    from data.http import get_with_retry

    info = get_filing_info(cik, max_filings=80)
    if not info:
        return []
    raw_cik = int(info.get("cik", cik))
    filings = [f for f in (info.get("recent_filings") or [])
               if f.get("form") in _FORMS_WITH_KEY_EXHIBITS]

    out: list[dict] = []
    for f in filings[:max_filings]:
        acc = (f.get("accession") or "").strip()
        if not acc:
            continue
        acc_clean = acc.replace("-", "")
        idx_url = (f"https://www.sec.gov/Archives/edgar/data/"
                   f"{raw_cik}/{acc_clean}/{acc}-index.html")
        try:
            resp = get_with_retry(idx_url, headers=HEADERS, timeout=15)
        except Exception as e:
            print(f"[exhibits] index fetch failed for {acc}: "
                  f"{type(e).__name__}: {e}")
            continue
        if resp is None:
            continue

        for m in _ROW_RE.finditer(resp.text):
            ex_type = m.group("type").strip().upper()
            fam = _family_of(ex_type)
            if fam is None:
                continue
            href = m.group("href").strip()
            if href.startswith("/ix?doc="):          # iXBRL viewer wrapper
                href = href[len("/ix?doc="):]
            url = f"https://www.sec.gov{href}" if href.startswith("/") else href
            # EDGAR's index HTML entity-encodes the filer's description
            # ("JPMORGAN CHASE &amp; CO. ...") — decode once here so every
            # sink escapes plain text exactly once (UX review 2026-09-24).
            desc = _html.unescape(re.sub(r"<[^>]+>", "", m.group("desc"))).strip()
            if not desc or desc.upper() == ex_type:
                desc = fam[0]  # filer left the description blank / echoed type
            out.append({
                "form": f.get("form", ""),
                "filed": f.get("date", ""),
                "type": ex_type,
                "family": fam[0],
                "color": fam[1],
                "description": desc,
                "url": url,
            })
    return out


def _exhibit_table(rows: list[dict]) -> str:
    """House-style (ui/tables.ksk_table) exhibit table HTML. The exhibit badge,
    category and link cells are built here (escaped here); every other cell is
    escaped by ksk_table_html. Descriptions are unescaped first — defensive
    against a cached pre-fix row still carrying "&amp;" — so the text is
    escaped exactly once."""
    import pandas as pd
    from ui.tables import ksk_table_html

    def _badge(r):
        return (f'<span style="background:{r["color"]};color:white;'
                f'padding:1px 7px;border-radius:0;font-size:0.78em;'
                f'font-weight:600;white-space:nowrap;">'
                f'{_html.escape(r["type"])}</span>')

    def _fam(r):
        return (f'<span style="color:{r["color"]};font-weight:600;'
                f'white-space:nowrap;">{_html.escape(r["family"])}</span>')

    def _link(r):
        return (f'<a href="{_html.escape(r["url"])}" target="_blank" '
                f'rel="noopener">View</a>') if r.get("url") else ""

    df = pd.DataFrame({
        "Filed": [r["filed"] or "—" for r in rows],
        "Form": [r["form"] or "—" for r in rows],
        "Exhibit": [_badge(r) for r in rows],
        "Category": [_fam(r) for r in rows],
        "Description": [_html.unescape(r["description"] or "") or "—"
                        for r in rows],
        "Link": [_link(r) for r in rows],
    })
    return ksk_table_html(df, html_cols=("Exhibit", "Category", "Link"),
                          txt_cols=("Form",))


def render_key_exhibits(ticker: str):
    """Key Exhibits sub-tab: notable exhibits from recent EDGAR filings."""
    cik = get_cik(ticker)
    if not cik:
        from ui.states import empty_state
        empty_state(f"{ticker} does not file with the SEC",
                    "OTC banks without SEC registration show FDIC call-report "
                    "data only — see Financials")
        return

    title_bar(f"{get_name(ticker) or ticker} ({ticker})", "Key Exhibits")
    st.caption(
        "Notable exhibits parsed from the index pages of the most recent "
        "10-K / 10-Q / 8-K / DEF 14A filings on EDGAR: EX-21 subsidiaries, "
        "EX-3/EX-4 charter & capital-stock documents, EX-10 material "
        "agreements, EX-99 press releases & presentations."
    )

    with st.spinner("Scanning recent filing indexes on EDGAR..."):
        exhibits = fetch_key_exhibits(cik)

    if not exhibits:
        from ui.states import empty_state
        empty_state("No notable exhibits (EX-21 / EX-3 / EX-4 / EX-10 / EX-99) found in this bank's most recent filings, or EDGAR index pages could not be fetched",
                    'Use Filings & Reports for the full filing list')
        return

    families = sorted({e["family"] for e in exhibits})
    picked = st.multiselect("Filter by category", options=families,
                            default=families, key=f"keyex_fam_{ticker}")
    shown = [e for e in exhibits if e["family"] in picked]
    if not shown:
        from ui.states import empty_state
        empty_state('No exhibits match the selected categories')
        return

    st.markdown(_exhibit_table(shown), unsafe_allow_html=True)
    st.caption(f"{len(shown)} exhibit{'s' if len(shown) != 1 else ''} from the "
               f"most recent filings · source: SEC EDGAR filing indexes")
