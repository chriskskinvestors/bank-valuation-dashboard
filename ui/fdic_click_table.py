"""
Reusable click-to-source FDIC table — the Financial Highlights table engine
generalised so any Templated-Financials subtab can render the same left-side
"table + provenance" (left table / right charts is the page pattern).

It drives the SAME proven `_build_component` overlay engine with the SAME FDIC
fields and formatters Financial Highlights uses, so values match cell-for-cell
— no new number paths (audit cardinal rule: never a plausible-wrong number).
"""
from __future__ import annotations

import pandas as pd
import streamlit.components.v1 as components

from data.bank_mapping import get_fdic_cert, get_cik, get_name
from data.loaders import load_fdic_hist
from utils.formatting import (num as _num, thou as _thou, pct as _pct,
                              usd_compact_from_thousands as _usd)
from ui.financial_highlights import (
    _build_component, _DEFS, _disp_date, _month, _year, _ratio_pct,
)


def _setup_periods(hist_records, period):
    """(keys, labels, recs, asof) for Annual (last 5 FY-ends) or Quarterly
    (last 8 quarters) — identical period selection to Financial Highlights."""
    hist = pd.DataFrame(hist_records)
    if hist.empty or "REPDTE" not in hist.columns:
        return [], {}, {}, {}
    hist["_y"] = hist["REPDTE"].apply(_year)
    hist["_m"] = hist["REPDTE"].apply(_month)
    hist = hist.sort_values("REPDTE")
    if period == "Annual":
        ye = hist[hist["_m"] == 12].dropna(subset=["_y"])
        years = sorted({int(y) for y in ye["_y"]})[-5:]
        keys = years
        labels = {y: f"FY{y}" for y in years}
        recs = {y: ye[ye["_y"] == y].iloc[0].to_dict() for y in years}
    else:
        q = hist.tail(8)
        keys, labels, recs = [], {}, {}
        for _, r in q.iterrows():
            d = pd.to_datetime(r["REPDTE"])
            k = d.strftime("%Y-%m-%d")
            keys.append(k)
            labels[k] = f"Q{(d.month - 1) // 3 + 1} '{str(d.year)[2:]}"
            recs[k] = r.to_dict()
    asof = {k: _disp_date(recs[k].get("REPDTE")) for k in keys}
    return keys, labels, recs, asof


def render_fdic_click_table(ticker, sections, *, period="Annual"):
    """Render the click-to-source FDIC table for a metric `sections` spec.

    sections: list of (section_name, rows); each row is (label, kind, *args):
      (label, "pct", FIELD)                          FDIC field as a percent
      (label, "dollar", FIELD)                       FDIC field as $000 compact
      (label, "ratio", NUM, DEN, "num lbl", "den lbl")   NUM/DEN*100, both sourced
    Returns True if it rendered, False when no FDIC history is available.
    """
    cert = get_fdic_cert(ticker)
    cik = get_cik(ticker)
    entity = f"{get_name(ticker)} ({ticker})"
    keys, labels, recs, asof = _setup_periods(load_fdic_hist(ticker), period)
    if not keys:
        return False
    fdic_link = f"https://banks.data.fdic.gov/bankfind-suite/bankfind/details/{cert}"
    sec_link = (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                f"&CIK={cik}&type=10-K") if cik else fdic_link

    def _P(v, metric, unit, ref, terms, op, reported, k):
        return {"v": v, "calc": {
            "metric": metric, "entity": entity, "source": "FDIC Call Report",
            "asof": asof[k], "unit": unit, "ref": ref,
            "definition": _DEFS.get(metric, ""), "terms": terms, "op": op,
            "reported": reported, "link": fdic_link}}

    def _cell(label, kind, args, k):
        if kind == "pct":
            (field,) = args
            raw = _num(recs[k].get(field))
            return _P(_pct(raw), label, "%", f"FDIC field {field}",
                      [{"label": label + " (as reported)", "val": _pct(raw)}],
                      None, True, k)
        if kind == "dollar":
            (field,) = args
            raw = _num(recs[k].get(field))
            return _P(_usd(raw), label, "$ in thousands", f"FDIC field {field}",
                      [{"label": label, "val": _thou(raw) + " ($000)"}], None, True, k)
        if kind == "ratio":
            nf, df_, nlbl, dlbl = args
            n, d = _num(recs[k].get(nf)), _num(recs[k].get(df_))
            return _P(_ratio_pct(n, d), label, "%", "Computed from Call Report",
                      [{"label": nlbl, "val": _thou(n) + " ($000)"},
                       {"label": dlbl, "val": _thou(d) + " ($000)"}],
                      f"{nlbl} ÷ {dlbl} × 100", False, k)
        return {"v": "—", "calc": None}

    cells, rows_html, ri = {}, [], 0
    for sec_name, rows in sections:
        rows_html.append(f'<tr><td class="sec" colspan="{len(keys) + 1}">{sec_name}</td></tr>')
        for row in rows:
            label, kind, args = row[0], row[1], row[2:]
            tds = [f'<td class="lbl">{label}</td>']
            for ci, k in enumerate(keys):
                payload = _cell(label, kind, args, k)
                cid = f"{ri}_{ci}"
                if payload.get("calc"):
                    cells[cid] = payload["calc"]
                    tds.append(f'<td class="val" data-cid="{cid}">{payload["v"]}</td>')
                else:
                    tds.append(f'<td class="val dead">{payload.get("v", "—")}</td>')
            zebra = ' class="zebra"' if ri % 2 == 1 else ""
            rows_html.append(f'<tr{zebra}>{"".join(tds)}</tr>')
            ri += 1
    head = ('<th class="lblh">($ in thousands unless noted)</th>'
            + "".join(f'<th class="colh">{labels[k]}</th>' for k in keys))
    height = 96 + 23 * (ri + len(sections) + 1)
    html = _build_component(head, "".join(rows_html), cells, entity, fdic_link, sec_link)
    components.html(html, height=height, scrolling=False)
    return True
