"""
load_all_data_fast serves a fresh metrics snapshot through ordinary universe
churn, and rebuilds when the snapshot is for a materially different set of
banks (REVIEW-2026-09-24 P1-6).

The old exact `snap["n_tickers"] == len(tickers)` guard turned ANY nightly
universe change (one bank added or dropped) into an inline full-universe
rebuild (~60 s behind one memo key, every session queued) on every Home /
Screen / Compare render until refresh-home-snapshot next wrote. Dropping the
guard outright was measured unsafe on 2026-09-25: a 2-bank snapshot (written
into the local store by the nav AppTest's stub universe) was then served to a
364-bank universe. The rule is now: serve when at most _METRICS_SNAP_CHURN of
the requested banks are missing from the snapshot, else rebuild.

`load_all_data_fast` lives in app.py (a Streamlit script), so it is lifted
from the AST and executed against a fake `cache` and a counting
`load_all_data` — no Streamlit runtime.
"""
import ast
import unittest
from datetime import datetime, timedelta
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app.py"
SRC = APP.read_text(encoding="utf-8")
_NAMES = {"load_all_data_fast", "_METRICS_SNAP_KEY", "_METRICS_SNAP_TTL_S",
          "_METRICS_SNAP_CHURN"}


def _lift() -> dict:
    tree = ast.parse(SRC)
    picked = [n for n in ast.walk(tree)
              if (isinstance(n, ast.FunctionDef) and n.name in _NAMES)
              or (isinstance(n, ast.Assign) and any(
                  isinstance(t, ast.Name) and t.id in _NAMES for t in n.targets))]
    found = {n.name if isinstance(n, ast.FunctionDef) else n.targets[0].id
             for n in picked}
    assert found == _NAMES, f"not found in app.py: {_NAMES - found}"
    mod = ast.Module(body=picked, type_ignores=[])
    ast.fix_missing_locations(mod)
    ns: dict = {}
    exec(compile(mod, str(APP), "exec"), ns)
    return ns


class _FakeCache:
    def __init__(self, snap):
        self.store = {"watchlist_metrics_snap": snap} if snap else {}
        self.puts = []

    def get(self, key, max_age_s=None):
        return self.store.get(key)

    def put(self, key, value):
        self.puts.append(key)
        self.store[key] = value


def _universe(n):
    return [f"T{i}" for i in range(n)]


def _snap(tickers, age_s: float = 60):
    return {"cached_at": (datetime.now() - timedelta(seconds=age_s)).isoformat(),
            "n_tickers": len(tickers),
            "metrics": [{"ticker": t} for t in tickers]}


class TestSnapshotChurnTolerance(unittest.TestCase):
    def _run(self, snap, tickers):
        ns = _lift()
        builds = []
        ns["cache"] = _FakeCache(snap)
        ns["load_all_data"] = lambda t: builds.append(list(t)) or [{"ticker": x} for x in t]
        out = ns["load_all_data_fast"](tickers)
        return out, builds, ns["cache"]

    def test_one_bank_added_overnight_serves_snapshot(self):
        # Snapshot built on 364 banks; tonight's universe has one more.
        # Pre-fix: 364 != 365 → full inline rebuild on every render.
        uni = _universe(365)
        out, builds, fake = self._run(_snap(uni[:364]), uni)
        self.assertEqual([], builds)
        self.assertEqual(364, len(out))
        self.assertEqual([], fake.puts)

    def test_one_bank_dropped_overnight_serves_snapshot(self):
        uni = _universe(364)
        out, builds, _ = self._run(_snap(uni + ["GONE"]), uni)
        self.assertEqual([], builds)
        self.assertEqual(365, len(out))

    def test_churn_at_the_tolerance_is_served(self):
        uni = _universe(364)
        tol = int(_lift()["_METRICS_SNAP_CHURN"] * 364)      # 18 banks
        out, builds, _ = self._run(_snap(uni[tol:]), uni)
        self.assertEqual([], builds)

    def test_snapshot_for_a_different_small_set_rebuilds(self):
        # The measured 2026-09-25 case: a 2-bank snapshot vs a 364-bank universe.
        uni = _universe(364)
        out, builds, fake = self._run(_snap(["AMAL", "JPM"]), uni)
        self.assertEqual([uni], builds)
        self.assertEqual(364, len(out))
        self.assertEqual(["watchlist_metrics_snap"], fake.puts)

    def test_churn_just_past_the_tolerance_rebuilds(self):
        uni = _universe(364)
        tol = int(_lift()["_METRICS_SNAP_CHURN"] * 364)
        out, builds, _ = self._run(_snap(uni[tol + 1:]), uni)
        self.assertEqual([uni], builds)

    def test_exact_match_served(self):
        uni = _universe(3)
        out, builds, _ = self._run(_snap(uni), uni)
        self.assertEqual([], builds)
        self.assertEqual(3, len(out))

    def test_stale_snapshot_rebuilds_and_persists(self):
        ttl = _lift()["_METRICS_SNAP_TTL_S"]
        uni = _universe(3)
        out, builds, fake = self._run(_snap(uni, age_s=ttl + 60), uni)
        self.assertEqual([uni], builds)
        self.assertEqual(["watchlist_metrics_snap"], fake.puts)

    def test_no_snapshot_rebuilds(self):
        out, builds, _ = self._run(None, ["A", "B"])
        self.assertEqual([["A", "B"]], builds)
        self.assertEqual(2, len(out))


if __name__ == "__main__":
    unittest.main()
