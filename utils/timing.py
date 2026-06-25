"""
Per-section timing (docs/PERFORMANCE.md lever 6: measure, don't guess).

Two outputs from the same marks:
  • Cloud Run logs — ``[timing] home.markets_rates 2140ms`` (server ground
    truth; only marks ≥ 50ms, to skip noise).
  • In-app panel — when the URL carries ``?perf=1``, every mark (no threshold)
    is stashed in session_state and ``render_timing_panel()`` shows a per-rerun
    breakdown. Lets us read WARM tab-switch costs without scraping logs.

Both paths are best-effort and never raise into the app: the session_state
write is guarded so ``timed()`` is safe from worker threads (no script context)
and from non-Streamlit callers (jobs).
"""
from __future__ import annotations

import time
from contextlib import contextmanager

_SS_KEY = "_timing_marks"


def _perf_on() -> bool:
    try:
        import streamlit as st
        return st.query_params.get("perf") == "1"
    except Exception:
        return False


def _record(label: str, ms: float) -> None:
    if ms >= 50:  # logs: only sections that actually cost
        print(f"[timing] {label} {ms:.0f}ms", flush=True)
    try:  # panel: every mark, but only when explicitly profiling
        import streamlit as st
        if st.query_params.get("perf") == "1":
            st.session_state.setdefault(_SS_KEY, {})[label] = ms
    except Exception:
        pass  # off-script thread or non-Streamlit caller — skip the stash


@contextmanager
def timed(label: str):
    t0 = time.perf_counter()
    try:
        yield
    finally:
        _record(label, (time.perf_counter() - t0) * 1000)


def render_timing_panel() -> None:
    """Show the last rerun's per-phase breakdown when ``?perf=1`` is set. Call
    near the top of the script so it survives sections that end in st.stop()
    (their marks were recorded on the prior rerun and persist in session_state)."""
    if not _perf_on():
        return
    try:
        import streamlit as st
        marks = st.session_state.get(_SS_KEY, {})
        if not marks:
            st.caption("⏱ perf on — click a tab to record timings…")
            return
        parts = " · ".join(f"**{k}** {v:.0f}ms"
                           for k, v in sorted(marks.items(), key=lambda kv: -kv[1]))
        # Accumulates each label's most-recent cost as you visit tabs — a
        # building table of per-section costs, slowest first.
        st.caption(f"⏱ {parts}")
    except Exception:
        pass
