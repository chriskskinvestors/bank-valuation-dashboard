"""
Tests for data/national_rates.py — FDIC National Rates and Rate Caps behind
the Market & Macro "Funding & Deposits" section (docs/HOME-MACRO-PLAN.md).
All HTTP is mocked; the workbook fixture is built in-memory with openpyxl
mirroring the real archive-revised-rule.xlsx layout (title row, date row
with 5 columns per month, header row, 'All Deposits' section label,
products down column 0, empty placeholder blocks for future months). Pins:

  • happy-path parsing: latest month wins, rate_pct/cap_pct per product
  • rounding to 2dp (the real file carries 1.340486-style floats)
  • placeholder future-month blocks (headers, no date) are skipped
  • history: ascending, window-filtered, monthly cadence honored
  • None/[] (never raise) on HTTP failure, 429 exhaustion, non-xlsx bytes,
    unrecognizable layout
  • a fresh cache entry short-circuits the network entirely
"""
import sys
import types
import unittest
from datetime import datetime, timedelta
from io import BytesIO
from unittest.mock import MagicMock, patch

# Stub streamlit before importing data modules (house pattern).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

HEADERS = ["National Rate", "National Rate plus 75 bps", "Treasury Yield",
           "Treasury Yield - Rate Cap Adjusted", "National Rate Cap"]
PRODUCTS = ["Savings", "Interest Checking", "Money Market <100M",
            "1 month CD <100M", "3 month CD <100M", "6 month CD <100M",
            "12 month CD <100M", "24 month CD <100M", "36 month CD <100M",
            "48 month CD <100M", "60 month CD <100M"]

# (rate, cap) per product per month — hand-picked off the real May/April
# 2026 archive. 60-month CD pins the rounding behavior (1.340486 → 1.34).
MAY_VALUES = [(0.38, 4.39), (0.07, 4.39), (0.57, 4.39), (0.21, 5.21),
              (1.24, 5.17), (1.35, 5.20), (1.55, 5.21), (1.50, 5.41),
              (1.32, 5.44), (1.24, 5.44), (1.340486, 5.57)]
APR_VALUES = [(0.40, 4.45), (0.09, 4.45), (0.59, 4.45), (0.21, 5.24),
              (1.25, 5.19), (1.44, 5.21), (1.53, 5.17), (1.51, 5.30),
              (1.33, 5.32), (1.25, 5.32), (1.35, 5.45)]


def _workbook_bytes(months, placeholder_blocks=1):
    """Build an archive-shaped xlsx: ``months`` is [(datetime, values)]
    newest first; ``placeholder_blocks`` empty future-month blocks (headers
    but no date) sit to their left, exactly like the real file."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "2026 Archive"
    n_blocks = placeholder_blocks + len(months)
    ws.append(["2026 National Rates"])          # row 1: title
    ws.append([])                                # row 2: blank
    date_row, header_row = [None], [None]        # col A empty
    for b in range(n_blocks):
        d = None if b < placeholder_blocks else months[b - placeholder_blocks][0]
        date_row += [d] * 5
        header_row += HEADERS
    ws.append(date_row)                          # row 3: dates
    ws.append(header_row)                        # row 4: headers
    ws.append(["All Deposits"])                  # row 5: section label
    for i, name in enumerate(PRODUCTS):
        row = [name]
        for b in range(placeholder_blocks):
            row += [None] * 5
        for _, values in months:
            rate, cap = values[i]
            row += [rate, rate + 0.75, 3.7, cap, cap]
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _resp(content):
    r = MagicMock()
    r.content = content
    return r


MAY18 = datetime(2026, 5, 18)
APR20 = datetime(2026, 4, 20)


class TestGetNationalRates(unittest.TestCase):

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_happy_path_parses_and_caches(self, mock_get, _cg, mock_cput):
        from data import national_rates
        mock_get.return_value = _resp(
            _workbook_bytes([(MAY18, MAY_VALUES), (APR20, APR_VALUES)]))

        out = national_rates.get_national_rates()
        self.assertIsNotNone(out)
        self.assertEqual(out["asof"], "2026-05-18")     # latest month wins
        self.assertEqual(out["savings"], {"rate_pct": 0.38, "cap_pct": 4.39})
        self.assertEqual(out["interest_checking"],
                         {"rate_pct": 0.07, "cap_pct": 4.39})
        self.assertEqual(out["mmda"], {"rate_pct": 0.57, "cap_pct": 4.39})
        self.assertEqual(out["cd_12mo"], {"rate_pct": 1.55, "cap_pct": 5.21})
        self.assertEqual(out["cd_60mo"]["rate_pct"], 1.34)  # 1.340486 rounded
        self.assertEqual(out["cd_60mo"]["cap_pct"], 5.57)
        # All ten tenors + three non-maturity products present.
        for f in ["savings", "interest_checking", "mmda", "cd_1mo", "cd_3mo",
                  "cd_6mo", "cd_12mo", "cd_24mo", "cd_36mo", "cd_48mo",
                  "cd_60mo"]:
            self.assertIn(f, out)
        # Cached under the documented key with a cached_at stamp.
        key, payload = mock_cput.call_args[0]
        self.assertEqual(key, "national_rates:revised_rule")
        self.assertIn("cached_at", payload)
        self.assertEqual(len(payload["records"]), 2)  # placeholders skipped

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_http_failure_returns_none(self, mock_get, _cg, mock_cput):
        from data import national_rates
        mock_get.side_effect = Exception("connection reset")
        self.assertIsNone(national_rates.get_national_rates())
        mock_cput.assert_not_called()

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry", return_value=None)
    def test_retries_exhausted_returns_none(self, _mg, _cg, mock_cput):
        from data import national_rates
        self.assertIsNone(national_rates.get_national_rates())
        mock_cput.assert_not_called()

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_non_xlsx_bytes_returns_none(self, mock_get, _cg, mock_cput):
        # e.g. a CDN error page served with HTTP 200.
        from data import national_rates
        mock_get.return_value = _resp(b"<html>Access Denied</html>")
        self.assertIsNone(national_rates.get_national_rates())
        mock_cput.assert_not_called()

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_unrecognizable_layout_returns_none(self, mock_get, _cg, mock_cput):
        # A valid workbook with no 'National Rate' header anywhere.
        from openpyxl import Workbook
        from data import national_rates
        wb = Workbook()
        wb.active.append(["totally", "different", "report"])
        buf = BytesIO(); wb.save(buf)
        mock_get.return_value = _resp(buf.getvalue())
        self.assertIsNone(national_rates.get_national_rates())
        mock_cput.assert_not_called()

    @patch("data.http.get_with_retry")
    @patch("data.cache.get")
    def test_cache_hit_short_circuits_network(self, mock_cget, mock_get):
        from data import national_rates
        rec = {"asof": "2026-05-18",
               "savings": {"rate_pct": 0.38, "cap_pct": 4.39}}
        mock_cget.return_value = {"cached_at": datetime.now().isoformat(),
                                  "records": [rec]}
        out = national_rates.get_national_rates()
        self.assertEqual(out, rec)
        self.assertEqual(mock_get.call_count, 0)

    @patch("data.cache.put")
    @patch("data.cache.get")
    @patch("data.http.get_with_retry")
    def test_stale_cache_refetches(self, mock_get, mock_cget, _cp):
        from data import national_rates
        stale = (datetime.now() - timedelta(days=2)).isoformat()
        mock_cget.return_value = {"cached_at": stale,
                                  "records": [{"asof": "1999-01-01"}]}
        mock_get.return_value = _resp(_workbook_bytes([(MAY18, MAY_VALUES)]))
        out = national_rates.get_national_rates()
        self.assertEqual(out["asof"], "2026-05-18")
        self.assertEqual(mock_get.call_count, 1)


class TestGetNationalRateHistory(unittest.TestCase):

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_history_ascending_within_window(self, mock_get, _cg, _cp):
        from data import national_rates
        # Dynamic dates so the window math is stable on any run date.
        recent = datetime.now() - timedelta(days=7)
        older = datetime.now() - timedelta(days=60)
        mock_get.return_value = _resp(
            _workbook_bytes([(recent, MAY_VALUES), (older, APR_VALUES)]))

        hist = national_rates.get_national_rate_history(weeks=104)
        self.assertEqual(len(hist), 2)  # monthly cadence: 2 obs, not 9 weeks
        self.assertEqual(hist[0]["asof"], older.date().isoformat())   # ascending
        self.assertEqual(hist[1]["asof"], recent.date().isoformat())
        self.assertEqual(hist[0]["cd_12mo"], {"rate_pct": 1.53, "cap_pct": 5.17})

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_window_filter_drops_old_months(self, mock_get, _cg, _cp):
        from data import national_rates
        recent = datetime.now() - timedelta(days=7)
        older = datetime.now() - timedelta(days=60)
        mock_get.return_value = _resp(
            _workbook_bytes([(recent, MAY_VALUES), (older, APR_VALUES)]))

        hist = national_rates.get_national_rate_history(weeks=4)  # 28d cutoff
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["asof"], recent.date().isoformat())

    @patch("data.cache.put")
    @patch("data.cache.get", return_value=None)
    @patch("data.http.get_with_retry")
    def test_failure_returns_empty_list(self, mock_get, _cg, _cp):
        from data import national_rates
        mock_get.side_effect = Exception("timeout")
        self.assertEqual(national_rates.get_national_rate_history(), [])


if __name__ == "__main__":
    unittest.main()
