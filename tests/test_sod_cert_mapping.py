"""
(2026-08-02) refresh_sod tagged branches with ticker=None for 9 PUBLIC banks.

The job built its cert→ticker map by iterating BANK_MAP's and
_RESOLVED_FROM_JSON's raw dicts. get_fdic_cert() is the LOOKUP-TIME resolver and
applies curated cert corrections on top of those, so every bank whose cert comes
from a correction was absent from the map — STT, BPOP, HWC, WAFD, NEWT, ALPIB,
FRBT, FXNC, KISB. Their branches were ingested with no ticker, joined the
unmapped pool, and rendered on Geographic as though the bank were private: blank
ticker cell, no Company-page deep link, and (before the picker became
cert-keyed) not selectable in By Bank(s) at all. Deposits were never wrong —
only unattributed.

Pins:
  1. the map is built through get_fdic_cert, not raw dict iteration;
  2. a ticker whose cert exists ONLY via the resolver still lands in the map;
  3. the raw maps remain a floor, so a universe hiccup can't shrink coverage;
  4. one cert never flips between tickers (setdefault, not overwrite).

Pure-function tests over the job's map-building block; no FDIC calls.

Run: python -m unittest tests.test_sod_cert_mapping
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()


def _build_map(universe, resolver, bank_map, resolved_json):
    """Mirror of the job's map-building block (jobs/refresh_sod.main).

    Kept in the test rather than imported because main() also enumerates every
    FDIC institution and spawns a thread pool; the structural test below pins
    that the job's real code still routes through get_fdic_cert."""
    cert_to_ticker: dict[int, str] = {}
    for ticker in sorted(universe):
        cert = resolver(ticker)
        if cert:
            cert_to_ticker.setdefault(int(cert), ticker)
    for ticker, info in bank_map.items():
        cert = info.get("fdic_cert")
        if cert:
            cert_to_ticker.setdefault(int(cert), ticker)
    for t, info in resolved_json.items():
        cert = info.get("fdic_cert")
        if cert:
            cert_to_ticker.setdefault(int(cert), t)
    return cert_to_ticker


class TestCertMapCoversResolverOnlyBanks(unittest.TestCase):
    def test_resolver_only_cert_is_mapped(self):
        """The exact bug: cert reachable via get_fdic_cert but absent from the
        raw dicts. STT's real cert (14) stands in for the 9 affected banks."""
        universe = ["STT", "JPM"]
        resolver = {"STT": 14, "JPM": 628}.get
        bank_map = {"JPM": {"fdic_cert": 628}}      # STT missing, as it was
        m = _build_map(universe, resolver, bank_map, {})
        self.assertEqual(m.get(14), "STT",
                         "a corrected cert must still tag its bank's branches")
        self.assertEqual(m.get(628), "JPM")

    def test_raw_maps_remain_a_floor(self):
        """A universe that comes back short must not shrink coverage below the
        static mappings."""
        m = _build_map([], lambda t: None,
                       {"JPM": {"fdic_cert": 628}},
                       {"BAC": {"fdic_cert": 3510}})
        self.assertEqual(m.get(628), "JPM")
        self.assertEqual(m.get(3510), "BAC")

    def test_resolver_wins_and_cert_never_flips(self):
        """setdefault, not overwrite: the resolver is authoritative and a single
        cert must not oscillate between tickers depending on iteration order."""
        m = _build_map(["NEWT"], {"NEWT": 18734}.get,
                       {"STALE": {"fdic_cert": 18734}}, {})
        self.assertEqual(m.get(18734), "NEWT")

    def test_resolver_exception_does_not_abort_the_map(self):
        def boom(t):
            if t == "BAD":
                raise RuntimeError("resolver down")
            return {"JPM": 628}.get(t)

        # The job guards each resolver call; mirror that here.
        def guarded(t):
            try:
                return boom(t)
            except Exception:
                return None

        m = _build_map(["BAD", "JPM"], guarded, {}, {})
        self.assertEqual(m.get(628), "JPM")


class TestJobUsesTheResolver(unittest.TestCase):
    """Structural: the job itself must route through get_fdic_cert. Without
    this, the map could silently regress to raw-dict iteration again."""

    def test_refresh_sod_builds_map_via_get_fdic_cert(self):
        src = (REPO / "jobs/refresh_sod.py").read_text(encoding="utf-8")
        self.assertIn("get_fdic_cert", src)
        self.assertIn("get_universe_tickers", src)
        i = src.index("cert_to_ticker: dict[int, str] = {}")
        window = src[i:i + 700]
        self.assertIn("get_fdic_cert(ticker)", window,
                      "the cert->ticker map must be built with the lookup-time "
                      "resolver, not by iterating BANK_MAP's raw dicts")


if __name__ == "__main__":
    unittest.main()
