"""
Peer-percentile context strip.

Shows where a bank sits versus its same-asset-size peers on the headline
metrics — e.g. "ROAA 1.24% · 78th pctile · peer median 1.05%". This is the
context that separates an institutional tool from a raw data dump: a number is
only meaningful relative to the right comparison set.

Peers = the banks the firm tracks (watchlist metrics) that fall in the same
asset-size tier. Rendered as a native HTML strip (no iframe).
"""
from __future__ import annotations
import html as _html

import streamlit as st

from analysis.peer_groups import metric_percentile_context, CONTEXT_METRIC_KEYS

# Clean display labels (the config labels carry parenthetical noise).
_LABELS = {
    "roaa": "ROAA", "roatce_normalized": "ROATCE", "nim": "NIM",
    "efficiency_ratio": "Efficiency", "npl_ratio": "NPL ratio",
    "nco_ratio": "NCO ratio", "cet1_ratio": "CET1",
}


def _ordinal(n: int) -> str:
    n = int(round(n))
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _band_word(p: float) -> tuple[str, str]:
    """Average percentile → plain-English read + colour."""
    if p >= 75:
        return "top-quartile", "#059669"
    if p >= 50:
        return "above peers", "#0891b2"
    if p >= 25:
        return "below peers", "#d97706"
    return "bottom-quartile", "#dc2626"


# Headline metrics grouped into the three reads an analyst forms first.
_GROUPS = [
    ("Profitability", ["roaa", "roatce_normalized", "nim", "efficiency_ratio"]),
    ("Credit", ["npl_ratio", "nco_ratio"]),
    ("Capital", ["cet1_ratio"]),
]


def _at_a_glance(ctx: dict) -> str:
    """One-line synthesis of the percentile reads by category."""
    parts = []
    for name, keys in _GROUPS:
        ps = [ctx[k]["percentile"] for k in keys if k in ctx]
        if not ps:
            continue
        word, color = _band_word(sum(ps) / len(ps))
        parts.append(f'{name}: <strong style="color:{color};">{word}</strong>')
    if not parts:
        return ""
    return " &nbsp;·&nbsp; ".join(parts)


def _pctile_color(p: float) -> str:
    """Goodness percentile → colour (higher already means better)."""
    if p >= 75:
        return "#059669"   # top quartile — emerald
    if p >= 50:
        return "#0891b2"   # above median — cyan
    if p >= 25:
        return "#d97706"   # below median — amber
    return "#dc2626"       # bottom quartile — red


def _all_metrics(self_metrics: dict | None, ticker: str) -> list[dict]:
    """Watchlist metrics from cache, ensuring the subject bank is included."""
    try:
        from data.cache import get as cache_get
        metrics = cache_get("watchlist_metrics_last") or []
    except Exception:
        metrics = []
    metrics = list(metrics)
    if self_metrics and not any(m.get("ticker") == ticker for m in metrics):
        metrics.append(self_metrics)
    return metrics


def render_peer_context(ticker: str, self_metrics: dict | None = None) -> bool:
    """Render the peer-percentile strip for a bank. Returns True if anything was
    shown (so callers can decide whether to draw a divider)."""
    all_metrics = _all_metrics(self_metrics, ticker)
    if not all_metrics:
        return False

    ctx = metric_percentile_context(ticker, all_metrics)
    meta = ctx.pop("_meta", {})
    tier = meta.get("tier")
    n = meta.get("cohort_size", 0)
    items = [(k, ctx[k]) for k in CONTEXT_METRIC_KEYS if k in ctx]
    # Need a real cohort (the subject + a few peers) for percentiles to mean
    # anything. A tier with only the subject bank isn't a comparison.
    if not items or n < 6:
        return False

    cells = []
    for k, d in items:
        label = _LABELS.get(k, d.get("label", k))
        val = d["value"]
        p = d["percentile"]
        med = d["median"]
        color = _pctile_color(p)
        cells.append(
            f'<div style="flex:1 1 0;min-width:96px;padding:6px 10px;'
            f'border-left:3px solid {color};background:rgba(148,163,184,0.05);'
            f'border-radius:0 8px 8px 0;">'
            f'<div style="font-size:0.6rem;text-transform:uppercase;letter-spacing:.02em;'
            f'color:var(--text-secondary,#475569);font-weight:600;">{_html.escape(label)}</div>'
            f'<div style="font-size:1.0rem;font-weight:700;color:var(--text-primary,#0f172a);'
            f'line-height:1.25;">{val:.2f}%</div>'
            f'<div style="font-size:0.66rem;color:{color};font-weight:700;">'
            f'{_ordinal(p)} pctile</div>'
            f'<div style="font-size:0.62rem;color:#94a3b8;">med {med:.2f}%</div>'
            f'</div>'
        )

    tier_txt = _html.escape(str(tier)) if tier else "peer"
    glance = _at_a_glance(ctx)
    glance_html = (
        f'<div style="font-size:0.84rem;color:#0f172a;margin-bottom:5px;">'
        f'<span style="color:#64748b;">At a glance vs peers — </span>{glance}</div>'
    ) if glance else ""
    strip = (
        f'<div style="margin:2px 0 6px;">'
        + glance_html
        + f'<div style="font-size:0.7rem;color:var(--text-secondary,#475569);margin-bottom:4px;">'
        f'Percentile vs <strong>{n}</strong> {tier_txt} peers tracked '
        f'(higher = better; ranks invert for efficiency / NPL / NCO)</div>'
        f'<div style="display:flex;gap:6px;flex-wrap:wrap;">' + "".join(cells) + '</div>'
        f'</div>'
    )
    st.markdown(strip, unsafe_allow_html=True)
    return True
