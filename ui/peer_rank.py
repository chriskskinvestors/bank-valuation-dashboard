"""
Peer Rank — the one consolidated place a bank is ranked against its peers.

For the selected bank, shows where it sits versus same-asset-size peers across
every headline metric: value, rank (#3 of 18), percentile bar, and peer median —
grouped into the reads an analyst forms first (Profitability / Credit / Capital /
Funding). This replaces scattering peer stats across every tab.

Peers = the banks the firm tracks (watchlist metrics) in the same asset-size tier.
"""
from __future__ import annotations
import html as _html

import pandas as pd
import streamlit as st

from data.bank_mapping import get_name
from analysis.peer_groups import (
    metric_percentile_context, get_peer_group_for_bank, _higher_is_better,
)
from ui.chrome import table_export, title_bar


# Metric groups for the scorecard. Missing metrics / thin cohorts auto-skip.
RANK_GROUPS = [
    ("Profitability", ["roaa", "roatce_normalized", "nim", "efficiency_ratio"]),
    ("Credit quality", ["npl_ratio", "nco_ratio", "reserve_coverage_pct"]),
    ("Capital", ["cet1_ratio", "total_capital_ratio", "leverage_ratio"]),
    ("Funding", ["nonint_dep_pct", "loans_to_deposits"]),
]
_ALL_KEYS = [k for _, ks in RANK_GROUPS for k in ks]

_LABELS = {
    "roaa": "ROAA", "roatce_normalized": "ROATCE", "nim": "NIM",
    "efficiency_ratio": "Efficiency", "npl_ratio": "NPL ratio",
    "nco_ratio": "NCO ratio", "reserve_coverage_pct": "Reserve / NPL",
    "cet1_ratio": "CET1", "total_capital_ratio": "Total capital",
    "leverage_ratio": "Leverage", "nonint_dep_pct": "Non-int deposits",
    "loans_to_deposits": "Loans / deposits",
}


def _ordinal(n) -> str:
    n = int(round(n))
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _color(p: float) -> str:
    if p >= 75:
        return "#059669"
    if p >= 50:
        return "#0891b2"
    if p >= 25:
        return "#d97706"
    return "#dc2626"


def _band_word(p: float):
    if p >= 75:
        return "top-quartile", "#059669"
    if p >= 50:
        return "above peers", "#0891b2"
    if p >= 25:
        return "below peers", "#d97706"
    return "bottom-quartile", "#dc2626"


def _resolve_cohort(all_metrics: list[dict] | None) -> list[dict]:
    """Use the passed cohort; empty stays empty (rendered as an honest empty
    state). The old `from app import get_watchlist_cohort` fallback could never
    succeed in production — importing app re-executes the whole script, whose
    module-level st.set_page_config raises mid-render — so it always fell into
    the swallowed except (audit #37). The one real caller (company_nav) always
    passes the cohort."""
    return all_metrics or []


def _ensure_self(all_metrics: list[dict], ticker: str) -> list[dict]:
    """Make sure the subject bank is in the cohort. If it isn't, we render
    without it rather than trying to load it here: the old `from app import
    load_single_bank_metrics_cached` fallback could never succeed (same
    whole-script re-execution hazard as _get_cohort, audit #37)."""
    return list(all_metrics or [])


def _metric_row(key: str, d: dict) -> str:
    label = _LABELS.get(key, d.get("label", key))
    val = d["value"]
    p = d["percentile"]
    color = _color(p)
    rank, out_of = d.get("rank"), d.get("out_of")
    rank_txt = f"#{rank} of {out_of}" if rank and out_of else ""
    return (
        '<div style="display:flex;align-items:center;gap:10px;padding:5px 0;'
        'border-bottom:1px solid rgba(148,163,184,0.12);">'
        f'<div style="width:140px;flex-shrink:0;font-size:0.82rem;color:#334155;">{_html.escape(label)}</div>'
        f'<div style="width:74px;flex-shrink:0;font-size:0.9rem;font-weight:700;color:#0f172a;">{val:.2f}%</div>'
        '<div style="flex:1;height:9px;background:#eef2f7;border-radius:0;min-width:60px;">'
        f'<div style="width:{max(2, min(100, p)):.0f}%;height:100%;background:{color};border-radius:0;"></div>'
        '</div>'
        f'<div style="width:150px;flex-shrink:0;text-align:right;font-size:0.78rem;color:{color};font-weight:700;">'
        f'{_ordinal(p)} pctile <span style="color:#94a3b8;font-weight:500;">· {rank_txt}</span></div>'
        f'<div style="width:96px;flex-shrink:0;text-align:right;font-size:0.74rem;color:#94a3b8;">med {d["median"]:.2f}%</div>'
        '</div>'
    )


def render_peer_rank(ticker: str, all_metrics: list[dict]):
    """Render the consolidated Peer Rank scorecard for a bank."""
    name = get_name(ticker)
    title_bar(f"{name} ({ticker})", "Peer Rank")

    metrics = _ensure_self(_resolve_cohort(all_metrics), ticker)

    # ── Peer-set toggle: asset size vs business mix ───────────────────
    mode_label = st.radio(
        "Peer set", ["Asset size", "Business mix"], horizontal=True,
        key=f"peerrank_mode_{ticker}",
        help="Asset size = banks in the same total-asset tier. Business mix = "
             "banks with the same dominant balance-sheet profile (CRE-heavy, "
             "C&I-focused, mortgage-heavy, retail-heavy, diversified).",
    )
    mode = "size" if mode_label == "Asset size" else "mix"

    ctx = metric_percentile_context(ticker, metrics, metric_keys=_ALL_KEYS, mode=mode)
    meta = ctx.pop("_meta", {})
    tier = meta.get("tier")
    n = meta.get("cohort_size", 0)

    if not ctx or n < 6:
        st.info(
            f"Not enough **{mode_label.lower()}** peers loaded to rank this bank yet. "
            "Open **Home** or **Screen & Compare** once to load the "
            "watchlist, then return — or this peer set has too few tracked banks for "
            "a meaningful ranking (try the other peer set)."
        )
        return

    set_desc = ("the same asset-size tier" if mode == "size"
                else "the same business-mix profile")
    st.caption(
        f"Ranked against **{n}** tracked **{_html.escape(str(tier))}** peers "
        f"({set_desc}). Percentile is goodness-adjusted — higher is always better, "
        "including for efficiency / NPL / NCO."
    )

    # ── At-a-glance verdict by category ────────────────────────────────
    glance = []
    for gname, keys in RANK_GROUPS:
        ps = [ctx[k]["percentile"] for k in keys if k in ctx]
        if not ps:
            continue
        word, color = _band_word(sum(ps) / len(ps))
        glance.append(f'{gname}: <strong style="color:{color};">{word}</strong>')
    if glance:
        st.markdown(
            '<div style="font-size:0.92rem;color:#0f172a;background:rgba(148,163,184,0.06);'
            'border-radius:0;padding:10px 14px;margin:2px 0 12px;">'
            '<span style="color:#64748b;">At a glance — </span>'
            + " &nbsp;·&nbsp; ".join(glance) + '</div>',
            unsafe_allow_html=True,
        )

    # ── Scorecard: grouped by category, or flat-sorted by percentile ───
    view = st.radio(
        "Sort", ["By category", "Strongest first", "Weakest first"],
        horizontal=True, key=f"peerrank_view_{ticker}",
        help="By category = grouped reads (Profitability / Credit / Capital / "
             "Funding). Strongest / Weakest first = a single list of every metric "
             "ranked by goodness percentile, so the standouts and the soft spots "
             "rise to the top.",
    )
    if view == "By category":
        for gname, keys in RANK_GROUPS:
            rows = [_metric_row(k, ctx[k]) for k in keys if k in ctx]
            if not rows:
                continue
            st.markdown(
                f'<div style="font-size:0.7rem;text-transform:uppercase;letter-spacing:.04em;'
                f'color:#64748b;font-weight:700;margin:10px 0 2px;">{gname}</div>'
                + "".join(rows),
                unsafe_allow_html=True,
            )
    else:
        items = [(k, ctx[k]) for k in _ALL_KEYS if k in ctx]
        items.sort(key=lambda kv: kv[1]["percentile"],
                   reverse=(view == "Strongest first"))
        st.markdown("".join(_metric_row(k, d) for k, d in items),
                    unsafe_allow_html=True)

    st.caption(
        "Rank #1 = best in the peer set. Bars show the goodness percentile. "
        "Peer median is the same-tier median for context."
    )
    # Underlying numeric scorecard (value / percentile / rank / median)
    exp_df = pd.DataFrame([
        {"Metric": _LABELS.get(k, k), "Value": ctx[k]["value"],
         "Percentile": ctx[k]["percentile"], "Rank": ctx[k].get("rank"),
         "Out of": ctx[k].get("out_of"), "Peer median": ctx[k]["median"]}
        for k in _ALL_KEYS if k in ctx
    ])
    table_export(exp_df, f"peer_rank_{ticker}", key=f"exp_peer_rank_{ticker}")

    # ── Full leaderboard for a chosen metric ───────────────────────────
    st.markdown("---")
    ranked_keys = [k for k in _ALL_KEYS if k in ctx]
    pick = st.selectbox(
        "Full peer leaderboard for:",
        options=ranked_keys,
        format_func=lambda k: _LABELS.get(k, k),
        key=f"peerrank_leaderboard_{ticker}",
    )
    if pick:
        _render_leaderboard(ticker, metrics, pick, mode)


def _render_leaderboard(ticker: str, metrics: list[dict], key: str, mode: str):
    """Rank every cohort bank on one metric, subject highlighted."""
    cohort = get_peer_group_for_bank(ticker, metrics, mode=mode)
    hib = _higher_is_better(key)
    rows = [(m.get("ticker"), m.get(key)) for m in cohort if m.get(key) is not None]
    rows.sort(key=lambda r: r[1], reverse=hib)  # best first

    cells = []
    for i, (tk, v) in enumerate(rows, start=1):
        is_self = (tk == ticker)
        bg = "background:rgba(37,99,235,0.10);" if is_self else (
            "background:rgba(148,163,184,0.04);" if i % 2 else "")
        weight = "700" if is_self else "500"
        marker = "▸ " if is_self else ""
        nm = _html.escape((get_name(tk) or "")[:34])
        cells.append(
            f'<tr style="{bg}">'
            f'<td style="padding:5px 10px;color:#64748b;width:46px;">#{i}</td>'
            f'<td style="padding:5px 10px;font-weight:{weight};color:#0f172a;white-space:nowrap;">'
            f'{marker}<a href="?bank={_html.escape(str(tk), quote=True)}" target="_self" '
            f'style="text-decoration:none;color:inherit;" '
            f'title="Open {_html.escape(str(tk))} company page">{_html.escape(str(tk))}</a></td>'
            f'<td style="padding:5px 10px;color:#475569;">{nm}</td>'
            f'<td style="padding:5px 10px;text-align:right;font-weight:{weight};color:#0f172a;">{v:.2f}%</td>'
            f'</tr>'
        )
    table = (
        f'<div style="font-size:0.72rem;color:#64748b;margin-bottom:4px;">'
        f'{_LABELS.get(key, key)} — {"higher" if hib else "lower"} is better · {len(rows)} banks</div>'
        '<table style="width:100%;border-collapse:collapse;font-size:0.84rem;'
        'border:1px solid rgba(148,163,184,0.22);border-radius:0;overflow:hidden;">'
        '<thead><tr style="background:rgba(241,245,249,0.7);color:#0f172a;">'
        '<th style="padding:6px 10px;text-align:left;">Rank</th>'
        '<th style="padding:6px 10px;text-align:left;">Ticker</th>'
        '<th style="padding:6px 10px;text-align:left;">Bank</th>'
        '<th style="padding:6px 10px;text-align:right;">Value</th>'
        '</tr></thead><tbody>' + "".join(cells) + '</tbody></table>'
    )
    st.markdown(table, unsafe_allow_html=True)
    # Underlying numeric leaderboard
    exp_df = pd.DataFrame([
        {"Rank": i, "Ticker": tk, "Bank": get_name(tk), "Value": v}
        for i, (tk, v) in enumerate(rows, start=1)
    ])
    table_export(exp_df, f"peer_leaderboard_{ticker}_{key}",
                 key=f"exp_peer_leaderboard_{ticker}_{key}")
