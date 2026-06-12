"""
Per-section timing logs (docs/PERFORMANCE.md lever 6: measure, don't
guess). Prints land in Cloud Run logs:  [timing] home.markets_rates 2140ms
"""
from __future__ import annotations

import time
from contextlib import contextmanager


@contextmanager
def timed(label: str):
    t0 = time.perf_counter()
    try:
        yield
    finally:
        ms = (time.perf_counter() - t0) * 1000
        if ms >= 50:  # skip noise; only sections that actually cost
            print(f"[timing] {label} {ms:.0f}ms", flush=True)
