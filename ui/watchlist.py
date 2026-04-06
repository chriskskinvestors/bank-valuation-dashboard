"""
Watchlist and Portfolio management — persisted to JSON files.
"""

import json
import re
from pathlib import Path

from config import DEFAULT_WATCHLIST, DEFAULT_PORTFOLIO

_ROOT = Path(__file__).parent.parent
WATCHLIST_FILE = _ROOT / "watchlist.json"
PORTFOLIO_FILE = _ROOT / "portfolio.json"


# ── Generic list helpers ─────────────────────────────────────────────────

def _load_list(path: Path, defaults: list[str]) -> list[str]:
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if isinstance(data, list) and data:
                return [t.upper().strip() for t in data if t.strip()]
        except Exception:
            pass
    return list(defaults)


def _save_list(path: Path, tickers: list[str]):
    tickers = [t.upper().strip() for t in tickers if t.strip()]
    path.write_text(json.dumps(tickers, indent=2))


def _add(path: Path, defaults: list[str], ticker: str) -> list[str]:
    lst = _load_list(path, defaults)
    ticker = ticker.upper().strip()
    if ticker and ticker not in lst:
        lst.append(ticker)
        _save_list(path, lst)
    return lst


def _remove(path: Path, defaults: list[str], ticker: str) -> list[str]:
    lst = _load_list(path, defaults)
    ticker = ticker.upper().strip()
    lst = [t for t in lst if t != ticker]
    _save_list(path, lst)
    return lst


def _bulk_import(path: Path, text: str) -> list[str]:
    tickers = re.split(r'[,\s\n]+', text.upper())
    tickers = [t.strip() for t in tickers if t.strip()]
    if tickers:
        _save_list(path, tickers)
    return tickers


# ── Watchlist ────────────────────────────────────────────────────────────

def load_watchlist() -> list[str]:
    return _load_list(WATCHLIST_FILE, DEFAULT_WATCHLIST)

def save_watchlist(tickers: list[str]):
    _save_list(WATCHLIST_FILE, tickers)

def add_ticker(ticker: str) -> list[str]:
    return _add(WATCHLIST_FILE, DEFAULT_WATCHLIST, ticker)

def remove_ticker(ticker: str) -> list[str]:
    return _remove(WATCHLIST_FILE, DEFAULT_WATCHLIST, ticker)

def bulk_import(text: str) -> list[str]:
    return _bulk_import(WATCHLIST_FILE, text)


# ── Portfolio ────────────────────────────────────────────────────────────

def load_portfolio() -> list[str]:
    return _load_list(PORTFOLIO_FILE, DEFAULT_PORTFOLIO)

def save_portfolio(tickers: list[str]):
    _save_list(PORTFOLIO_FILE, tickers)

def add_to_portfolio(ticker: str) -> list[str]:
    return _add(PORTFOLIO_FILE, DEFAULT_PORTFOLIO, ticker)

def remove_from_portfolio(ticker: str) -> list[str]:
    return _remove(PORTFOLIO_FILE, DEFAULT_PORTFOLIO, ticker)

def bulk_import_portfolio(text: str) -> list[str]:
    return _bulk_import(PORTFOLIO_FILE, text)


# ── Sidebar UI ───────────────────────────────────────────────────────────

def _render_list_section(st, label, load_fn, add_fn, remove_fn, bulk_fn, prefix):
    """Render a list management section in the sidebar."""
    lst = load_fn()

    with st.sidebar.expander(f"**{label}** ({len(lst)})", expanded=(prefix == "wl")):
        # Add ticker form
        with st.form(f"{prefix}_add_form", clear_on_submit=True):
            col1, col2 = st.columns([3, 1])
            new_ticker = col1.text_input(
                "Add ticker",
                label_visibility="collapsed",
                placeholder="Add ticker...",
                key=f"{prefix}_add_input",
            )
            submitted = col2.form_submit_button("+")
            if submitted and new_ticker:
                lst = add_fn(new_ticker)
                st.rerun()

        # Bulk import
        with st.expander("Bulk import/export"):
            bulk_text = st.text_area(
                "Paste tickers (comma separated)",
                value=", ".join(lst),
                key=f"{prefix}_bulk_area",
                height=60,
            )
            if st.button("Import", key=f"{prefix}_bulk_btn"):
                lst = bulk_fn(bulk_text)
                st.rerun()

        # Bank list with remove buttons
        remove_target = None
        for ticker in lst:
            col1, col2 = st.columns([4, 1])
            col1.markdown(f"`{ticker}`")
            if col2.button("x", key=f"{prefix}_rm_{ticker}"):
                remove_target = ticker

        if remove_target:
            lst = remove_fn(remove_target)
            st.rerun()

    return lst


def render_watchlist_sidebar(st):
    """Render watchlist + portfolio management in the sidebar. Returns (watchlist, portfolio)."""
    st.sidebar.markdown("---")

    watchlist = _render_list_section(
        st, "Watchlist", load_watchlist, add_ticker, remove_ticker, bulk_import, "wl"
    )

    portfolio = _render_list_section(
        st, "Portfolio", load_portfolio, add_to_portfolio, remove_from_portfolio,
        bulk_import_portfolio, "pf"
    )

    return watchlist, portfolio
