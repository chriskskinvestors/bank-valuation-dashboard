"""
Generic table renderer — works for any tab by accepting a column list.
"""

import html as _html

import pandas as pd
import streamlit as st

from config import METRICS_BY_KEY
from data.bank_mapping import get_name, BANK_MAP
from utils.formatting import format_value, get_bg_color
from analysis.peer_groups import compute_peer_percentile

# Percentile heatmap scale (mirrors ui.peer_comparison's so Screen and Compare
# read the same): (min_effective_percentile, bg, fg, bold).
_HEAT_SCALE = [
    (80, "#d1fae5", "#065f46", True),
    (60, "#ecfdf5", "#047857", False),
    (40, "#f8fafc", "#475569", False),
    (20, "#fef2f2", "#991b1b", False),
    (0,  "#fee2e2", "#991b1b", True),
]


def _heat_color(pct: float | None, higher_better: bool = True) -> str:
    """Background style for a percentile rank (0-100); '' when n/a or neutral."""
    if pct is None:
        return ""
    eff = pct if higher_better else (100 - pct)
    for floor, bg, fg, bold in _HEAT_SCALE:
        if eff >= floor:
            return f"background-color:{bg};color:{fg};" + ("font-weight:600;" if bold else "")
    return ""


def _fast_name_lookup(tickers: pd.Series) -> pd.Series:
    """
    Vectorized bank name lookup.

    For known tickers, uses the static BANK_MAP dict (O(1) hash lookup per row).
    For unknown tickers, falls back to get_name() which may do dynamic resolution.
    """
    def _lookup_one(t):
        if t is None or pd.isna(t):
            return ""
        entry = BANK_MAP.get(t)
        if isinstance(entry, dict):
            return entry.get("name") or t
        return None  # signal "needs fallback"

    known_names = tickers.map(_lookup_one)

    # Slow path for unknowns: use get_name (which may hit resolve_ticker)
    unknown_mask = known_names.isna()
    if unknown_mask.any():
        def _safe_get_name(t):
            if t is None or pd.isna(t):
                return ""
            try:
                return get_name(t)
            except Exception:
                return str(t)
        known_names.loc[unknown_mask] = tickers.loc[unknown_mask].map(_safe_get_name)

    return known_names


def _render_legend():
    """One-line legend for get_bg_color's 3-band cell shading (utils/formatting),
    using the design-system status dots (no emoji, per DESIGN-SYSTEM.md)."""
    dot = ('<span class="ksk-dot {k}" style="margin-right:4px;"></span>{lbl}')
    parts = " &nbsp;·&nbsp; ".join([
        dot.format(k="ok", lbl="good (at/above target)"),
        dot.format(k="warn", lbl="caution (near the line)"),
        dot.format(k="bad", lbl="poor (past the warn level)"),
        "unshaded = no threshold / n/a",
    ])
    st.markdown(
        f'<div style="font-size:var(--fs-xs);color:var(--text-secondary);'
        f'margin:2px 0 6px;">Cell shading — {parts}</div>',
        unsafe_allow_html=True,
    )


def _render_heatmap_legend():
    """Legend for the percentile heatmap (same scale that colors the cells)."""
    labels = ["Top 20%", "60–80th", "40–60th", "20–40th", "Bottom 20%"]
    chips = "".join(
        f'<span style="background:{bg};color:{fg};padding:3px 9px;border-radius:4px;'
        f'{"font-weight:600;" if bold else ""}">{lbl}</span>'
        for (_floor, bg, fg, bold), lbl in zip(_HEAT_SCALE, labels))
    st.markdown(
        '<div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;'
        'font-size:var(--fs-xs);margin:2px 0 6px;">'
        '<span style="color:var(--text-secondary);">Rank within current results:</span>'
        f'{chips}</div>',
        unsafe_allow_html=True,
    )


def render_generic_table(
    metrics_data: list[dict],
    columns: list[str],
    table_key: str = "default",
    show_legend: bool = False,
    heatmap: bool = False,
):
    """
    Render a screening table for any tab.

    metrics_data: list of dicts from build_all_bank_metrics()
    columns: list of metric keys to display
    table_key: unique key for Streamlit widget state
    show_legend: render the color legend above the table
    heatmap: color each numeric cell by its percentile rank within the displayed
        rows (vs the default threshold-based shading)
    """
    if not metrics_data:
        st.warning("No banks match the current scope and filters.")
        return None

    if show_legend:
        _render_heatmap_legend() if heatmap else _render_legend()

    df = pd.DataFrame(metrics_data)
    df.insert(0, "Bank", _fast_name_lookup(df["ticker"]))
    valid_cols = [c for c in columns if c in df.columns]

    # Per-column numeric values for the heatmap (percentiles vs the displayed set).
    col_numeric = ({c: [r.get(c) for r in metrics_data
                        if isinstance(r.get(c), (int, float))] for c in valid_cols}
                   if heatmap else {})

    # Full-grid SNL HTML table (design system): hairline grid, small-caps headers,
    # tabular right-aligned numbers, negatives red, missing → "—", and a clickable
    # ticker that deep-links to the Company page (?bank=). st.dataframe was dropped
    # here because its canvas renderer ignores na_rep (showed literal "None") and
    # can't carry per-row deep-links.
    heads = ['<th>Ticker</th>', '<th class="nm">Bank</th>']
    for c in valid_cols:
        heads.append(f'<th>{_html.escape(METRICS_BY_KEY.get(c, {}).get("label", c))}</th>')
    thead = "<tr>" + "".join(heads) + "</tr>"

    body = []
    for rec in df.to_dict("records"):
        tk = str(rec.get("ticker") or "")
        cert = rec.get("_fdic_cert")
        defunct = rec.get("_defunct") and cert and not (
            isinstance(cert, float) and pd.isna(cert))
        if defunct:
            # As-of defunct / since-acquired bank (no Company page) → FDIC profile.
            tk_cell = ('<td><a class="lnk tk" href="https://banks.data.fdic.gov/'
                       f'bankfind-suite/bankfind/details/{int(cert)}" target="_blank" '
                       f'rel="noopener">{_html.escape(tk)}</a></td>')
        elif tk:
            # A covered company → deep-link to its Company page.
            tk_cell = (f'<td><a class="lnk tk" href="?bank={_html.escape(tk, quote=True)}" '
                       f'target="_self">{_html.escape(tk)}</a></td>')
        else:
            tk_cell = f'<td class="tk">{_html.escape(tk)}</td>'
        cells = [
            tk_cell,
            f'<td class="nm">{_html.escape(str(rec.get("Bank") or ""))}</td>',
        ]
        for c in valid_cols:
            m = METRICS_BY_KEY.get(c, {})
            v = rec.get(c)
            missing = v is None or (isinstance(v, float) and pd.isna(v))
            txt = "—" if missing else _html.escape(
                format_value(v, m.get("format", "number"), m.get("decimals", 2)))
            if heatmap and not missing and isinstance(v, (int, float)):
                rule = m.get("color_rule")
                if rule == "higher_better":
                    bg = _heat_color(compute_peer_percentile(v, col_numeric[c]), True)
                elif rule == "lower_better":
                    bg = _heat_color(compute_peer_percentile(v, col_numeric[c]), False)
                else:
                    bg = ""
            else:
                bg = get_bg_color(c, v)
            cls = "num"
            if not bg and isinstance(v, (int, float)) and not missing and v < 0:
                cls += " neg"
            style = f' style="{bg}"' if bg else ""
            cells.append(f'<td class="{cls}"{style}>{txt}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")

    st.markdown(
        "<style>"
        ".scrn-wrap{max-height:660px;overflow:auto;border:0.5px solid var(--grid-head);}"
        ".scrn-wrap thead th{position:sticky;top:0;z-index:2;}"
        ".scrn-wrap td.nm,.scrn-wrap th.nm{text-align:left;color:var(--text-secondary);"
        "max-width:240px;overflow:hidden;text-overflow:ellipsis;}"
        ".scrn-wrap a.tk{font-weight:700;text-decoration:none;}"
        "</style>"
        f'<div class="scrn-wrap"><table class="ksk-grid">'
        f'<thead>{thead}</thead><tbody>{"".join(body)}</tbody></table></div>',
        unsafe_allow_html=True,
    )

    return df
