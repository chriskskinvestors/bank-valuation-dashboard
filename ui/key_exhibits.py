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

import re

import streamlit as st

from data.bank_mapping import get_cik, get_name
from data.sec_client import HEADERS, get_filing_info
from ui.chrome import title_bar

# (regex on the exhibit type, human label, badge color)
_EXHIBIT_FAMILIES = [
    (re.compile(r"^EX-21(\.|$)"), "Subsidiaries",                "#059669"),
    (re.compile(r"^EX-3(\.|$)"),  "Charter / Bylaws",            "#9333ea"),
    (re.compile(r"^EX-4(\.|$)"),  "Capital Stock / Debt",        "#2563eb"),
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
            desc = re.sub(r"<[^>]+>", "", m.group("desc")).strip()
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
    """Dense HTML table, same visual family as the filings table."""
    import html as _html

    body = []
    for i, r in enumerate(rows):
        badge = (f'<span style="background:{r["color"]};color:white;'
                 f'padding:2px 8px;border-radius:4px;font-size:0.78em;'
                 f'font-weight:600;white-space:nowrap;">{r["type"]}</span>')
        fam = (f'<span style="color:{r["color"]};font-size:0.8em;'
               f'font-weight:600;white-space:nowrap;">{r["family"]}</span>')
        link = (f'<a href="{_html.escape(r["url"])}" target="_blank" '
                f'style="color:#2563eb;text-decoration:none;">View</a>')
        zebra = "background:rgba(148,163,184,0.045);" if i % 2 else ""
        body.append(
            f"<tr style='{zebra}'>"
            f"<td style='padding:7px 10px;white-space:nowrap;color:var(--text-secondary);'>{r['filed']}</td>"
            f"<td style='padding:7px 10px;white-space:nowrap;color:var(--text-primary);'>{r['form']}</td>"
            f"<td style='padding:7px 10px;'>{badge}</td>"
            f"<td style='padding:7px 10px;'>{fam}</td>"
            f"<td style='padding:7px 10px;color:var(--text-primary);'>{_html.escape(r['description'])}</td>"
            f"<td style='padding:7px 10px;white-space:nowrap;'>{link}</td>"
            f"</tr>"
        )
    return f"""
    <style>
    .exhibits-tbl {{ width:100%; border-collapse:collapse; font-size:13px;
      border:1px solid rgba(148,163,184,0.22); border-radius:8px; overflow:hidden; }}
    .exhibits-tbl thead th {{ padding:8px 10px; text-align:left; color:var(--text-primary);
      font-weight:600; border-bottom:1px solid rgba(148,163,184,0.3);
      background:rgba(241,245,249,0.6); }}
    .exhibits-tbl tbody tr {{ border-bottom:1px solid rgba(148,163,184,0.12); }}
    .exhibits-tbl tbody tr:hover td {{ background:rgba(37,99,235,0.05) !important; }}
    </style>
    <div style="overflow-x:auto;">
    <table class="exhibits-tbl">
    <thead><tr><th>Filed</th><th>Form</th><th>Exhibit</th><th>Category</th>
    <th>Description</th><th>Link</th></tr></thead>
    <tbody>{"".join(body)}</tbody>
    </table>
    </div>
    """


def render_key_exhibits(ticker: str):
    """Key Exhibits sub-tab: notable exhibits from recent EDGAR filings."""
    cik = get_cik(ticker)
    if not cik:
        st.warning(f"No SEC CIK found for **{ticker}**. "
                   "This bank may not file with the SEC.")
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
        st.info(
            "No notable exhibits (EX-21 / EX-3 / EX-4 / EX-10 / EX-99) found "
            "in this bank's most recent filings, or EDGAR index pages could "
            "not be fetched. Use Filings & Reports for the full filing list."
        )
        return

    families = sorted({e["family"] for e in exhibits})
    picked = st.multiselect("Filter by category", options=families,
                            default=families, key=f"keyex_fam_{ticker}")
    shown = [e for e in exhibits if e["family"] in picked]
    if not shown:
        st.info("No exhibits match the selected categories.")
        return

    st.markdown(_exhibit_table(shown), unsafe_allow_html=True)
    st.caption(f"{len(shown)} exhibit{'s' if len(shown) != 1 else ''} from the "
               f"most recent filings · source: SEC EDGAR filing indexes")
