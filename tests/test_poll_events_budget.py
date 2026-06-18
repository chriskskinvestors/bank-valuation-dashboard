"""jobs/poll_events.main — universe build + per-run wall-clock budget.

Pins the 2026-06-12 production incident: poll-events built its universe with
get_universe_tickers(), which fires a live rate-limited cert_is_active per
ticker. On a cold Cloud Run job FDIC throttled the burst and the build alone
blew the 900s task timeout BEFORE any adapter ran, so no news committed and the
Home page froze. The fix uses the file-based get_universe() (no per-ticker FDIC)
and adds a soft budget so one slow source can't starve the rest.

All network/DB is mocked; no live calls.
"""
from __future__ import annotations

import contextlib
import io
import sys
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Stub streamlit before importing data modules (house pattern).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)


class _FakeAdapter:
    def __init__(self, name, on_poll=None, sleep=0.0):
        self.name = name
        self._on_poll = on_poll
        self._sleep = sleep
        self.polled = False

    def poll(self, scope, since=None):
        self.polled = True
        if self._sleep:
            import time as _t
            _t.sleep(self._sleep)
        if self._on_poll:
            self._on_poll()
        return []


def _run_main(stack, adapters, time_values=None, universe=("AAA", "BBB"), env=None):
    """Drive poll_events.main with every adapter + network/DB call mocked.
    `adapters` are the 8 fakes in main()'s construction order. `env` sets extra
    os.environ keys (e.g. POLL_PROFILE). Returns (exit_code, cert_is_active_mock)."""
    import jobs.poll_events as pe
    import data.events as ev
    import data.bank_universe as bu
    import data.fdic_client as fc

    it = iter(adapters)
    # Patch each adapter class to hand back the next fake in order.
    for modpath, clsname in [
        ("data.events.sec_8k", "SEC8KAdapter"),
        ("data.events.sec_8k", "SEC8KRecentAdapter"),
        ("data.events.businesswire", "BusinessWireAdapter"),
        ("data.events.prnewswire", "PRNewswireAdapter"),
        ("data.events.globenewswire", "GlobeNewswireAdapter"),
        ("data.events.google_news", "GoogleNewsAdapter"),
        ("data.events.google_news", "GoogleNewsTopicAdapter"),
        ("data.events.yfinance_news", "YFinanceNewsAdapter"),
        ("data.events.ir_site", "IRSiteAdapter"),
    ]:
        stack.enter_context(mock.patch(f"{modpath}.{clsname}",
                                       side_effect=lambda *a, **k: next(it)))

    cert = stack.enter_context(mock.patch.object(fc, "cert_is_active"))
    stack.enter_context(mock.patch.object(bu, "get_universe",
                                          return_value={t: {} for t in universe}))
    stack.enter_context(mock.patch.object(ev, "init_schema"))
    stack.enter_context(mock.patch.object(ev, "insert_events_returning_new",
                                          side_effect=lambda evs: list(evs)))
    stack.enter_context(mock.patch.object(ev, "last_seen_published", return_value=None))
    stack.enter_context(mock.patch.object(pe, "_invalidate_fundamentals_for_filings"))
    stack.enter_context(mock.patch.object(pe, "_purge_junk_events", return_value=0))
    stack.enter_context(mock.patch.object(pe, "_summarize_recent_events", return_value=0))
    stack.enter_context(mock.patch.dict("os.environ", {}, clear=False))
    if "ANTHROPIC_API_KEY" in __import__("os").environ:
        stack.enter_context(mock.patch.dict("os.environ",
                                            {"ANTHROPIC_API_KEY": ""}, clear=False))
    if env:
        stack.enter_context(mock.patch.dict("os.environ", env, clear=False))
    if time_values is not None:
        stack.enter_context(mock.patch.object(pe.time, "time",
                                              side_effect=list(time_values)))
    # Capture the job's stdout — its ▶/× progress glyphs can't encode to the
    # Windows cp1252 test console (Cloud Run runs UTF-8).
    with contextlib.redirect_stdout(io.StringIO()):
        rc = pe.main()
    return rc, cert


class TestPollEventsUniverse(unittest.TestCase):
    def test_universe_build_does_not_hit_per_ticker_fdic(self):
        # The regression: building the universe must NOT call cert_is_active
        # (the live, rate-limited per-ticker FDIC check that blew the timeout).
        fakes = [_FakeAdapter(f"a{i}") for i in range(8)]
        with ExitStack() as stack:
            rc, cert = _run_main(stack, fakes)
        self.assertEqual(rc, 0)
        cert.assert_not_called()
        self.assertTrue(all(f.polled for f in fakes))

    def test_budget_stops_polling_new_sources(self):
        # Once the overall task budget is spent, remaining sources are skipped
        # (committed-as-we-go), never the hard 900s task kill.
        import jobs.poll_events as pe
        fakes = [_FakeAdapter(f"a{i}") for i in range(8)]
        # time.time() calls: t0, then the remaining-budget check before each
        # adapter, then the final elapsed. Make the check before adapter #2
        # land past the budget so the loop breaks.
        over = pe._TASK_BUDGET_S + 100
        times = [1000.0, 1000.0, 1000.0 + over] + [1000.0 + over] * 20
        with ExitStack() as stack:
            rc, _ = _run_main(stack, fakes, time_values=times)
        self.assertEqual(rc, 0)
        self.assertTrue(fakes[0].polled, "first adapter should run")
        self.assertFalse(fakes[1].polled, "second adapter should be skipped past budget")

    def test_slow_adapter_is_abandoned_not_fatal(self):
        # A single adapter that overruns its per-adapter cap is abandoned (hard
        # timeout) and the rest still run — the run finishes 0, never the 900s
        # task kill. Pin with a tiny cap and a first adapter that sleeps past it.
        import jobs.poll_events as pe
        fakes = [_FakeAdapter("slow", sleep=0.3)] + [_FakeAdapter(f"a{i}") for i in range(7)]
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(pe, "_PER_ADAPTER_S", 0.05))
            rc, _ = _run_main(stack, fakes)   # real clock; tiny cap trips timeout
        self.assertEqual(rc, 0, "a slow source is a non-fatal timeout, not a crash")
        self.assertTrue(fakes[1].polled, "later adapters still run after a timeout")


class TestFastProfile(unittest.TestCase):
    def test_fast_profile_runs_only_lightweight_sources(self):
        # POLL_PROFILE=fast must run ONLY SEC 8-K + the two cheap wire feeds +
        # FMP, skipping the slow full-universe Google News / Yahoo / IR scrape so
        # the job stays sub-minute and safe at a 1–5 min cadence.
        fakes = [_FakeAdapter(f"a{i}") for i in range(8)]
        with ExitStack() as stack:
            rc, _ = _run_main(stack, fakes, env={"POLL_PROFILE": "fast"})
        self.assertEqual(rc, 0)
        # Construction order in fast mode: FMP (real/unpatched), then SEC8K,
        # PRNewswire, GlobeNewswire — so fakes 0,1,2 are those three and poll.
        self.assertTrue(fakes[0].polled and fakes[1].polled and fakes[2].polled,
                        "8-K + the two wire feeds must run in fast mode")
        self.assertFalse(any(f.polled for f in fakes[3:]),
                         "Google News / Yahoo / IR must NOT run in fast mode")


if __name__ == "__main__":
    unittest.main()
