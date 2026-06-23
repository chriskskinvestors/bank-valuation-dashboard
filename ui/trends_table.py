"""Quarterly-trend table — one metric across quarter columns, one row per bank.

Rows come from data.as_of_metrics.metric_grid: {"ticker", "_fdic_cert", <label>: v}.
Rendered as the design-system ksk-grid (hairlines, tabular nums, conditional cell
shading via get_bg_color, "—" for n/a, ticker deep-links to the Company page).
"""
import html as _html

import pandas as pd
import streamlit as st

from config import METRICS_BY_KEY
from data.bank_mapping import get_name, BANK_MAP
from utils.formatting import format_value, get_bg_color


def render_trends_table(rows: list[dict], labels: list[str], metric_key: str):
    if not rows:
        st.warning("No data for this metric and scope.")
        return None

    m = METRICS_BY_KEY.get(metric_key, {})
    fmt, dec = m.get("format", "number"), m.get("decimals", 2)

    heads = ['<th>Ticker</th>', '<th class="nm">Bank</th>']
    heads += [f'<th>{_html.escape(lb)}</th>' for lb in labels]
    thead = "<tr>" + "".join(heads) + "</tr>"

    body = []
    for r in rows:
        tk = str(r.get("ticker") or "")
        entry = BANK_MAP.get(tk)
        if isinstance(entry, dict):
            name = entry.get("name") or tk
        else:
            try:
                name = get_name(tk)
            except Exception:
                name = tk
        tk_cell = (
            f'<td><a class="lnk tk" href="?bank={_html.escape(tk, quote=True)}" '
            f'target="_self">{_html.escape(tk)}</a></td>'
            if tk else f'<td class="tk">{_html.escape(tk)}</td>')
        cells = [tk_cell, f'<td class="nm">{_html.escape(str(name))}</td>']
        for lb in labels:
            v = r.get(lb)
            missing = v is None or (isinstance(v, float) and pd.isna(v))
            txt = "—" if missing else _html.escape(format_value(v, fmt, dec))
            bg = get_bg_color(metric_key, v)
            cls = "num"
            if not bg and isinstance(v, (int, float)) and not missing and v < 0:
                cls += " neg"
            style = f' style="{bg}"' if bg else ""
            cells.append(f'<td class="{cls}"{style}>{txt}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")

    st.markdown(
        "<style>"
        ".trnd-wrap{max-height:660px;overflow:auto;border:0.5px solid var(--grid-head);}"
        ".trnd-wrap thead th{position:sticky;top:0;z-index:2;}"
        ".trnd-wrap td.nm,.trnd-wrap th.nm{text-align:left;color:var(--text-secondary);"
        "max-width:220px;overflow:hidden;text-overflow:ellipsis;}"
        ".trnd-wrap a.tk{font-weight:700;text-decoration:none;}"
        "</style>"
        f'<div class="trnd-wrap"><table class="ksk-grid">'
        f'<thead>{thead}</thead><tbody>{"".join(body)}</tbody></table></div>',
        unsafe_allow_html=True,
    )
    return rows
