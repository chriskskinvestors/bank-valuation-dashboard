"""
Per-bank smoke test: load every Company Analysis view for every bank
and report crashes.

Approach:
  1. Install a fake `streamlit` module before any ui.* import.
  2. Fake Streamlit is a MagicMock with sane defaults:
       - button/checkbox/toggle → False (no side effects)
       - radio/selectbox → first option
       - columns/tabs/expander/form/container/spinner → no-op context managers
       - cache_data/cache_resource → pass-through decorators
       - session_state → real dict
  3. For each ticker × view, call the render function inside try/except and
     record crashes. Streamlit widget side-effects are suppressed but any
     *data-layer* exception (None math, KeyError, bad API response) surfaces.

Output: tests/smoke_report.csv — one row per (ticker, view, status).
Run:   python tests/smoke_views.py
"""

from __future__ import annotations
import sys
import csv
import time
import traceback
from pathlib import Path
from unittest.mock import MagicMock
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ──────────────────────────────────────────────────────────────────────────
# Fake Streamlit
# ──────────────────────────────────────────────────────────────────────────

def _make_fake_streamlit():
    fake = MagicMock()

    # Widgets that return bool/int/None — force safe defaults
    fake.button.return_value = False
    fake.download_button.return_value = False
    fake.checkbox.return_value = False
    fake.toggle.return_value = False
    fake.file_uploader.return_value = None
    fake.text_input.return_value = ""
    fake.text_area.return_value = ""
    fake.number_input.return_value = 0
    fake.slider.return_value = 0
    fake.date_input.return_value = None
    fake.time_input.return_value = None
    fake.color_picker.return_value = "#000000"

    # Selection widgets — return the first option
    def _first_opt(*args, **kwargs):
        # signature: label, options=... (or positional)
        opts = kwargs.get("options")
        if opts is None and len(args) >= 2:
            opts = args[1]
        try:
            if opts is None:
                return None
            return list(opts)[0] if opts else None
        except Exception:
            return None
    fake.radio.side_effect = _first_opt
    fake.selectbox.side_effect = _first_opt
    fake.multiselect.return_value = []
    fake.select_slider.side_effect = _first_opt

    # Context managers — return a no-op context
    def _make_ctx():
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        return ctx

    def _ctx(*a, **kw):
        return _make_ctx()

    def _columns(spec, *a, **kw):
        n = spec if isinstance(spec, int) else len(list(spec))
        return [_make_ctx() for _ in range(n)]

    def _tabs(names):
        return [_make_ctx() for _ in names]

    fake.container = _ctx
    fake.expander = _ctx
    fake.columns = _columns
    fake.tabs = _tabs
    fake.empty.return_value = _make_ctx()
    fake.form = _ctx
    fake.spinner = _ctx
    fake.status = _ctx
    fake.sidebar = fake  # sidebar aliases everything

    # Cache decorators — pass through
    def _cache_decorator(*args, **kwargs):
        if len(args) == 1 and callable(args[0]):
            return args[0]

        def _wrap(fn):
            return fn
        return _wrap
    fake.cache_data = _cache_decorator
    fake.cache_resource = _cache_decorator

    # Control flow — don't actually stop
    fake.stop = MagicMock()
    fake.rerun = MagicMock()
    fake.experimental_rerun = MagicMock()

    # Session state — plain dict
    fake.session_state = {}

    # Layout/misc
    fake.set_page_config = MagicMock()
    fake.progress = MagicMock(return_value=_make_ctx())

    return fake


_FAKE_ST = _make_fake_streamlit()
sys.modules["streamlit"] = _FAKE_ST


# ──────────────────────────────────────────────────────────────────────────
# Fake yfinance — returns empty data immediately. yfinance calls in the
# Earnings view are rate-limited and not part of our core data pipeline,
# so short-circuit them here.
# ──────────────────────────────────────────────────────────────────────────
def _install_fake_yfinance():
    import types
    import pandas as pd
    fake_yf = types.ModuleType("yfinance")

    class _FakeTicker:
        def __init__(self, symbol, *a, **kw):
            self.symbol = symbol
            self.info = {}
            self.earnings_dates = pd.DataFrame()
            self.calendar = {}
            self.recommendations = pd.DataFrame()
            self.analyst_price_targets = {}

        def history(self, *a, **kw):
            return pd.DataFrame()

        def get_earnings_dates(self, *a, **kw):
            return pd.DataFrame()

        def get_earnings_trend(self, *a, **kw):
            return pd.DataFrame()

        def __getattr__(self, name):
            # Unknown attribute access → return empty-ish thing
            return pd.DataFrame()

    fake_yf.Ticker = _FakeTicker
    fake_yf.download = lambda *a, **kw: pd.DataFrame()
    sys.modules["yfinance"] = fake_yf


_install_fake_yfinance()


# ──────────────────────────────────────────────────────────────────────────
# View definitions — all 11 Company Analysis sub-tabs
# ──────────────────────────────────────────────────────────────────────────

# Each entry: (view_name, module, function, call_type)
VIEWS = [
    ("Overview",        "ui.bank_detail",       "render_bank_detail",         "overview"),
    ("Financials",      "ui.historicals",       "render_historicals",         "simple"),
    ("Filings",         "ui.filings",           "render_filings_for_ticker",  "simple"),
    ("Deposits",        "ui.deposit_lookup",    "render_deposits_for_ticker", "simple"),
    ("Credit",          "ui.credit_dynamics",   "render_credit_dynamics",     "with_watchlist"),
    ("Capital",         "ui.capital_dynamics",  "render_capital_dynamics",    "with_watchlist"),
    ("NIM Sensitivity", "ui.rate_sensitivity",  "render_rate_sensitivity",    "simple"),
    ("Valuation",       "ui.valuation_model",   "render_valuation_model",     "simple"),
    ("Ownership",       "ui.ownership",         "render_ownership",           "simple"),
    ("Data Quality",    "ui.data_quality",      "render_data_quality",        "simple"),
    ("Earnings",        "ui.earnings",          "render_earnings_consensus",  "earnings"),
]


def _call_render(view_name, module_name, fn_name, call_type, ticker):
    """Invoke one render function for one ticker. Returns (status, error)."""
    import warnings
    warnings.filterwarnings("ignore")

    # Fresh session_state per call (avoids leakage between banks)
    _FAKE_ST.session_state.clear()

    mod = __import__(module_name, fromlist=[fn_name])
    fn = getattr(mod, fn_name)

    if call_type == "overview":
        # Needs a DataFrame with a 'ticker' column
        import pandas as pd
        from analysis.metrics import build_bank_metrics
        from data import fdic_client, sec_client
        from data.bank_mapping import get_cik, get_fdic_cert
        cik = get_cik(ticker)
        cert = get_fdic_cert(ticker)
        fdic_data, fdic_hist = {}, []
        if cert:
            try:
                df = fdic_client.fetch_financials(cert, limit=8)
                if not df.empty:
                    fdic_hist = df.to_dict("records")
                    fdic_data = fdic_hist[0]
            except Exception:
                pass
        sec_data = {}
        if cik:
            try:
                sec_data = sec_client.get_latest_fundamentals(cik) or {}
            except Exception:
                pass
        metrics = build_bank_metrics(ticker, fdic_data, sec_data, {"price": 50}, fdic_hist)
        df = pd.DataFrame([{**metrics, "ticker": ticker}])
        fn(ticker, df)
    elif call_type == "with_watchlist":
        fn(ticker, [ticker])
    elif call_type == "earnings":
        from analysis.metrics import build_bank_metrics
        from data import fdic_client, sec_client
        from data.bank_mapping import get_cik, get_fdic_cert
        cik = get_cik(ticker); cert = get_fdic_cert(ticker)
        fdic_data, fdic_hist = {}, []
        if cert:
            try:
                fdf = fdic_client.fetch_financials(cert, limit=8)
                if not fdf.empty:
                    fdic_hist = fdf.to_dict("records"); fdic_data = fdic_hist[0]
            except Exception:
                pass
        sec_data = {}
        if cik:
            try:
                sec_data = sec_client.get_latest_fundamentals(cik) or {}
            except Exception:
                pass
        metrics = build_bank_metrics(ticker, fdic_data, sec_data, {"price": 50}, fdic_hist)
        fn(ticker, metrics)
    else:
        fn(ticker)


def smoke_ticker(ticker: str) -> list[dict]:
    """Run all views against one ticker. Returns list of dicts."""
    rows = []
    for view_name, mod, fn_name, call_type in VIEWS:
        status = "OK"
        err_type = ""
        err_msg = ""
        tb_tail = ""
        try:
            _call_render(view_name, mod, fn_name, call_type, ticker)
        except Exception as e:
            status = "CRASH"
            err_type = type(e).__name__
            err_msg = str(e)[:250]
            tb_tail = traceback.format_exc()[-800:]
        rows.append({
            "ticker": ticker,
            "view": view_name,
            "status": status,
            "error_type": err_type,
            "error_msg": err_msg,
            "tb_tail": tb_tail,
        })
    return rows


def run(tickers: list[str] | None = None, workers: int = 6):
    import warnings; warnings.filterwarnings("ignore")
    from data.bank_universe import get_universe_tickers
    from config import DEFAULT_WATCHLIST

    if tickers is None:
        universe = set(get_universe_tickers()) | set(DEFAULT_WATCHLIST)
        tickers = sorted(universe)
    total_tickers = len(tickers)
    print(f"Smoke-testing {total_tickers} banks × {len(VIEWS)} views = {total_tickers * len(VIEWS)} cases")
    t0 = time.time()

    all_rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(smoke_ticker, t): t for t in tickers}
        done = 0
        for fut in as_completed(futures):
            all_rows.extend(fut.result())
            done += 1
            if done % 25 == 0 or done == total_tickers:
                print(f"  {done}/{total_tickers} banks done ({time.time() - t0:.0f}s elapsed)")

    # Categorize
    total_cases = len(all_rows)
    crashes = [r for r in all_rows if r["status"] == "CRASH"]
    print()
    print("=" * 72)
    print(f"SMOKE TEST RESULTS")
    print("=" * 72)
    print(f"Total cases:  {total_cases}")
    print(f"Passed:       {total_cases - len(crashes)} ({(total_cases - len(crashes))/total_cases*100:.1f}%)")
    print(f"Crashed:      {len(crashes)} ({len(crashes)/total_cases*100:.1f}%)")

    # By view
    by_view_crash: dict[str, int] = {}
    for r in crashes:
        by_view_crash[r["view"]] = by_view_crash.get(r["view"], 0) + 1
    if by_view_crash:
        print("\nCrashes by view:")
        for v, c in sorted(by_view_crash.items(), key=lambda x: -x[1]):
            print(f"  {v:<20} {c:>4}")

    # By error type
    by_err: dict[str, int] = {}
    for r in crashes:
        by_err[r["error_type"]] = by_err.get(r["error_type"], 0) + 1
    if by_err:
        print("\nCrashes by error type:")
        for e, c in sorted(by_err.items(), key=lambda x: -x[1]):
            print(f"  {e:<30} {c:>4}")

    # Tickers with most crashes
    by_ticker_crash: dict[str, int] = {}
    for r in crashes:
        by_ticker_crash[r["ticker"]] = by_ticker_crash.get(r["ticker"], 0) + 1
    if by_ticker_crash:
        print(f"\nTickers with >=3 crashes ({sum(1 for c in by_ticker_crash.values() if c >= 3)} total):")
        for t, c in sorted(by_ticker_crash.items(), key=lambda x: (-x[1], x[0]))[:20]:
            print(f"  {t:<6} {c:>2}")

    # Save CSV
    out_path = Path(__file__).parent / "smoke_report.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "view", "status", "error_type", "error_msg", "tb_tail"])
        w.writeheader()
        w.writerows(sorted(all_rows, key=lambda x: (x["status"], x["view"], x["ticker"])))
    print(f"\nReport: {out_path}")

    return all_rows


if __name__ == "__main__":
    # Allow "python tests/smoke_views.py JPM BAC WFC" to smoke-test a subset
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    run(args if args else None)
