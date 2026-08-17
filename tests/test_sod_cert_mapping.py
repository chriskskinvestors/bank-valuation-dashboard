"""
refresh_sod's cert→ticker map: two defect classes, both pinned here.

(2026-08-02) The job built the map by iterating BANK_MAP's and
_RESOLVED_FROM_JSON's raw dicts. get_fdic_cert() is the LOOKUP-TIME resolver and
applies curated cert corrections on top of those, so every bank whose cert comes
from a correction was absent from the map — STT, BPOP, HWC, WAFD, NEWT, ALPIB,
FRBT, FXNC, KISB. Their branches were ingested with no ticker, joined the
unmapped pool, and rendered on Geographic as though the bank were private: blank
ticker cell, no Company-page deep link, and (before the picker became
cert-keyed) not selectable in By Bank(s) at all. Deposits were never wrong —
only unattributed.

(2026-08) The map held ONE cert per ticker — the lead charter. 11 universe
banks are multi-bank holdcos (WTFC runs 16 charters), so branches of every
SIBLING charter got ticker=None and the same private-bank rendering. The map
now expands each lead cert through data.cert_group.get_cert_group.

Pins:
  1. the map is built through get_fdic_cert, not raw dict iteration;
  2. a ticker whose cert exists ONLY via the resolver still lands in the map;
  3. the raw maps remain a floor, so a universe hiccup can't shrink coverage;
  4. one cert never flips between tickers (setdefault, not overwrite);
  5. a multi-charter ticker's SIBLING certs all map to the ticker;
  6. a single-charter ticker (get_cert_group degrading to [cert]) maps
     exactly as before the group expansion;
  7. a duplicate cert claim keeps the first ticker and warns loudly.

Tests call the job's real _build_cert_to_ticker with its seams patched where
they are looked up (the function imports at call time); no FDIC calls.

Run: python -m unittest tests.test_sod_cert_mapping
"""
from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

from jobs.refresh_sod import _build_cert_to_ticker  # noqa: E402


def _build_map(universe, resolver, bank_map=None, resolved_json=None,
               groups=None):
    """Run the job's real map builder with every external seam patched.

    `resolver` is a callable (may raise, as the live one can); `groups` maps
    ticker → cert group. Tickers absent from `groups` degrade to [cert],
    exactly as data.cert_group.get_cert_group does for single-charter banks."""
    groups = groups or {}

    def fake_group(ticker, cert=None):
        return groups.get(ticker, [cert])

    with patch("data.bank_universe.get_universe_tickers",
               return_value=list(universe)), \
         patch("data.bank_mapping.get_fdic_cert", side_effect=resolver), \
         patch("data.bank_mapping.BANK_MAP", dict(bank_map or {})), \
         patch("data.bank_mapping._RESOLVED_FROM_JSON",
               dict(resolved_json or {})), \
         patch("data.cert_group.get_cert_group", side_effect=fake_group):
        return _build_cert_to_ticker()


class TestCertMapCoversResolverOnlyBanks(unittest.TestCase):
    def test_resolver_only_cert_is_mapped(self):
        """The exact bug: cert reachable via get_fdic_cert but absent from the
        raw dicts. STT's real cert (14) stands in for the 9 affected banks."""
        m = _build_map(["STT", "JPM"], {"STT": 14, "JPM": 628}.get,
                       bank_map={"JPM": {"fdic_cert": 628}})  # STT missing
        self.assertEqual(m.get(14), "STT",
                         "a corrected cert must still tag its bank's branches")
        self.assertEqual(m.get(628), "JPM")

    def test_raw_maps_remain_a_floor(self):
        """A universe that comes back short must not shrink coverage below the
        static mappings."""
        m = _build_map([], lambda t: None,
                       bank_map={"JPM": {"fdic_cert": 628}},
                       resolved_json={"BAC": {"fdic_cert": 3510}})
        self.assertEqual(m.get(628), "JPM")
        self.assertEqual(m.get(3510), "BAC")

    def test_resolver_wins_and_cert_never_flips(self):
        """setdefault, not overwrite: the resolver is authoritative and a single
        cert must not oscillate between tickers depending on iteration order."""
        m = _build_map(["NEWT"], {"NEWT": 18734}.get,
                       bank_map={"STALE": {"fdic_cert": 18734}})
        self.assertEqual(m.get(18734), "NEWT")

    def test_resolver_exception_does_not_abort_the_map(self):
        def boom(t):
            if t == "BAD":
                raise RuntimeError("resolver down")
            return {"JPM": 628}.get(t)

        m = _build_map(["BAD", "JPM"], boom)
        self.assertEqual(m.get(628), "JPM")


class TestCertGroupExpansion(unittest.TestCase):
    """(2026-08) Sibling charters of a multi-bank holdco must carry the ticker;
    everything else must be byte-identical to the pre-expansion map."""

    def test_sibling_certs_all_map_to_the_ticker(self):
        """WTFC-shaped: the lead cert (33396) plus siblings, all → WTFC."""
        m = _build_map(["WTFC", "JPM"], {"WTFC": 33396, "JPM": 628}.get,
                       groups={"WTFC": [33396, 33397, 34142, 57905]})
        for c in (33396, 33397, 34142, 57905):
            self.assertEqual(m.get(c), "WTFC",
                             f"sibling cert {c} must carry the holdco ticker")
        self.assertEqual(m.get(628), "JPM")

    def test_single_charter_map_is_identical(self):
        """The ~350 single-charter banks: get_cert_group degrades to [cert]
        (no `groups` entry here), so the map is exactly the old one."""
        m = _build_map(["JPM", "STT"], {"JPM": 628, "STT": 14}.get,
                       bank_map={"BAC": {"fdic_cert": 3510}})
        self.assertEqual(m, {628: "JPM", 14: "STT", 3510: "BAC"})

    def test_duplicate_cert_claim_keeps_first_and_warns(self):
        """A cert belongs to at most one ticker. When a second ticker's group
        claims it, the first claim stands and the collision is printed — never
        a silent reassignment of branch deposits between public banks."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            m = _build_map(["AAA", "ZZZ"], {"AAA": 100, "ZZZ": 200}.get,
                           groups={"AAA": [100, 999], "ZZZ": [200, 999]})
        self.assertEqual(m.get(999), "AAA", "first claim must win")
        self.assertEqual(m.get(100), "AAA")
        self.assertEqual(m.get(200), "ZZZ")
        out = buf.getvalue()
        self.assertIn("999", out, "double claim must be loud, not silent")
        self.assertIn("ZZZ", out)
        self.assertIn("AAA", out)

    def test_group_lookup_exception_degrades_to_lead_cert(self):
        """get_cert_group already degrades internally; if it ever raises, the
        job must still map the lead cert rather than drop the bank."""
        def boom_group(ticker, cert=None):
            raise RuntimeError("cache down")

        with patch("data.bank_universe.get_universe_tickers",
                   return_value=["JPM"]), \
             patch("data.bank_mapping.get_fdic_cert",
                   side_effect={"JPM": 628}.get), \
             patch("data.bank_mapping.BANK_MAP", {}), \
             patch("data.bank_mapping._RESOLVED_FROM_JSON", {}), \
             patch("data.cert_group.get_cert_group", side_effect=boom_group):
            m = _build_cert_to_ticker()
        self.assertEqual(m, {628: "JPM"})


class TestJobUsesTheResolver(unittest.TestCase):
    """Structural: the job must route through get_fdic_cert AND expand each
    lead cert through get_cert_group. Without this, the map could silently
    regress to raw-dict iteration or to one-cert-per-ticker."""

    def test_refresh_sod_builds_map_via_resolver_and_group(self):
        src = (REPO / "jobs/refresh_sod.py").read_text(encoding="utf-8")
        self.assertIn("get_universe_tickers", src)
        i = src.index("cert_to_ticker: dict[int, str] = {}")
        window = src[i:i + 900]
        self.assertIn("get_fdic_cert(ticker)", window,
                      "the cert->ticker map must be built with the lookup-time "
                      "resolver, not by iterating BANK_MAP's raw dicts")
        self.assertIn("get_cert_group(ticker", window,
                      "each lead cert must expand to the ticker's whole cert "
                      "group, or sibling charters render as private banks")
        self.assertIn("_build_cert_to_ticker()", src.split("def main")[1],
                      "main() must use the tested builder")


if __name__ == "__main__":
    unittest.main()
