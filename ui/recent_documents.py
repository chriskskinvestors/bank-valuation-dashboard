"""
Recent Documents — CapIQ/SNL-style master documents page
(Company → News & Filings → Recent Documents; owner spec 2026-07-08 matching
the S&P CapIQ "Recent Documents" screen).

Seven dense panels over a filterable window (default five years) of EDGAR
filings plus FMP transcripts:

  Left column                       Right column
  · Annuals/Interims                · Current Reports (8-K)
  · Transcripts & Presentations     · Key Exhibits
  · Proxies                         · Merger Documents
  · Regulatory Filings

Each document opens a CapIQ-style action menu (native <details>; the shared
``name`` attribute auto-closes siblings — Chromium behavior, which is the
supported browser):

  Document Viewer → EDGAR inline-XBRL viewer, new tab (iXBRL forms only)
  View HTML       → the document on Archives
  All Documents   → the filing index page
  Download PDF    → ?pdf=… link (new tab) rendered server-side to PDF
                    (data/filing_pdf, headless chromium) behind an
                    st.dialog download button

"More" hands off to the richer sub-tab (Filings & Reports, Key Exhibits,
Transcripts & Presentations) by writing the sub-tab radio's session key —
the key format mirrors app.py::_subtab_key. Panels without a richer tab
expand in place.

Security: all data-derived text goes through _safe() before the
unsafe_allow_html sink; hrefs are escaped; the ?pdf= handler rebuilds
sec.gov URLs server-side from parts that pass strict regexes and never
converts a caller-supplied URL (see parse_pdf_param).
"""
from __future__ import annotations

import html as _html
import re
from datetime import date, timedelta
from urllib.parse import urlencode

import pandas as pd
import streamlit as st

from data.bank_mapping import get_cik, get_fdic_cert, get_name
from data.sec_client import get_filing_info
from ui.chrome import table_export, title_bar
from ui.filings import _filing_primary, _safe
from ui.key_exhibits import fetch_key_exhibits

# Mirrors app.py::_subtab_key("News & Filings", None) — the sub-tab radio's
# session key. Written from a button on_click (before the radio instantiates
# on the rerun), which is the legal window for widget-backed state.
_SUBTAB_KEY = "company_subtab::News & Filings::None"

_ANNUAL_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A", "11-K", "ARS"}
_PROXY_FORMS = {"DEF 14A", "DEFA14A", "PRE 14A"}
_MERGER_FORMS = {"S-4", "S-4/A", "425", "DEFM14A"}
_REG_FORMS = {"SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A",
              "S-1", "S-1/A", "S-3", "S-3/A", "25", "25-NSE",
              "15-12B", "15-12G"}
# Forms whose primary document is inline-XBRL on EDGAR (viewer-capable).
_IXBRL_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A"}

_DATE_RANGES = {"1 Year": 1, "2 Years": 2, "5 Years": 5, "10 Years": 10,
                "All": None}

_ROWS_COLLAPSED = 10

# CapIQ-style document labels by form.
_FORM_LABELS = {
    "ARS": "Annual Report (ARS)",
    "DEF 14A": "Proxy (DEF 14A)", "DEFA14A": "Proxy Add'l (DEFA14A)",
    "PRE 14A": "Preliminary Proxy (PRE 14A)",
    "DEFM14A": "Merger Proxy (DEFM14A)",
    "S-4": "Registration Statement (S-4)",
    "S-4/A": "Registration Statement (S-4/A)",
    "425": "Merger Communication (425)",
    "S-1": "Registration Statement (S-1)", "S-1/A": "Registration Statement (S-1/A)",
    "S-3": "Registration Statement (S-3)", "S-3/A": "Registration Statement (S-3/A)",
    "25": "Delisting Notice (25)", "25-NSE": "Delisting Notice (25-NSE)",
    "15-12B": "Deregistration (15-12B)", "15-12G": "Deregistration (15-12G)",
}

_PRESENTATION_WORDS = ("presentation", "investor deck", "slide", "supplemental")

# ?pdf= param shapes (see _handle_pdf_request):
#   doc|<accession>|<filename>   a document inside a filing we listed
#   er|<accession>               earnings release — EX-99.1 resolved at click
#   sec|<archives-path>          an exhibit URL we listed (path under sec.gov)
#   tx|<year>|<quarter>          FMP transcript rendered from text
_ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_DOCNAME_RE = re.compile(r"^[\w.\-]+$")
_SECPATH_RE = re.compile(r"^Archives/edgar/data/[\w./\-]+$")


def parse_pdf_param(raw: str) -> dict | None:
    """Validate and split a ?pdf= param. None for anything malformed — the
    handler silently drops bad params rather than fetching attacker-shaped
    URLs. Pure function (unit-tested)."""
    parts = (raw or "").split("|")
    kind = parts[0] if parts else ""
    if kind == "doc" and len(parts) == 3:
        acc, doc = parts[1], parts[2]
        if _ACCESSION_RE.match(acc) and _DOCNAME_RE.match(doc) and ".." not in doc:
            return {"kind": "doc", "accession": acc, "doc": doc}
    elif kind == "er" and len(parts) == 2:
        if _ACCESSION_RE.match(parts[1]):
            return {"kind": "er", "accession": parts[1]}
    elif kind == "sec" and len(parts) == 2:
        path = parts[1]
        if _SECPATH_RE.match(path) and ".." not in path:
            return {"kind": "sec", "path": path}
    elif kind == "tx" and len(parts) == 3:
        if parts[1].isdigit() and parts[2].isdigit() and len(parts[1]) == 4:
            return {"kind": "tx", "year": int(parts[1]), "quarter": int(parts[2])}
    return None


def doc_label(f: dict) -> str:
    """CapIQ-style document label for an EDGAR filing record."""
    form = f.get("form", "")
    if f.get("is_earnings"):
        return "Earnings Release (ER)"
    if form in _FORM_LABELS:
        return _FORM_LABELS[form]
    if form in ("8-K", "8-K/A"):
        primary = _filing_primary(form, f.get("items", ""), False)
        return f"{form} — {primary}" if primary != form else f"{form} (8-K)"
    return f"{form} ({form})"


def classify_filings(filings: list[dict]) -> dict[str, list[dict]]:
    """Assign each filing to exactly one panel. Pure function (unit-tested).
    Earnings 8-Ks go to Annuals/Interims (CapIQ convention); every other 8-K
    is a Current Report. DEFM14A lives with Merger Documents, not Proxies."""
    panels: dict[str, list[dict]] = {
        "annuals": [], "current": [], "proxies": [], "mergers": [],
        "regulatory": [],
    }
    for f in filings:
        form = f.get("form", "")
        if form in _ANNUAL_FORMS or f.get("is_earnings"):
            panels["annuals"].append(f)
        elif form in ("8-K", "8-K/A"):
            panels["current"].append(f)
        elif form in _MERGER_FORMS:
            panels["mergers"].append(f)
        elif form in _PROXY_FORMS:
            panels["proxies"].append(f)
        elif form in _REG_FORMS:
            panels["regulatory"].append(f)
    return panels


# ── Row / menu HTML ──────────────────────────────────────────────────────

_CSS = """
<style>
.rd-panel { margin-bottom: 14px; }
.rd-panel-h { font-weight:700; font-size:0.85rem; color:var(--text-primary);
  padding:6px 10px; background:rgba(241,245,249,0.85);
  border:1px solid rgba(148,163,184,0.3); border-bottom:none; }
.rd-tbl { width:100%; table-layout:fixed; border-collapse:collapse;
  font-size:12.5px; border:1px solid rgba(148,163,184,0.22); }
.rd-tbl thead th { padding:5px 10px; text-align:left; color:var(--text-secondary);
  font-weight:600; font-size:0.72rem; text-transform:uppercase; letter-spacing:0.02em;
  border-bottom:1px solid rgba(148,163,184,0.3); background:rgba(241,245,249,0.6); }
.rd-tbl tbody tr { border-bottom:1px solid rgba(148,163,184,0.12); }
.rd-tbl tbody tr:hover td { background:rgba(37,99,235,0.05); }
.rd-tbl td { padding:5px 10px; vertical-align:top; }
.rd-tbl td.rd-date { white-space:nowrap; color:var(--text-secondary); }
/* The menu must NOT be clipped: ellipsis lives on the summary (label line)
   only; the dropdown is the summary's sibling, free to overflow the cell. */
.rd-dd { display:block; position:relative; }
/* !important: styles.py has a global `details summary` color/size/weight rule
   (expander styling) that must not win over the document-link look. */
.rd-dd > summary { list-style:none; cursor:pointer;
  color:var(--brand-accent) !important; font-weight:600 !important;
  font-size:12.5px !important;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.rd-dd > summary::-webkit-details-marker { display:none; }
.rd-dd > summary:hover { text-decoration:underline; }
.rd-menu { position:absolute; left:0; top:calc(100% + 2px); z-index:60;
  background:var(--bg-surface,#fff); border:1px solid rgba(148,163,184,0.45);
  box-shadow:0 4px 14px rgba(15,23,42,0.18); min-width:180px; padding:3px 0; }
.rd-menu a { display:block; padding:6px 14px; font-size:0.8rem;
  color:var(--text-primary); text-decoration:none; white-space:nowrap; }
.rd-menu a:hover { background:rgba(37,99,235,0.08); color:var(--brand-accent); }
.rd-sub { color:var(--text-muted); font-weight:400; }
</style>
"""


def _menu_html(label: str, items: list[tuple[str, str]], sub: str = "") -> str:
    """A document cell: <details> click-menu named so only one opens at a
    time. `label`/`sub` are data-derived → escaped; hrefs escaped. The sub
    text rides inside the summary so the whole line ellipsizes together."""
    sub_html = f' <span class="rd-sub">{_safe(sub)}</span>' if sub else ""
    links = "".join(
        f'<a href="{_html.escape(url)}" target="_blank" rel="noopener">'
        f'{_safe(text)}</a>' for text, url in items if url)
    if not links:  # no actionable documents — render an inert label
        return f'<span style="font-weight:600;">{_safe(label)}</span>{sub_html}'
    return (f'<details class="rd-dd" name="rd-menu">'
            f'<summary>{_safe(label)}{sub_html}</summary>'
            f'<div class="rd-menu">{links}</div></details>')


def _row_html(doc_cell: str, filed: str) -> str:
    return (f'<tr><td class="rd-doc">{doc_cell}</td>'
            f'<td class="rd-date">{_safe(filed)}</td></tr>')


def _panel_html(title: str, rows: list[str]) -> str:
    body = "".join(rows) if rows else (
        '<tr><td class="rd-doc" style="color:var(--text-muted);">None in the '
        'selected date range</td><td class="rd-date"></td></tr>')
    return (f'<div class="rd-panel"><div class="rd-panel-h">{_safe(title)}</div>'
            f'<table class="rd-tbl"><thead><tr><th>Document</th>'
            f'<th style="width:96px;">Filing Date</th></tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


def _pdf_href(ticker: str, param: str) -> str:
    """Self-link that opens a fresh session on this tab with ?pdf= set; the
    handler there renders the PDF behind a download dialog."""
    return "?" + urlencode({"s": "Company", "bank": ticker,
                            "tab": "Recent Documents", "pdf": param})


def _filing_menu(f: dict, ticker: str) -> str:
    """Action menu for an EDGAR filing row (from get_filing_info)."""
    form = f.get("form", "")
    url = f.get("url", "")
    acc = (f.get("accession") or "").strip()
    doc_name = url.rsplit("/", 1)[-1] if url else ""
    items: list[tuple[str, str]] = []
    if url and form in _IXBRL_FORMS:
        # EDGAR's inline viewer wraps the primary doc path.
        path = url.split("sec.gov", 1)[-1]
        items.append(("Document Viewer", f"https://www.sec.gov/ix?doc={path}"))
    if f.get("is_earnings") and acc:
        # The ER document is the EX-99.1 exhibit — resolved at click time by
        # the pdf handler; View HTML falls back to the 8-K primary doc.
        items.append(("View HTML", url))
        if f.get("index_url"):
            items.append(("All Documents", f["index_url"]))
        items.append(("Download PDF", _pdf_href(ticker, f"er|{acc}")))
    else:
        if url:
            items.append(("View HTML", url))
        if f.get("index_url"):
            items.append(("All Documents", f["index_url"]))
        if acc and doc_name and _ACCESSION_RE.match(acc) and _DOCNAME_RE.match(doc_name):
            items.append(("Download PDF", _pdf_href(ticker, f"doc|{acc}|{doc_name}")))
    sub = ""
    if form in ("8-K", "8-K/A") and not f.get("is_earnings"):
        return _menu_html(f"{form} (8-K)", items,
                          sub=_filing_primary(form, f.get("items", ""), False))
    return _menu_html(doc_label(f), items, sub=sub)


def _exhibit_menu(e: dict, ticker: str, label: str) -> str:
    """Action menu for a Key Exhibits / EX-99 row (from fetch_key_exhibits)."""
    url = e.get("url", "")
    items: list[tuple[str, str]] = []
    if url:
        items.append(("View HTML", url))
        prefix = "https://www.sec.gov/"
        if url.startswith(prefix):
            path = url[len(prefix):]
            if _SECPATH_RE.match(path) and ".." not in path:
                items.append(("Download PDF", _pdf_href(ticker, f"sec|{path}")))
    return _menu_html(label, items, sub=e.get("description", ""))


# ── PDF request handling (?pdf=…) ────────────────────────────────────────

@st.cache_data(ttl=3600, max_entries=8, show_spinner=False)
def _pdf_from_url(url: str) -> bytes | None:
    from data.filing_pdf import filing_url_to_pdf_bytes
    return filing_url_to_pdf_bytes(url)


@st.cache_data(ttl=3600, max_entries=4, show_spinner=False)
def _pdf_from_transcript(ticker: str, year: int, quarter: int) -> bytes | None:
    from data.filing_pdf import html_to_pdf_bytes
    from data.fmp_transcripts import get_transcript
    from ui.transcripts import _SPEAKER_RE
    tx = get_transcript(ticker, year, quarter)
    content = (tx or {}).get("content") or ""
    if not content:
        return None
    paras = []
    for raw in content.split("\n"):
        line = raw.strip()
        if not line:
            continue
        m = _SPEAKER_RE.match(line)
        if m:
            paras.append(f"<p><strong>{_html.escape(m.group(1))}:</strong> "
                         f"{_html.escape(m.group(2))}</p>")
        else:
            paras.append(f"<p>{_html.escape(line)}</p>")
    asof = f" · {tx['date']}" if tx.get("date") else ""
    html_doc = (
        f"<html><head><meta charset='utf-8'></head>"
        f"<body style='font-family:Georgia,serif;font-size:11pt;line-height:1.5;'>"
        f"<h2 style='margin-bottom:2px;'>{_html.escape(get_name(ticker) or ticker)}"
        f" ({_html.escape(ticker)})</h2>"
        f"<div style='color:#555;margin-bottom:14px;'>Q{quarter} {year} earnings call"
        f"{_html.escape(asof)} · source: Financial Modeling Prep</div>"
        f"{''.join(paras)}</body></html>")
    return html_to_pdf_bytes(html_doc)


def _handle_pdf_request(ticker: str, cik: int, filings: list[dict]) -> None:
    """If ?pdf= is present and valid, render the document to PDF behind a
    download dialog, then clear the param so reruns don't reopen it."""
    raw = st.query_params.get("pdf")
    if not raw:
        return
    req = parse_pdf_param(raw)
    try:
        del st.query_params["pdf"]
    except Exception:
        pass
    if req is None:
        return

    by_acc = {(f.get("accession") or "").strip(): f for f in filings}

    def _resolve() -> tuple[str | None, str]:
        """(url-or-None, filename) for url kinds; tx handled separately."""
        if req["kind"] == "doc":
            acc_clean = req["accession"].replace("-", "")
            return (f"https://www.sec.gov/Archives/edgar/data/{cik}/"
                    f"{acc_clean}/{req['doc']}",
                    f"{ticker}_{req['doc'].rsplit('.', 1)[0]}.pdf")
        if req["kind"] == "sec":
            return (f"https://www.sec.gov/{req['path']}",
                    f"{ticker}_{req['path'].rsplit('/', 1)[-1].rsplit('.', 1)[0]}.pdf")
        if req["kind"] == "er":
            from data.filing_summarizer import find_press_release_url
            url = find_press_release_url(cik, req["accession"])
            if not url:  # honest fallback: the 8-K primary document
                url = (by_acc.get(req["accession"]) or {}).get("url")
            f = by_acc.get(req["accession"]) or {}
            return url, f"{ticker}_earnings_release_{f.get('date', '')}.pdf"
        return None, ""

    @st.dialog("Download PDF")
    def _dlg():
        if req["kind"] == "tx":
            fname = f"{ticker}_Q{req['quarter']}_{req['year']}_transcript.pdf"
            with st.spinner("Rendering transcript to PDF…"):
                pdf = _pdf_from_transcript(ticker, req["year"], req["quarter"])
        else:
            url, fname = _resolve()
            if not url:
                st.error("Could not locate this document on EDGAR.")
                return
            st.caption(url)
            with st.spinner("Rendering PDF — a large 10-K can take ~10 seconds…"):
                pdf = _pdf_from_url(url)
        if pdf:
            st.download_button("Download PDF", pdf, file_name=fname,
                               mime="application/pdf", type="primary")
            st.caption(f"{len(pdf) / 1e6:.1f} MB · rendered from the EDGAR "
                       "HTML by the dashboard's print engine")
        else:
            st.error("PDF rendering failed for this document. Use View HTML "
                     "from the document menu instead.")

    _dlg()


# ── Page ─────────────────────────────────────────────────────────────────

def _goto_subtab(tab: str) -> None:
    st.session_state[_SUBTAB_KEY] = tab


def _more_control(key: str, total: int, tab_target: str | None) -> None:
    """CapIQ's "More": hand off to the richer sub-tab when one exists,
    else expand the panel in place (rd_all_<key> session flag)."""
    shown = min(total, _ROWS_COLLAPSED)
    if total <= shown and not tab_target:
        return
    if tab_target:
        st.button(f"More → {tab_target}", key=f"rd_more_{key}",
                  on_click=_goto_subtab, args=(tab_target,))
    elif not st.session_state.get(f"rd_all_{key}"):
        st.button(f"Show all ({total})", key=f"rd_more_{key}",
                  on_click=lambda: st.session_state.update({f"rd_all_{key}": True}))


def _panel_rows(key: str, rows: list[str]) -> list[str]:
    if st.session_state.get(f"rd_all_{key}"):
        return rows
    return rows[:_ROWS_COLLAPSED]


def render_recent_documents(ticker: str) -> None:
    cik = get_cik(ticker)
    if not cik:
        st.warning(f"No SEC CIK found for **{ticker}**. "
                   "This bank may not file with the SEC.")
        return

    name = get_name(ticker) or ticker
    title_bar(f"{name} ({ticker})", "Recent Documents")

    # ── Filters row ──────────────────────────────────────────────────────
    fcol, _sp = st.columns([1, 4])
    with fcol:
        rng = st.selectbox("Date Range", list(_DATE_RANGES),
                           index=2, key=f"rd_range_{ticker}")

    with st.spinner("Loading EDGAR submissions…"):
        info = get_filing_info(cik, max_filings=1000)
    if not info:
        st.error("Failed to load filing data from SEC EDGAR.")
        return
    raw_cik = int(info.get("cik", cik))
    filings = info.get("recent_filings", [])

    years = _DATE_RANGES[rng]
    if years is not None:
        cutoff = (date.today() - timedelta(days=365 * years)).isoformat()
        filings = [f for f in filings if (f.get("date") or "") >= cutoff]

    _handle_pdf_request(ticker, raw_cik, filings)

    panels = classify_filings(filings)

    # Exhibits (cached, last ~10 filings): EX-21/3/4/10 → Key Exhibits panel;
    # EX-99 splits into presentations vs press releases, minus the earnings
    # releases already rowed in Annuals/Interims.
    exhibits = fetch_key_exhibits(raw_cik)
    er_dates = {f.get("date") for f in panels["annuals"] if f.get("is_earnings")}
    key_exhibits, presentations, press_releases = [], [], []
    for e in exhibits:
        if e.get("family") == "Press Release / Presentation":
            # ER dedup FIRST: the EX-99.1 on an earnings 8-K IS the earnings
            # release already rowed in Annuals/Interims — and its description
            # is often blank (falling back to the family name, which would
            # falsely keyword-match "presentation" below).
            if e.get("type") == "EX-99.1" and e.get("filed") in er_dates:
                continue
            desc = (e.get("description") or "").lower()
            if (desc != "press release / presentation"
                    and any(w in desc for w in _PRESENTATION_WORDS)):
                presentations.append(e)
            else:
                press_releases.append(e)
        else:
            key_exhibits.append(e)

    st.markdown(_CSS, unsafe_allow_html=True)
    left, right = st.columns(2, gap="medium")

    # ── Left column ──────────────────────────────────────────────────────
    with left:
        rows = [_row_html(_filing_menu(f, ticker), f.get("date", ""))
                for f in _panel_rows("annuals", panels["annuals"])]
        st.markdown(_panel_html("Annuals/Interims", rows), unsafe_allow_html=True)
        _more_control("annuals", len(panels["annuals"]), "Filings & Reports")

        tx_rows = []
        try:
            from data.fmp_transcripts import get_transcript_dates
            tx_dates = get_transcript_dates(ticker) or []
        except Exception:
            tx_dates = []
        tab_url = "?" + urlencode({"s": "Company", "bank": ticker,
                                   "tab": "Transcripts & Presentations"})
        for d in tx_dates:
            label = f"Transcript (Earnings Call) — Q{d['quarter']} {d['year']}"
            items = [("Download PDF",
                      _pdf_href(ticker, f"tx|{d['year']}|{d['quarter']}")),
                     ("Open in Transcripts tab", tab_url)]
            tx_rows.append((d.get("date") or "", _menu_html(label, items)))
        for e in presentations:
            tx_rows.append((e.get("filed") or "",
                            _exhibit_menu(e, ticker,
                                          f"Investor Presentation ({e['type']})")))
        tx_rows.sort(key=lambda r: r[0], reverse=True)
        rows = [_row_html(cell, dt) for dt, cell in _panel_rows("tx", tx_rows)]
        st.markdown(_panel_html("Transcripts & Presentations", rows),
                    unsafe_allow_html=True)
        _more_control("tx", len(tx_rows), "Transcripts & Presentations")

        rows = [_row_html(_filing_menu(f, ticker), f.get("date", ""))
                for f in _panel_rows("proxies", panels["proxies"])]
        st.markdown(_panel_html("Proxies", rows), unsafe_allow_html=True)
        _more_control("proxies", len(panels["proxies"]), None)

        rows = [_row_html(_filing_menu(f, ticker), f.get("date", ""))
                for f in _panel_rows("regulatory", panels["regulatory"])]
        st.markdown(_panel_html("Regulatory Filings", rows), unsafe_allow_html=True)
        _more_control("regulatory", len(panels["regulatory"]), None)
        cert = get_fdic_cert(ticker)
        reg_links = [f'<a href="https://www.sec.gov/cgi-bin/browse-edgar?action='
                     f'getcompany&CIK={raw_cik}&type=&dateb=&owner=include&count=40" '
                     f'target="_blank">EDGAR company page</a>']
        if cert:
            reg_links.append(
                f'<a href="https://www.fdic.gov/analysis/bank-research/bankfind/'
                f'index.html?CERT={cert}" target="_blank">FDIC BankFind</a>')
            reg_links.append(
                '<a href="https://cdr.ffiec.gov/public/ManageFacsimiles.aspx" '
                'target="_blank">FFIEC call reports</a>')
        st.caption("Bank call reports live at FFIEC/FDIC, not EDGAR: "
                   + " · ".join(reg_links), unsafe_allow_html=True)

    # ── Right column ─────────────────────────────────────────────────────
    with right:
        cur_rows = [(f.get("date") or "", _filing_menu(f, ticker))
                    for f in panels["current"]]
        for e in press_releases:
            cur_rows.append((e.get("filed") or "",
                             _exhibit_menu(e, ticker, "Press Release (PR)")))
        cur_rows.sort(key=lambda r: r[0], reverse=True)
        rows = [_row_html(cell, dt) for dt, cell in _panel_rows("current", cur_rows)]
        st.markdown(_panel_html("Current Reports", rows), unsafe_allow_html=True)
        _more_control("current", len(cur_rows), "Filings & Reports")

        rows = [_row_html(_exhibit_menu(e, ticker, f"{e['family']} ({e['type']})"),
                          e.get("filed", ""))
                for e in _panel_rows("keyex", key_exhibits)]
        st.markdown(_panel_html("Key Exhibits", rows), unsafe_allow_html=True)
        _more_control("keyex", len(key_exhibits), "Key Exhibits")

        rows = [_row_html(_filing_menu(f, ticker), f.get("date", ""))
                for f in _panel_rows("mergers", panels["mergers"])]
        st.markdown(_panel_html("Merger Documents", rows), unsafe_allow_html=True)
        _more_control("mergers", len(panels["mergers"]), None)

    # ── Export ───────────────────────────────────────────────────────────
    if filings:
        table_export(pd.DataFrame(filings), f"recent_documents_{ticker}",
                     key=f"exp_rd_{ticker}")
        st.caption(f"{len(filings)} EDGAR filings in range · click any document "
                   "name for viewer / HTML / PDF options · PDFs are rendered "
                   "from the EDGAR HTML on demand")
