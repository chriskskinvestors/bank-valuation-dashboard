"""
Q4 Inc IR-platform press-release ingestion (data/events/ir_site.py).

Many bank IR sites run on Q4 Inc, which renders releases client-side from a JSON
API — so the legacy HTML scraper found nothing (the PFS CFO-appointment release
was invisible across every wire/EDGAR source, only on its Q4 IR site). The Q4
path hits that API directly. No network: requests are mocked.
"""
import os
import sys
import types
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

import data.events.ir_site as ir  # noqa: E402

_Q4_HTML = (
    '<html><head><script src="https://q4cdn.com/x.js"></script>'
    '<script>window.config={apiKey:"BF185719B0464B3CB809D23926182246"};</script>'
    "</head><body>JS-rendered, no release links here</body></html>"
)

_Q4_JSON = {
    "GetPressReleaseListResult": [
        {"Headline": "Provident Bank Appoints Adriano Duarte EVP and Chief Financial Officer",
         "PressReleaseDate": "06/23/2026 08:00:00",
         "LinkToDetailPage": "/news-events/press-releases/press-release/2026/Provident-Bank-Appoints-Adriano/default.aspx"},
        {"Headline": "Provident Bank Names Annamaria Vitelli EVP, Chief Wealth Officer",
         "PressReleaseDate": "06/04/2026 08:00:00",
         "LinkToDetailPage": "/news-events/press-releases/press-release/2026/Vitelli/default.aspx"},
        {"Headline": "Old news that predates the cutoff",
         "PressReleaseDate": "01/01/2020 08:00:00",
         "LinkToDetailPage": "/news/old/default.aspx"},
    ]
}


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class TestQ4ParseDate(unittest.TestCase):
    def test_q4_datetime_format(self):
        d = ir._parse_q4_date("06/23/2026 08:00:00")
        self.assertEqual((d.year, d.month, d.day), (2026, 6, 23))
        self.assertEqual(d.tzinfo, timezone.utc)

    def test_date_only_and_garbage(self):
        self.assertEqual(ir._parse_q4_date("06/23/2026").day, 23)
        self.assertIsNone(ir._parse_q4_date("not a date"))


class TestQ4PressReleases(unittest.TestCase):
    CUTOFF = datetime.now(timezone.utc) - timedelta(days=30)

    def test_pulls_releases_and_absolutizes_links(self):
        with patch.object(ir, "_fetch", return_value=_Q4_HTML), \
             patch.object(ir.requests, "get", return_value=_FakeResp(_Q4_JSON)):
            out = ir._q4_press_releases("https://investorrelations.provident.bank/", self.CUTOFF)
        self.assertIsNotNone(out)
        headlines = [h for (_u, h, _d) in out]
        self.assertIn("Provident Bank Appoints Adriano Duarte EVP and Chief Financial Officer", headlines)
        # cutoff drops the 2020 item
        self.assertTrue(all("Old news" not in h for h in headlines))
        # relative LinkToDetailPage -> absolute on the IR host
        self.assertTrue(all(u.startswith("https://investorrelations.provident.bank/")
                            for (u, _h, _d) in out))

    def test_non_q4_site_returns_none(self):
        # No q4 marker / no apiKey → not Q4 → None so the caller HTML-scrapes.
        with patch.object(ir, "_fetch", return_value="<html><body>plain site</body></html>"):
            self.assertIsNone(ir._q4_press_releases("https://example.com/ir", self.CUTOFF))

    def test_unfetchable_home_returns_none(self):
        with patch.object(ir, "_fetch", return_value=None):
            self.assertIsNone(ir._q4_press_releases("https://x/ir", self.CUTOFF))

    def test_adapter_emits_q4_events_for_pfs(self):
        with patch.object(ir, "IR_URLS", {"PFS": "https://investorrelations.provident.bank/"}), \
             patch.object(ir, "_fetch", return_value=_Q4_HTML), \
             patch.object(ir.requests, "get", return_value=_FakeResp(_Q4_JSON)):
            evs = ir.IRSiteAdapter().poll(["PFS"], since=self.CUTOFF)
        self.assertTrue(evs)
        self.assertTrue(all(e.ticker == "PFS" and e.source == "ir_site" for e in evs))
        self.assertTrue(any("Adriano Duarte" in e.headline for e in evs))


if __name__ == "__main__":
    unittest.main()
