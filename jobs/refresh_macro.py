"""Cloud Run Job: keep the Market & Macro FRED cache warm.

The macro dashboard (ui/macro.py) renders ~25 FRED series — rates, the curve,
inflation, labor, credit spreads — every one through data.fred_client.fetch_series.
That function has a cross-instance Postgres cache (prefix ``macro_cache``) with a
1-hour TTL, but NOTHING kept it warm. So once an hour the cache lapsed and the
NEXT person to open Market & Macro paid the full penalty: ~25 live FRED calls
made SERIALLY on the render thread (and a cold Cloud Run instance paid it every
time it scaled up). That's the multi-second "Market & Macro takes forever" stall.

This job calls fetch_series for the whole series set on a schedule, so the
Postgres cache is always fresh and the render thread only ever does cheap cache
reads — never a live FRED fan-out. It mirrors jobs/refresh_home_snapshot.py: a
job that pre-pays an expensive read off the request path.

fetch_series stores the FULL history per series (the ``years`` filter is applied
only to the returned frame, not the cached JSON), so one warm call per series id —
at any ``years`` — populates the cache for every window the UI later requests.

The series id list is the union of data.fred_client.SERIES and the literals used
in ui/macro.py. It is duplicated here (not imported from ui/macro) so this job
stays free of streamlit-heavy UI imports; if the dashboard adds a new series,
add its id here too. A missing id just means that one panel stays live-fetched —
never wrong, only slower — so drift degrades gracefully.

Exit codes:
  0 — warmed (or already fresh); some individual series may have failed
  1 — could not import the macro client at all
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Union of fred_client.SERIES and every fetch_series("...") id in ui/macro.py.
# Kept literal so the job doesn't import streamlit. See module docstring.
_MACRO_SERIES = [
    # rates / curve
    "FEDFUNDS", "DFF", "DGS3MO", "DGS2", "DGS5", "DGS10", "DGS30",
    "T10Y2Y", "T10Y3M", "MORTGAGE30US",
    # inflation
    "CPIAUCSL", "CPILFESL", "PCEPILFE",
    # activity / labor / sentiment / money
    "GDP", "A191RL1Q225SBEA", "UNRATE", "PAYEMS", "INDPRO", "RSAFS",
    "HOUST", "PERMIT", "UMCSENT", "M2SL", "USREC",
    # commodities / dollar
    "DCOILWTICO", "DXY",
    # credit spreads
    "BAMLH0A0HYM2", "BAMLH0A3HYC", "BAMLC0A0CM", "BAMLC0A4CBBB",
]


def main() -> int:
    import warnings
    warnings.filterwarnings("ignore")
    try:
        from data.fred_client import fetch_series
    except Exception as e:  # pragma: no cover
        print(f"[macro-warm] cannot import fred_client: {type(e).__name__}: {e}",
              flush=True)
        return 1

    t0 = time.time()
    series = sorted(set(_MACRO_SERIES))
    print(f"[{time.strftime('%H:%M:%S')}] warming {len(series)} FRED series...",
          flush=True)

    ok = 0
    for sid in series:
        try:
            # years=10 ≥ the deepest window the UI requests; fetch_series caches
            # the full history regardless, so one call warms every window.
            df = fetch_series(sid, years=10)
            if df is not None and not df.empty:
                ok += 1
            else:
                print(f"[macro-warm] {sid}: empty", flush=True)
        except Exception as e:
            print(f"[macro-warm] {sid}: {type(e).__name__}: {e}", flush=True)

    # The recession score + headline snapshot read only the series above, so
    # warming them is enough; touch them too so a failure surfaces in logs.
    try:
        from data.fred_client import get_macro_snapshot, recession_probability
        get_macro_snapshot()
        recession_probability()
    except Exception as e:
        print(f"[macro-warm] snapshot/recession warm failed: "
              f"{type(e).__name__}: {e}", flush=True)

    print(f"[{time.strftime('%H:%M:%S')}] warmed {ok}/{len(series)} series "
          f"in {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
