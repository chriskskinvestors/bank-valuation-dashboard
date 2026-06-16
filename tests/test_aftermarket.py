"""
Tests for the pre/post-market plumbing (data.fmp_client aftermarket fns).

Pins the no-plausible-wrong rule for the ETF table's "Aft" column:
  • aftermarket_move returns a % move ONLY when the bid/ask spread is tight
    enough to be a real print; wide / one-sided / non-positive / unparseable
    quotes → None (UI shows '—', never a fabricated after-hours move).
  • get_aftermarket_quote parses bidPrice/askPrice; returns the empty shape
    on plan-denial (None from _get) or no key — so the feature degrades to
    EOD cleanly if the prod key isn't upgraded.

HTTP/cache mocked. Run:  python -m unittest tests.test_aftermarket
"""
from __future__ import annotations
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)


class TestAftermarketMove(unittest.TestCase):

    def test_tight_spread_gives_move(self):
        from data.fmp_client import aftermarket_move
        # last 100, mid 100.95, spread 0.1 (~0.1% of mid) → ~+0.95%
        self.assertAlmostEqual(aftermarket_move(100.90, 101.00, 100.0), 0.95, places=2)

    def test_negative_move(self):
        from data.fmp_client import aftermarket_move
        self.assertAlmostEqual(aftermarket_move(98.95, 99.05, 100.0), -1.0, places=2)

    def test_wide_spread_is_none(self):
        """The IWO after-hours case: bid 360.9 / ask 410.98 — far too wide
        to be a real print → None, not a guessed move."""
        from data.fmp_client import aftermarket_move
        self.assertIsNone(aftermarket_move(360.9, 410.98, 386.17))

    def test_one_sided_or_crossed_is_none(self):
        from data.fmp_client import aftermarket_move
        self.assertIsNone(aftermarket_move(0, 101.0, 100.0))      # no bid
        self.assertIsNone(aftermarket_move(101.0, 100.0, 100.0))  # crossed
        self.assertIsNone(aftermarket_move(100.9, 101.0, 0))      # no last

    def test_unparseable_is_none(self):
        from data.fmp_client import aftermarket_move
        self.assertIsNone(aftermarket_move(None, None, None))
        self.assertIsNone(aftermarket_move("x", "y", "z"))


class TestGetAftermarketQuote(unittest.TestCase):

    def setUp(self):
        self.env = patch.dict("os.environ", {"FMP_API_KEY": "test-key"})
        self.env.start()
        self.cget = patch("data.fmp_client._cache_get", return_value=None)
        self.cput = patch("data.fmp_client._cache_put")
        self.cget.start(); self.cput.start()

    def tearDown(self):
        self.env.stop(); self.cget.stop(); self.cput.stop()

    def test_parses_bid_ask(self):
        from data import fmp_client
        payload = [{"symbol": "SPY", "bidPrice": 753.82, "askPrice": 753.95,
                    "volume": 60175623, "timestamp": 1781567999000}]
        with patch("data.fmp_client._get", return_value=payload):
            out = fmp_client.get_aftermarket_quote("spy")
        self.assertEqual(out["bid"], 753.82)
        self.assertEqual(out["ask"], 753.95)
        self.assertEqual(out["volume"], 60175623)

    def test_denial_returns_empty_shape(self):
        """_get returns None on a 401/403 (logged) → empty aftermarket dict."""
        from data import fmp_client
        with patch("data.fmp_client._get", return_value=None):
            out = fmp_client.get_aftermarket_quote("SPY")
        self.assertEqual(out, {"bid": None, "ask": None,
                               "volume": None, "timestamp": None})

    def test_no_key_returns_empty_shape(self):
        from data import fmp_client
        with patch.dict("os.environ", {"FMP_API_KEY": ""}, clear=False):
            out = fmp_client.get_aftermarket_quote("SPY")
        self.assertIsNone(out["bid"])


if __name__ == "__main__":
    unittest.main()
