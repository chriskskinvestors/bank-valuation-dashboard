"""tests/__init__ network guard + universe seed (2026-09-30).

Discovery made live calls through unstubbed seams (SEC submissions, the SEC
ticker search, FDIC cert groups, FRED, IR-site probes) and ran the live ~6.5-min
universe build because the isolated store started empty. Pins:

  1. under the unittest runner, resolving a real host raises
     LiveNetworkBlocked — a BaseException, so requests' and the pipeline's
     `except Exception` fallbacks can't swallow it into a quiet pass;
  2. localhost still resolves (nothing local is collateral);
  3. the guard arms ONLY under unittest — `python -m tests.golden_dataset`
     imports this package and must go live;
  4. the store is seeded with the frozen universe snapshot, fresh, under the
     key prod serves from — get_universe never live-builds in a test run.
"""
import socket
import sys
import types
import unittest
from unittest.mock import patch

import tests


@unittest.skipUnless(tests._under_unittest(),
                     "guard arms only under `python -m unittest`")
class TestGuardArmed(unittest.TestCase):
    def test_real_host_is_blocked(self):
        with self.assertRaises(tests.LiveNetworkBlocked):
            socket.getaddrinfo("www.sec.gov", 443)

    def test_blocked_call_escapes_except_exception(self):
        import requests
        self.assertFalse(issubclass(tests.LiveNetworkBlocked, Exception))
        swallowed = False
        with self.assertRaises(tests.LiveNetworkBlocked):
            try:
                requests.get("https://data.sec.gov/submissions/CIK0000019617.json",
                             timeout=5)
            except Exception:
                swallowed = True
        self.assertFalse(swallowed)

    def test_localhost_resolves(self):
        self.assertTrue(socket.getaddrinfo("localhost", 8502))
        self.assertTrue(socket.getaddrinfo("127.0.0.1", 8502))


class TestGuardScope(unittest.TestCase):
    def test_not_armed_outside_unittest(self):
        # `python -m tests.golden_dataset`: __main__ has no spec yet when the
        # package imports (verified 2026-09-30).
        with patch.dict(sys.modules, {"__main__": types.SimpleNamespace(__spec__=None)}):
            self.assertFalse(tests._under_unittest())


class TestUniverseSeed(unittest.TestCase):
    def test_snapshot_is_seeded_fresh(self):
        from data.bank_universe import _load_lastgood
        universe, fresh = _load_lastgood()
        self.assertTrue(fresh)
        self.assertGreater(len(universe), 300)
        self.assertEqual(universe["JPM"]["cik"], 19617)
        self.assertEqual(universe["JPM"]["fdic_cert"], 628)


if __name__ == "__main__":
    unittest.main()
