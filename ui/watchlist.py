"""
Watchlist management — persisted to watchlist.json.
"""

import json
from pathlib import Path

from config import DEFAULT_WATCHLIST

WATCHLIST_FILE = Path(__file__).parent.parent / "watchlist.json"


def load_watchlist() -> list[str]:
    """Load the watchlist from disk, or return defaults."""
    if WATCHLIST_FILE.exists():
        try:
            data = json.loads(WATCHLIST_FILE.read_text())
            if isinstance(data, list) and data:
                return [t.upper().strip() for t in data if t.strip()]
        except Exception:
            pass
    return list(DEFAULT_WATCHLIST)


def save_watchlist(tickers: list[str]):
    """Persist the watchlist to disk."""
    tickers = [t.upper().strip() for t in tickers if t.strip()]
    WATCHLIST_FILE.write_text(json.dumps(tickers, indent=2))


def add_ticker(ticker: str) -> list[str]:
    """Add a ticker and save. Returns updated list."""
    wl = load_watchlist()
    ticker = ticker.upper().strip()
    if ticker and ticker not in wl:
        wl.append(ticker)
        save_watchlist(wl)
    return wl


def remove_ticker(ticker: str) -> list[str]:
    """Remove a ticker and save. Returns updated list."""
    wl = load_watchlist()
    ticker = ticker.upper().strip()
    wl = [t for t in wl if t != ticker]
    save_watchlist(wl)
    return wl


def bulk_import(text: str) -> list[str]:
    """Import tickers from comma/space/newline separated text. Returns updated list."""
    import re
    tickers = re.split(r'[,\s\n]+', text.upper())
    tickers = [t.strip() for t in tickers if t.strip()]
    if tickers:
        save_watchlist(tickers)
    return tickers


def render_watchlist_sidebar(st):
    """Render watchlist management in the Streamlit sidebar. Returns current watchlist."""
    st.sidebar.markdown("---")
    st.sidebar.subheader("Watchlist")

    watchlist = load_watchlist()

    # ── Add ticker ────────────────────────────────────────────────────────
    # Use a form so the text input value is preserved when the button is clicked
    with st.sidebar.form("add_ticker_form", clear_on_submit=True):
        col1, col2 = st.columns([3, 1])
        new_ticker = col1.text_input(
            "Add ticker",
            label_visibility="collapsed",
            placeholder="Add ticker...",
            key="add_ticker_input",
        )
        submitted = col2.form_submit_button("+")
        if submitted and new_ticker:
            watchlist = add_ticker(new_ticker)
            st.rerun()

    # ── Bulk import ───────────────────────────────────────────────────────
    with st.sidebar.expander("Bulk import/export"):
        bulk_text = st.text_area(
            "Paste tickers (comma separated)",
            value=", ".join(watchlist),
            key="bulk_import_area",
            height=80,
        )
        if st.button("Import", key="bulk_import_btn"):
            watchlist = bulk_import(bulk_text)
            st.rerun()

    # ── Current watchlist with remove buttons ─────────────────────────────
    st.sidebar.markdown(f"**{len(watchlist)} banks**")
    remove_target = None
    for ticker in watchlist:
        col1, col2 = st.sidebar.columns([4, 1])
        col1.markdown(f"`{ticker}`")
        if col2.button("x", key=f"rm_{ticker}"):
            remove_target = ticker

    if remove_target:
        watchlist = remove_ticker(remove_target)
        st.rerun()

    return watchlist
