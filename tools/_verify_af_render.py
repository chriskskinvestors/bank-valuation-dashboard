"""Throwaway: headless render check for the above-the-fold panes — verifies
the HTML logic without the live app's cold-FRED/DB latency. Mocks the data
sources with real-shaped values and asserts the produced markup."""
import sys
import types
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

# Stub streamlit: capture markdown, provide empty query_params.
st = types.ModuleType("streamlit")
_captured = []
st.markdown = lambda html, **k: _captured.append(html)
st.query_params = {}
st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
st.cache_resource = st.cache_data
sys.modules["streamlit"] = st

import ui.home as h  # noqa: E402

import pandas as pd  # noqa: E402


def _fake_history(ticker, period="1Y"):
    n = 80
    dates = pd.date_range("2026-03-01", periods=n, freq="D")
    base = 100 + (abs(hash(ticker)) % 20)
    closes = [base * (1 + 0.0012 * i) for i in range(n)]
    return pd.DataFrame({"date": dates, "close": closes, "volume": [1e6] * n})


SAMPLE_METRICS = [
    {"ticker": "WAL", "price": 88.50, "change_pct": 3.18, "total_assets": 80e9},
    {"ticker": "CMA", "price": 61.20, "change_pct": -3.12, "total_assets": 80e9},
    {"ticker": "PNFP", "price": 96.20, "change_pct": 3.91, "total_assets": 50e9},
]
SAMPLE_FEED = [
    {"tag": "M&A", "cls": "ma", "tk": "CMA",
     "head": "Comerica to be acquired by Fifth Third", "ts": "2026-06-15T13:35:00"},
    {"tag": "SELL", "cls": "tr", "tk": "CFR",
     "head": "VP sells 837 of Cullen/Frost", "ts": "2026-06-15T12:32:00"},
]

with patch.object(h, "_fred_points", return_value=(4.33, 4.35, 4.40)), \
     patch("data.price_cache_store.get_prices",
           return_value={"SPY": {"price": 754.83, "change": 13.08, "change_pct": 1.76},
                         "KRE": {"price": 72.23, "change": -1.18, "change_pct": -1.61},
                         "WAL": {"rel_volume": 3.2}, "CMA": {"rel_volume": 8.4},
                         "PNFP": {"rel_volume": 4.7}}), \
     patch("data.fmp_client.get_aftermarket_quote_batch",
           return_value={"SPY": {"bid": 753.82, "ask": 753.95},
                         "IWO": {"bid": 360.9, "ask": 410.98}}), \
     patch.object(h, "_collect_earnings_alerts",
                  return_value=[{"ticker": "ZION", "date": "2026-06-16",
                                 "days_until": 0, "eps_est": 1.21}]), \
     patch("data.macro_calendar.get_upcoming_prints",
           return_value=[{"date": "2026-06-18", "name": "FOMC rate decision",
                          "kind": "fomc"}]), \
     patch.object(h, "_af_feed_items", return_value=SAMPLE_FEED), \
     patch("data.live_rates.live_yields", return_value={
         "3M": (3.6, 3.5, 3.4), "2Y": (3.8, 3.7, 3.6), "5Y": (4.1, 4.0, 3.9),
         "10Y": (4.4, 4.3, 4.2), "30Y": (4.9, 4.8, 4.7)}), \
     patch("data.fmp_client.get_history", side_effect=_fake_history):
    h._render_above_fold(SAMPLE_METRICS, ["ZION", "WAL", "CMA"])

html = _captured[-1] if _captured else ""
checks = {
    "grid wrapper": 'class="afwrap"' in html and 'class="kg"' in html,
    "ETF pane": "Markets · ETFs" in html,
    "Rates pane": "Rates · Credit" in html,
    "SPY ticker": '>SPY<' in html,
    "SPY price": "754.83" in html,
    "KRE down-colored": 'dn">-1.61' in html or 'dn">−1.61' in html or "-1.61" in html,
    "rate level 4.33": "4.33" in html,
    "checkbox": "cbx" in html,
    "deep-link href": 'href="?' in html,
    "Movers pane": ">Movers<" in html,
    "Movers WAL row": "WAL" in html and "+3.18" in html,
    "Movers size dropdown": "<details" in html and "Size" in html,
    "Gainers/Losers selector": ">Gainers<" in html and ">Losers<" in html,
    "Calendar pane": ">Calendar<" in html,
    "Calendar FOMC": "FOMC" in html,
    "Feed pane": "Bank News Feed" in html,
    "Feed M&A tag": 'ftag ma">M&A' in html or "M&amp;A" in html,
    "bank deep-link": "bank=" in html,
    "Overlay pane": "Overlay · Selected" in html,
    "Overlay chart svg": "<svg" in html and "<polyline" in html,
    "Timeframe pills": ">3M<" in html and ">2Y<" in html,
    "Volume pane": "Unusual Volume" in html,
    "Volume rel-vol row": "8.4×" in html,
    "Volume period pills": ">1D<" in html and ">6M<" in html,
    "no placeholder panes left": "wiring next" not in html,
    "7 panes": html.count('class="pane') >= 7,
}
ok = all(checks.values())
for k, v in checks.items():
    print(f"  {'OK ' if v else 'FAIL'} {k}")
print(f"\nlen(html)={len(html)}  panes={html.count('class=\"pane')}")
# Spot-check the spread gate: SPY tight spread → a real Aft move; IWO wide → —
import data.fmp_client as fc
spy_move = fc.aftermarket_move(753.82, 753.95, 754.83)
iwo_move = fc.aftermarket_move(360.9, 410.98, 386.17)
print(f"aftermarket_move SPY={spy_move}  IWO={iwo_move} (IWO must be None)")
sys.exit(0 if ok and spy_move is not None and iwo_move is None else 1)
