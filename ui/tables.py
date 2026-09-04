"""ksk_table — render a (pre-formatted) DataFrame in the house table
language (polish lane 2, owner-approved via the KSK Polish Pass mockup).

Replaces display-only ``st.dataframe`` sites: uppercase muted headers,
monospace tabular figures, hairline rows, hover highlight, semantic
red/green. NOT for tables that need st.dataframe's column sorting
(screeners) or LinkColumn machinery — those keep the widget.

Cells are plain text (HTML-escaped here). Values are expected already
formatted by utils/formatting — this module renders, never computes.
"""
from __future__ import annotations

import html
import re

import streamlit as st

# A cell counts as numeric-shaped (→ right-aligned, monospace) when it
# starts with a sign/digit/currency/paren. The em-dash absence marker is
# neutral everywhere.
_NUM_RE = re.compile(r"^[+\-$(]?\d|^[+\-]\$")
_DASH = {"—", "-", ""}


def _cell_class(val: str) -> str:
    if val.startswith("+"):
        return "pos"
    if val.startswith("-") or val.startswith("($") or val.startswith("(-"):
        return "neg"
    return ""


def ksk_table_html(df, *, signed_cols: tuple[str, ...] = (),
                   max_height_px: int | None = None) -> str:
    """The HTML for a ksk-grid table.

    signed_cols: columns whose +/- prefixed values color green/red.
    max_height_px: wrap in a scroll container when the table can be tall
    (mirrors the height=min(640, ...) idiom the dataframe sites used).
    """
    cols = list(df.columns)
    # Alignment per column: right when most non-dash values look numeric.
    aligns = {}
    for c in cols:
        vals = [str(v) for v in df[c].tolist() if str(v) not in _DASH]
        num = sum(1 for v in vals if _NUM_RE.match(v))
        aligns[c] = "num" if vals and num / len(vals) >= 0.6 else "txt"
    # First column is the label column (house convention: names left) even
    # when its values look numeric ("3M"/"6M"/"1Y" period labels).
    if cols:
        aligns[cols[0]] = "txt"

    out = ["<table>", "<thead><tr>"]
    for c in cols:
        out.append(f'<th class="{aligns[c]}">{html.escape(str(c))}</th>')
    out.append("</tr></thead><tbody>")
    for _, row in df.iterrows():
        out.append("<tr>")
        for c in cols:
            raw = row[c]
            try:
                import pandas as _pd
                v = "—" if raw is None or (_pd.isna(raw)) else str(raw)
            except (TypeError, ValueError):
                v = str(raw)
            klass = aligns[c]
            if c in signed_cols and v not in _DASH:
                sem = _cell_class(v)
                if sem:
                    klass += " " + sem
            out.append(f'<td class="{klass}">{html.escape(v)}</td>')
        out.append("</tr>")
    out.append("</tbody></table>")
    table = "".join(out)
    style = (f' style="max-height:{max_height_px}px;overflow-y:auto;"'
             if max_height_px else "")
    return f'<div class="ksk-grid kskt"{style}>{table}</div>'


def ksk_table(df, *, signed_cols: tuple[str, ...] = (),
              max_height_px: int | None = None) -> None:
    """Render `df` as a house-style table (see module docstring)."""
    if df is None or len(df) == 0:
        return
    st.markdown(ksk_table_html(df, signed_cols=signed_cols,
                               max_height_px=max_height_px),
                unsafe_allow_html=True)
