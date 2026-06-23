"""Shared SEC throttle + hard per-request timeout for the Company Reported audit
harness (tools/_audit_*.py and tools/_verify_composition.py).

Why this exists: the audits fetch cache-free across the whole universe. A single
transient SEC stall — a connection that establishes then hangs mid-read with no
read timeout — once froze a full-universe run for hours (the per-bank try/except
never fires because nothing raises; the socket just blocks). install_throttle
caps EVERY _get at a wall-clock `timeout`, so a stall raises TimeoutError, the
caller records that one bank as an error, and the loop moves on instead of
hanging the entire run.

The fetch runs in a daemon thread: on timeout we abandon it (it dies with the
process) and the next request starts on a fresh connection — so one stuck socket
can't wedge a shared worker and starve every later request.
"""
from __future__ import annotations

import threading
import time


def install_throttle(sfs, req_per_sec: float = 7.0, timeout: float = 90.0):
    """Replace ``sfs._get`` with a polite ~req_per_sec throttle wrapped in a hard
    per-request timeout. Returns the wrapper (callers don't need it). Idempotent
    enough for a one-shot tool: call once at import time, after ``import
    data.sec_filing_scraper as sfs``."""
    min_gap = 1.0 / req_per_sec
    last = [0.0]
    orig = sfs._get

    def throttled(url, *a, **k):
        dt = time.time() - last[0]
        if dt < min_gap:
            time.sleep(min_gap - dt)
        last[0] = time.time()
        box: dict = {}

        def _call():
            try:
                box["v"] = orig(url, *a, **k)
            except BaseException as e:  # propagate the real error to the caller
                box["e"] = e

        th = threading.Thread(target=_call, daemon=True)
        th.start()
        th.join(timeout)
        if th.is_alive():
            raise TimeoutError(f"SEC fetch exceeded {timeout:.0f}s (abandoned): {url}")
        if "e" in box:
            raise box["e"]
        return box.get("v")

    sfs._get = throttled
    return throttled
