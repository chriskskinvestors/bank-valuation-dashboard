"""Analyst Coverage sub-tab (Overview section) — SNL plan §12.

Sell-side coverage snapshot from FMP's analyst endpoints (market data, never
displayed fundamentals): price-target consensus + windowed target summary,
recent grade actions, and FMP's composite factor rating (labeled as a generic
model score). Plus the yfinance consensus block the Earnings tab already
sources (recommendation, analyst count, forward EPS), so the coverage picture
sits on one page. Honest empty state — most small banks have no coverage.
"""
from __future__ import annotations

import html as _h

import pandas as pd
import streamlit as st

from data.bank_mapping import get_name
from data.fmp_client import (
    get_quote,
    get_price_target_consensus,
    get_price_target_summary,
    get_analyst_grades,
    get_ratings_snapshot,
)
from ui.chrome import title_bar, ledger, table_export

_UP = '<span style="color:#059669;font-weight:600;">{}</span>'
_DOWN = '<span style="color:#dc2626;font-weight:600;">{}</span>'


def _fmt_px(v) -> str:
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "n/a"


def _upside_pct(target, price):
    """Implied move to target vs current price; None unless both are
    positive numbers (a zero/negative price is bad data, not 0% upside)."""
    if not isinstance(target, (int, float)) or not isinstance(price, (int, float)):
        return None
    if price <= 0 or target <= 0:
        return None
    return (target / price - 1.0) * 100.0


def _fmt_upside(pct) -> str:
    if pct is None:
        return "n/a"
    tmpl = _UP if pct >= 0 else _DOWN
    return tmpl.format(f"{pct:+.1f}%")


def _grade_action_html(action) -> str:
    a = (action or "").strip()
    low = a.lower()
    if "upgrade" in low:
        return _UP.format(_h.escape(a))
    if "downgrade" in low:
        return _DOWN.format(_h.escape(a))
    return _h.escape(a) if a else "—"


def render_analyst_coverage(ticker: str):
    title_bar(f"{get_name(ticker) or ticker} ({ticker})", "Analyst Coverage")

    consensus = get_price_target_consensus(ticker)
    summary = get_price_target_summary(ticker)
    grades = get_analyst_grades(ticker)
    rating = get_ratings_snapshot(ticker)

    if not consensus and not summary and not grades and not rating:
        st.info("No sell-side coverage data is available for this company "
                "from the analyst-data provider (most smaller banks have no "
                "published price targets or grade actions).")
        return

    price = (get_quote(ticker) or {}).get("price")

    # ── Price targets (ledger) + street consensus (ledger) side by side ──
    left, right = st.columns(2)
    with left:
        c = consensus or {}
        up = _upside_pct(c.get("consensus"), price)
        ledger("Price Targets", [
            ("Current price", _fmt_px(price)),
            ("Consensus target", _fmt_px(c.get("consensus"))),
            ("Implied upside", _fmt_upside(up)),
            ("Median target", _fmt_px(c.get("median"))),
            ("High / Low", f"{_fmt_px(c.get('high'))} / {_fmt_px(c.get('low'))}"),
        ])
        st.caption("Analyst price targets via FMP — market data, not a house view.")
    with right:
        est = _yf_consensus(ticker)
        rec = est.get("recommendation")  # yfinance recommendationKey: "buy", "strong_buy", "none"
        rec = rec.replace("_", " ").title() if rec and rec != "none" else "n/a"
        ledger("Street Consensus", [
            ("Recommendation", _h.escape(rec)),
            ("Covering analysts", str(est.get("analyst_count") or "n/a")),
            ("Forward EPS (annual)", _fmt_px(est.get("eps_fwd_annual"))),
            ("Next earnings", _h.escape(str(est.get("next_earnings_date") or "n/a"))),
        ])
        st.caption("Consensus estimates via yfinance — same source as the Earnings tab.")

    # ── Target summary by window ─────────────────────────────────────────
    if summary:
        windows = [
            ("Last month", summary.get("last_month_count"), summary.get("last_month_avg")),
            ("Last quarter", summary.get("last_quarter_count"), summary.get("last_quarter_avg")),
            ("Last year", summary.get("last_year_count"), summary.get("last_year_avg")),
            ("All time", summary.get("all_time_count"), summary.get("all_time_avg")),
        ]
        rows = ""
        for label, n, avg in windows:
            up = _upside_pct(avg, price)
            rows += ("<tr>"
                     f'<td style="text-align:left;">{label}</td>'
                     f'<td style="text-align:right;">{n if n is not None else "n/a"}</td>'
                     f'<td style="text-align:right;">{_fmt_px(avg)}</td>'
                     f'<td style="text-align:right;">{_fmt_upside(up)}</td>'
                     "</tr>")
        st.markdown("#### Price Targets by Window")
        st.markdown(
            '<div class="ksk-grid"><table><thead><tr>'
            '<th style="text-align:left;">Window</th>'
            '<th style="text-align:right;"># Targets</th>'
            '<th style="text-align:right;">Avg Target</th>'
            '<th style="text-align:right;">vs Price</th>'
            f"</tr></thead><tbody>{rows}</tbody></table></div>",
            unsafe_allow_html=True)
        pubs = summary.get("publishers") or []
        if pubs:
            st.caption("Target publishers: " + ", ".join(_h.escape(p) for p in pubs[:8]))

    # ── Composite factor rating (generic model — labeled) ────────────────
    if rating and rating.get("rating"):
        score = rating.get("overall_score")
        parts = [(k, rating.get(f"{k2}_score")) for k, k2 in
                 [("DCF", "dcf"), ("ROE", "roe"), ("ROA", "roa"),
                  ("D/E", "debt_to_equity"), ("P/E", "pe"), ("P/B", "pb")]]
        detail = " · ".join(f"{k} {v}" for k, v in parts if v is not None)
        st.markdown("#### Composite Rating")
        st.markdown(
            f'**{_h.escape(str(rating["rating"]))}**'
            + (f" (overall {score}/5)" if score is not None else "")
            + (f" — {detail}" if detail else ""))
        st.caption("FMP's generic factor model (scores 1–5). The DCF and "
                   "debt-to-equity factors are not bank-tailored — context "
                   "only, not comparable to the house valuation model.")

    # ── Grade actions ────────────────────────────────────────────────────
    if grades:
        st.markdown("#### Recent Grade Actions")
        rows = ""
        for g in grades:
            frm, to = g.get("from_grade"), g.get("to_grade")
            change = (f"{_h.escape(frm)} &rarr; {_h.escape(to)}" if frm and to
                      else _h.escape(to or frm or "—"))
            rows += ("<tr>"
                     f'<td style="text-align:left;">{_h.escape(g.get("date") or "—")}</td>'
                     f'<td style="text-align:left;">{_h.escape(g.get("firm") or "—")}</td>'
                     f'<td style="text-align:left;">{_grade_action_html(g.get("action"))}</td>'
                     f'<td style="text-align:left;">{change}</td>'
                     "</tr>")
        st.markdown(
            '<div class="ksk-grid"><table><thead><tr>'
            '<th style="text-align:left;">Date</th>'
            '<th style="text-align:left;">Firm</th>'
            '<th style="text-align:left;">Action</th>'
            '<th style="text-align:left;">Grade</th>'
            f"</tr></thead><tbody>{rows}</tbody></table></div>",
            unsafe_allow_html=True)
        df = pd.DataFrame([{"Date": g.get("date"), "Firm": g.get("firm"),
                            "Action": g.get("action"),
                            "From": g.get("from_grade"), "To": g.get("to_grade")}
                           for g in grades])
        table_export(df, f"{ticker}_grade_actions", key=f"exp_grades_{ticker}")


def _yf_consensus(ticker: str) -> dict:
    """Cached yfinance consensus block; {} when throttled/uncovered (the
    Earnings tab owns the full view — this is a compact summary)."""
    try:
        from data.estimates import fetch_estimates_cached
        est = fetch_estimates_cached(ticker) or {}
        return est if not est.get("error") else {}
    except Exception:
        return {}
