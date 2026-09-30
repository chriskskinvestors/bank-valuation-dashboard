"""
The deploy-gate coverage test uses THE shared retry policy (data/http), not a
private copy (REVIEW-2026-09-24 P2 — CLAUDE.md: data/http.py is the one retry
policy). The gate asks only yes/no, so its wrapper must keep answering the
way the old private loop did: a 200 is a pass; exhausted 429s, a non-2xx
status, or a final connection error is None (a fail), never an exception that
would crash the whole gate.
"""
import unittest
from unittest.mock import patch

import requests

import tests.test_universe_coverage as gate


class _Resp:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"{self.status_code}")
            err.response = self
            raise err


class TestGateRetryWrapper(unittest.TestCase):
    def test_ok_response_is_returned(self):
        with patch("requests.get", return_value=_Resp(200)):
            self.assertEqual(200, gate._get_with_retry("https://x").status_code)

    def test_404_is_none_not_an_exception(self):
        with patch("requests.get", return_value=_Resp(404)):
            self.assertIsNone(gate._get_with_retry("https://x"))

    def test_exhausted_429s_is_none(self):
        with patch("requests.get", return_value=_Resp(429, {"Retry-After": "0"})), \
                patch("data.http.time.sleep"):
            self.assertIsNone(gate._get_with_retry("https://x", max_attempts=2))

    def test_final_connection_error_is_none(self):
        with patch("requests.get", side_effect=requests.ConnectionError("down")), \
                patch("data.http.time.sleep"):
            self.assertIsNone(gate._get_with_retry("https://x", max_attempts=2))

    def test_gate_sends_its_user_agent(self):
        with patch("requests.get", return_value=_Resp(200)) as g:
            gate._get_with_retry("https://x")
        self.assertEqual(gate.UA, g.call_args.kwargs["headers"])

    def test_transient_failure_recheck_path_runs(self):
        # The gate's serial re-check after a 45 s cooldown only runs when a
        # bank fails the first sweep — the one path no other test reaches.
        # (Removing the private retry loop once dropped `import time` with it,
        # leaving time.sleep(45) a NameError that would have failed the
        # deploy gate on the first transient blip.)
        import contextlib
        import io
        calls = {"n": 0}

        def check(ticker):
            calls["n"] += 1
            return ticker, calls["n"] > 1, 1, 2     # fails once, then passes

        with patch("data.bank_universe.get_universe_tickers", return_value=["AAA"]), \
                patch("config.DEFAULT_WATCHLIST", []), \
                patch("data.bank_mapping.BANK_MAP", {}), \
                patch("data.bank_mapping.get_fdic_cert", return_value=2), \
                patch.object(gate, "_check_ticker", check), \
                patch("time.sleep") as sleep, \
                contextlib.redirect_stdout(io.StringIO()):
            rc = gate.main()
        self.assertEqual(0, rc)
        sleep.assert_called_once_with(45)
        self.assertEqual(2, calls["n"])

    def test_no_private_retry_loop_left(self):
        from pathlib import Path
        src = Path(gate.__file__).read_text(encoding="utf-8")
        self.assertIn("from data.http import get_with_retry", src)
        self.assertNotIn("for attempt in range", src)


if __name__ == "__main__":
    unittest.main()
