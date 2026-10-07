"""Company Reported › Income Statement / Balance Sheet (Annual | Quarterly).

The statement is stitched from the bank's own 10-K/10-Q R-files by
data/sec_statements (as_reported_statement_multiyear / _multiquarter). This
page renders it with its PROVENANCE (REVIEW-2026-09-24 P1-6, owner decision):

  * a cell a later filing restated shows the later value, marked "*", and
    clicking it opens the as-originally-filed figure and both filings;
  * a column only the legacy registrant's own filings report (after a
    reverse merger — Beacon's pre-merger Berkshire Hills quarters) carries a
    "‡" header marker and a caption naming that entity, so two companies are
    never presented as one silently;
  * a column-level caveat (a quarter partly restated in a 10-K, a period
    recast on the accounting acquirer's basis) is listed under the table.

Shared formatting / export / trend helpers stay in ui.financials_statements
(the Company Reported helper family); this module owns only the two
statement pages.
"""
from __future__ import annotations

import html as _h
import re

import streamlit as st

from data.bank_mapping import get_bank_info
from ui.financial_highlights import _build_component
from ui.financials_statements import (
    _CR_BALANCE_TRENDS, _CR_INCOME_TRENDS, _CR_STMT_PAGE, _CR_STMT_UNITS,
    _cr_export, _cr_fmt, _cr_line_kind, _cr_provenance, _cr_statement_trends,
)

LEGACY_MARK = " ‡"        # header suffix: a legacy-registrant column
RESTATED_MARK = "*"       # cell suffix: a later filing restated this value


def render_company_statement(ticker: str, stype: str):
    """Company-Reported statement (stype = "income" | "balance"), stitched from
    the bank's own SEC filings. An Annual/Quarterly toggle switches between the
    multi-year 10-K stitch (default) and the discrete-single-quarter 10-Q stitch
    (12 quarters). Faithful to the company's own line items; blank where a line
    wasn't reported that period."""
    info = get_bank_info(ticker)
    cik = info.get("cik") if info else None
    if not cik:
        from ui.states import empty_state
        empty_state('No SEC filer mapping for this bank')
        return
    view = st.radio("Period", ["Annual", "Quarterly"], horizontal=True,
                    key=f"cr_period_{stype}_{ticker}", label_visibility="collapsed")
    if view == "Quarterly":
        _render_company_statement_quarterly(ticker, stype, cik, info)
        return
    _render_company_statement_annual(ticker, stype, cik, info)


def _cell_card(note: dict, *, label: str, col: str, shown: str, kind: str,
               entity: str) -> dict:
    """Click-through card (ui.financial_highlights._build_component CELLS
    schema) for one restated cell: the as-filed value and its filing, then the
    displayed value and the filing that restated it."""
    who = f" — {note['original_entity']}" if note.get("original_entity") else ""
    orig = _cr_fmt(kind, note.get("original"))
    later = note.get("restated_in") or "a later filing"
    terms = [
        {"label": "As originally filed", "val": orig,
         "sub": f"{note.get('original_source') or 'earlier filing'}{who}",
         **({"doc": {"url": note["original_url"], "label": note["original_source"]}}
            if note.get("original_url") else {})},
        {"label": "Shown (latest filed)", "val": shown, "sub": later},
    ]
    if note.get("not_re_reported"):
        op = (f"As originally filed: {orig}. The period was restated in {later}, "
              f"which does not re-report this line — n/a rather than a superseded figure.")
    else:
        op = f"As originally filed: {orig}, restated in {later}"
    return {"metric": label, "entity": entity, "source": later, "asof": col,
            "unit": "$/share" if kind == "eps" else ("shares" if kind == "shares" else "USD"),
            "ref": "Company Reported", "definition": "",
            "terms": terms, "op": op,
            "reported": False, "link": note.get("restated_url") or ""}


def _cr_table(col_labels: list, rows: list, cells: dict, *, entity: str,
              src: str | None) -> None:
    """The Company-Reported statement grid through the SAME _build_component
    the other statements use. rows: [{"label", "values":[str|None,...],
    "cids":[str|None,...], "kind":"data"|"header"}]; a cell with a cid is
    clickable (its card in `cells`), the rest are plain."""
    from streamlit.components import v1 as _stc
    ncol = len(col_labels)
    body, ri = [], 0
    for r in rows:
        if r.get("kind") == "header":
            body.append(f'<tr><td class="sec" colspan="{ncol + 1}">'
                        f'{_h.escape(str(r["label"]))}</td></tr>')
            continue
        tds = [f'<td class="lbl">{_h.escape(str(r["label"]))}</td>']
        for v, cid in zip(r["values"], r.get("cids") or [None] * len(r["values"])):
            s = "" if v is None or v == "" else _h.escape(str(v))
            if s == "":
                tds.append('<td class="val dead"></td>')
                continue
            neg = " neg" if s.strip().startswith("(") else ""
            dc = f' data-cid="{cid}"' if cid else ""
            tds.append(f'<td class="val{neg}"{dc}>{s}</td>')
        zebra = ' class="zebra"' if ri % 2 == 1 else ""
        body.append(f'<tr{zebra}>{"".join(tds)}</tr>')
        ri += 1
    head = ('<th class="lblh">(figures in USD)</th>'
            + "".join(f'<th class="colh">{_h.escape(str(lb))}</th>' for lb in col_labels))
    height = 96 + 23 * (ri + 4)
    html = _build_component(head, "".join(body), cells, entity, None, src)
    if len(col_labels) <= 2:
        with st.columns([2, 3])[0]:
            _stc.html(html, height=height, scrolling=False)
    else:
        _stc.html(html, height=height, scrolling=False)


def _provenance_rows(stmt: dict, cols: list, entity: str):
    """(rows, xrows, cells, captions) for a stitched statement whose periods
    are NEWEST-first, displayed oldest → newest under `cols`. Captions name
    the legacy-registrant columns and every column-level caveat."""
    n = len(stmt["periods"])
    rev = lambda xs: list(reversed((list(xs or []) + [None] * n)[:n]))   # noqa: E731
    rows, xrows, cells = [], [], {}
    for ri, r in enumerate(stmt["rows"]):
        if r["header"]:
            rows.append({"label": r["label"], "values": [], "kind": "header"})
            xrows.append((r["label"], "header", []))
            continue
        vals = rev(r["values"])
        notes = rev(r.get("restated"))
        lk = _cr_line_kind(r)
        shown, cids = [], []
        for ci, (v, note) in enumerate(zip(vals, notes)):
            txt = _cr_fmt(lk, v)
            if note and not txt and note.get("not_re_reported"):
                txt = "n/a"                      # superseded, restated value unknown
            if note and txt:
                cid = f"r{ri}c{ci}"
                cells[cid] = _cell_card(note, label=r["label"],
                                        col=cols[ci].replace(LEGACY_MARK, ""),
                                        shown=txt, kind=lk, entity=entity)
                cids.append(cid)
                txt += RESTATED_MARK
            else:
                cids.append(None)
            shown.append(txt)
        rows.append({"label": r["label"], "values": shown, "cids": cids, "kind": "data"})
        xrows.append((r["label"], lk, vals))
    captions = []
    ents = rev(stmt.get("period_entity"))
    for ent in dict.fromkeys(e for e in ents if e):
        which = ", ".join(c.replace(LEGACY_MARK, "") for c, e in zip(cols, ents) if e == ent)
        captions.append(f"‡ {which}: reported by **{ent}** — the pre-merger registrant's "
                        f"own filings. The current company's filings do not present "
                        f"these periods, so they are a different entity's figures.")
    for c, pn in zip(cols, rev(stmt.get("period_notes"))):
        if pn:
            captions.append(f"{c.replace(LEGACY_MARK, '')}: {pn}")
    if cells:
        # "\*": a bare leading "*" would render as a markdown bullet
        captions.append("\\* restated or superseded by a later filing — click the cell "
                        "for the figure as originally filed.")
    return rows, xrows, cells, captions


def _legacy_cols(stmt: dict, cols: list) -> list:
    """Column labels with the legacy-registrant marker appended."""
    n = len(cols)
    ents = list(reversed((list(stmt.get("period_entity") or []) + [None] * n)[:n]))
    return [c + LEGACY_MARK if e else c for c, e in zip(cols, ents)]


def _legacy_provenance(stmt: dict, cols: list) -> dict:
    """Extra Source-sheet row naming the legacy-registrant columns (none when
    the statement is one company throughout)."""
    n = len(cols)
    ents = list(reversed((list(stmt.get("period_entity") or []) + [None] * n)[:n]))
    if not any(ents):
        return {}
    return {"Legacy-registrant columns (‡)": "; ".join(
        f"{c}: {e}" for c, e in zip(cols, ents) if e)}


def _trend_stmt(stmt: dict) -> dict:
    """The statement the trend charts plot: legacy-registrant columns dropped,
    so a line never joins two companies' figures as one series."""
    ents = stmt.get("period_entity")
    if not ents or not any(ents):
        return stmt
    keep = [i for i, e in enumerate(ents) if not e]
    return {**stmt, "periods": [stmt["periods"][i] for i in keep],
            "rows": [r if r["header"] else
                     {**r, "values": [r["values"][i] if i < len(r["values"]) else None
                                      for i in keep]}
                     for r in stmt["rows"]]}


def _render_company_statement_annual(ticker, stype, cik, info):
    """Multi-year Company-Reported statement, stitched from the bank's recent
    10-Ks. Faithful to the company's own line items; blank where a line wasn't
    reported that year."""
    try:
        from data.sec_statements import as_reported_statement_multiyear
        res = as_reported_statement_multiyear(cik, stype, n_years=5)
    except Exception:
        res = None
    if not res:
        st.caption("Company-reported statement not available from this filer's 10-Ks — n/a.")
        return
    stmt, filings, latest = res["statement"], res["filings"], res["meta"]
    src = (f"https://www.sec.gov/Archives/edgar/data/{int(latest['cik'])}/"
           f"{latest['accession']}/{latest['doc']}")

    def _yr(p):
        m = re.search(r"\d{4}", p or "")
        return m.group() if m else (p or "")

    _has_persh = any(_cr_line_kind(r) != "usd"
                     for r in stmt["rows"] if not r["header"])
    _persh_note = " EPS in \\$/share, shares in millions;" if _has_persh else ""
    st.caption(f"Source: company 10-K filings — latest [{latest['date']}]({src}); "
               f"{len(stmt['periods'])} fiscal years stitched from {len(filings)} filings. "
               f"Dollar lines \\$-compact;{_persh_note} "
               f"blank = not separately reported that year.")
    cols = _legacy_cols(stmt, [f"FY{_yr(p)}" for p in stmt["periods"][::-1]])
    entity = f"{(info or {}).get('name') or ticker} ({ticker})"
    rows, xrows, cells, captions = _provenance_rows(stmt, cols, entity)

    _lt, _rt = st.columns([1, 1], vertical_alignment="top")
    with _lt:
        _cr_table(cols, rows, cells, entity=entity, src=src)
        for c in captions:
            st.caption(c)
        _cr_export(cols, xrows,
                   filename=f"{ticker}_cr_{stype}_annual_{latest['date']}",
                   key=f"exp_cr_{stype}_annual_{ticker}",
                   provenance={**_cr_provenance(
                       _CR_STMT_PAGE[stype], ticker, info, cik,
                       basis="Annual — fiscal years stitched from the company's 10-Ks",
                       source="Company 10-K filings (as-reported statements, stitched)",
                       src=src, latest_date=latest["date"], periods=cols,
                       units=_CR_STMT_UNITS), **_legacy_provenance(stmt, cols)})
    with _rt:
        _cr_statement_trends(_trend_stmt(stmt), ticker, f"cr{stype}",
                             _CR_INCOME_TRENDS if stype == "income" else _CR_BALANCE_TRENDS)


def _render_company_statement_quarterly(ticker, stype, cik, info):
    """Discrete-quarter Company-Reported statement (12 quarters), stitched from
    the bank's own 10-Qs (and 10-Ks for the year-end column). Income/cash-flow
    columns are TRUE single quarters: Q1–Q3 are a 10-Q's "three months ended"
    figure (the latest filing presenting that quarter); Q4 = annual 10-K minus
    the nine-month 10-Q (audit invariant A21 — a quarter is never a YTD
    cumulative). Balance-sheet columns are point-in-time quarter-end snapshots.
    A quarter that can't be cleanly derived is left blank, never guessed. All
    figures are as-reported and company-sourced (never FDIC)."""
    try:
        from data.sec_statements import as_reported_statement_multiquarter
        res = as_reported_statement_multiquarter(cik, stype, n_quarters=12)
    except Exception:
        res = None
    if not res:
        st.caption("Company-reported quarterly statement not available from this "
                   "filer's 10-Qs — n/a.")
        return
    stmt, filings, latest = res["statement"], res["filings"], res["meta"]
    src = (f"https://www.sec.gov/Archives/edgar/data/{int(latest['cik'])}/"
           f"{latest['accession']}/{latest['doc']}")

    _has_persh = any(_cr_line_kind(r) != "usd"
                     for r in stmt["rows"] if not r["header"])
    _persh_note = " EPS in \\$/share, shares in millions;" if _has_persh else ""
    _q4 = ("" if stype == "balance"
           else " Discrete quarters — Q4 = annual 10-K minus the nine-month 10-Q.")
    st.caption(f"Source: stitched from the bank's 10-Qs — latest [{latest['date']}]"
               f"({src}); {len(stmt['periods'])} quarters from {len(filings)} filings."
               f"{_q4} As-reported, company-sourced (never FDIC); each period from "
               f"the latest filing that reports it. Dollar lines "
               f"\\$-compact;{_persh_note} blank = not cleanly derivable.")
    cols = _legacy_cols(stmt, list(stmt["periods"][::-1]))   # compact "Q3'25" labels
    entity = f"{(info or {}).get('name') or ticker} ({ticker})"
    rows, xrows, cells, captions = _provenance_rows(stmt, cols, entity)

    _lt, _rt = st.columns([1, 1], vertical_alignment="top")
    with _lt:
        _cr_table(cols, rows, cells, entity=entity, src=src)
        for c in captions:
            st.caption(c)
        _cr_export(cols, xrows,
                   filename=f"{ticker}_cr_{stype}_quarterly_{latest['date']}",
                   key=f"exp_cr_{stype}_quarterly_{ticker}",
                   provenance={**_cr_provenance(
                       _CR_STMT_PAGE[stype], ticker, info, cik,
                       basis=("Quarterly — discrete quarters stitched from the company's "
                              "10-Qs/10-Ks" + ("" if stype == "balance" else
                                               "; Q4 = annual 10-K minus the nine-month 10-Q")),
                       source="Company 10-Q/10-K filings (as-reported statements, stitched)",
                       src=src, latest_date=latest["date"], periods=cols,
                       units=_CR_STMT_UNITS), **_legacy_provenance(stmt, cols)})
    with _rt:
        _cr_statement_trends(_trend_stmt(stmt), ticker, f"crq{stype}",
                             _CR_INCOME_TRENDS if stype == "income" else _CR_BALANCE_TRENDS)
