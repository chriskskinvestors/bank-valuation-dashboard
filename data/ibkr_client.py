"""
Interactive Brokers market data client using ib_insync.

Provides real-time price streaming and historical data for bank stocks.
Requires TWS or IB Gateway running and API enabled.
"""

import threading
import time
from datetime import datetime
from typing import Callable

import asyncio
import pandas as pd

# ib_insync requires an event loop at import time; create one if missing
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

try:
    from ib_insync import IB, Stock, util
    HAS_IBKR = True
except (ImportError, Exception):
    HAS_IBKR = False

from config import IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID


class IBKRClient:
    """Manages IBKR connection, streaming prices, and historical data."""

    def __init__(self):
        self.ib = IB() if HAS_IBKR else None
        self.connected = False
        self.contracts = {}       # ticker -> Contract
        self.tickers = {}         # ticker -> Ticker (live data)
        self.prices = {}          # ticker -> {price, bid, ask, volume, ...}
        self._callbacks = []
        self._thread = None
        self._running = False

    def connect(self) -> bool:
        """Connect to TWS/Gateway. Returns True on success."""
        if not HAS_IBKR:
            print("[IBKR] ib_insync not installed")
            return False
        try:
            self.ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID, timeout=10)
            self.connected = True
            print(f"[IBKR] Connected to {IBKR_HOST}:{IBKR_PORT}")
            return True
        except Exception as e:
            print(f"[IBKR] Connection failed: {e}")
            self.connected = False
            return False

    def disconnect(self):
        if self.ib and self.connected:
            self.ib.disconnect()
            self.connected = False

    def subscribe(self, tickers: list[str]):
        """Subscribe to real-time market data for a list of tickers."""
        if not self.connected:
            return

        for ticker in tickers:
            if ticker in self.contracts:
                continue
            contract = Stock(ticker, "SMART", "USD")
            self.ib.qualifyContracts(contract)
            self.contracts[ticker] = contract

            # Request streaming market data
            tk = self.ib.reqMktData(contract, "", False, False)
            self.tickers[ticker] = tk

        # Set up pending tickers event
        self.ib.pendingTickersEvent += self._on_pending_tickers

    def unsubscribe(self, ticker: str):
        """Cancel market data for a ticker."""
        if ticker in self.contracts and self.connected:
            self.ib.cancelMktData(self.contracts[ticker])
            del self.contracts[ticker]
            del self.tickers[ticker]
            self.prices.pop(ticker, None)

    def _on_pending_tickers(self, tickers_set):
        """Called when new tick data arrives."""
        for tk in tickers_set:
            # Find which ticker symbol this is
            for symbol, contract in self.contracts.items():
                if tk.contract == contract:
                    self.prices[symbol] = {
                        "price": tk.last if tk.last == tk.last else tk.close,
                        "bid": tk.bid if tk.bid == tk.bid else None,
                        "ask": tk.ask if tk.ask == tk.ask else None,
                        "volume": int(tk.volume) if tk.volume == tk.volume else 0,
                        "high": tk.high if tk.high == tk.high else None,
                        "low": tk.low if tk.low == tk.low else None,
                        "close": tk.close if tk.close == tk.close else None,
                        "open": tk.open if tk.open == tk.open else None,
                        "timestamp": datetime.now(),
                    }
                    break

        # Notify callbacks
        for cb in self._callbacks:
            try:
                cb(self.prices)
            except Exception:
                pass

    def on_price_update(self, callback: Callable):
        """Register a callback for price updates: callback(prices_dict)."""
        self._callbacks.append(callback)

    def get_price(self, ticker: str) -> dict:
        """Get current price data for a ticker."""
        return self.prices.get(ticker, {})

    def get_all_prices(self) -> dict:
        """Get all current prices."""
        return dict(self.prices)

    def get_historical_data(
        self, ticker: str, duration: str = "1 Y", bar_size: str = "1 day"
    ) -> pd.DataFrame:
        """
        Fetch historical bars from IBKR.

        duration: "1 D", "1 W", "1 M", "3 M", "1 Y", "5 Y"
        bar_size: "1 min", "5 mins", "1 hour", "1 day", "1 week"
        """
        if not self.connected:
            return pd.DataFrame()

        contract = self.contracts.get(ticker)
        if not contract:
            contract = Stock(ticker, "SMART", "USD")
            self.ib.qualifyContracts(contract)

        try:
            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )
            if not bars:
                return pd.DataFrame()
            df = util.df(bars)
            df["date"] = pd.to_datetime(df["date"])
            return df
        except Exception as e:
            print(f"[IBKR] Historical data error for {ticker}: {e}")
            return pd.DataFrame()

    def start_event_loop(self):
        """Start the ib_insync event loop in a background thread."""
        if not self.connected:
            return

        def _run():
            self._running = True
            while self._running:
                self.ib.sleep(0.1)

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def stop_event_loop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)


# Singleton instance
_client = None


def get_ibkr_client() -> IBKRClient:
    """Get or create the singleton IBKR client."""
    global _client
    if _client is None:
        _client = IBKRClient()
    return _client


# ── Fallback for when IBKR is not connected ─────────────────────────────
# Returns empty data structures so the app can still display fundamentals


def get_empty_price() -> dict:
    return {
        "price": None, "bid": None, "ask": None, "volume": None,
        "high": None, "low": None, "close": None, "open": None,
        "timestamp": None,
    }
