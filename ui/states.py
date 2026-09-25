"""Shared loading-skeleton and empty-state components (polish pass,
owner-approved via the KSK Polish Pass mockup 2026-09-03).

skeleton(...)    — drop-in replacement for `st.spinner("Loading ...")`:
                   shimmer placeholder rows hold the pane's structure while
                   the wrapped block computes, then vanish. Use it where the
                   spinner text carried no information beyond "loading".
                   Keep a real st.spinner where the message matters (long
                   AI parses, multi-step jobs).

empty_state(...) — the ONE way to say a pane has nothing to show: what is
                   absent + optionally why/when it would appear. Replaces
                   improvised dashes, bare captions, and st.info boxes.
                   Never fabricates; never looks broken.

CSS for both lives in ui/styles.py (.ksk-skel / .ksk-empty).
"""
from __future__ import annotations

import html
from contextlib import contextmanager

import streamlit as st

# Width cycle for shimmer bars — irregular on purpose so the placeholder
# reads as "rows of text coming", not a barcode.
_WIDTHS = (80, 55, 70, 45, 65, 75, 50, 60)


def skeleton_html(rows: int = 6, cols: int = 4) -> str:
    out = ['<div class="ksk-skel">']
    for r in range(rows):
        out.append('<div class="row">')
        for c in range(cols):
            w = _WIDTHS[(r * cols + c) % len(_WIDTHS)]
            out.append(f'<i style="width:{w}%"></i>')
        out.append("</div>")
    out.append("</div>")
    return "".join(out)


@contextmanager
def skeleton(rows: int = 6, cols: int = 4):
    """`with skeleton(): <compute + render>` — the placeholder occupies the
    slot until the block finishes, then clears (content renders below it,
    exactly like st.spinner's layout)."""
    slot = st.empty()
    try:
        slot.markdown(skeleton_html(rows, cols), unsafe_allow_html=True)
    except Exception:
        pass
    try:
        yield
    finally:
        try:
            slot.empty()
        except Exception:
            pass


def group_ratio_note(ticker: str, rec) -> str | None:
    """One-line explanation for a multi-charter bank's blank average-based
    ratios, or None for a single-charter bank.

    data/cert_group consolidates a multi-charter holdco's call reports and
    deliberately sets FDIC's AVERAGE-balance ratios (ROA, ROE, NIMY, NTLNLSR,
    yields/costs … AVERAGE_BASED_RATIOS) to None — they cannot be rebuilt
    from period-end levels. Without this note JPM (two charters) showed five
    silent dashes on Corporate Profile, Financial Highlights and Performance
    Analysis (UX review 2026-09-24 P0-05). `rec` is the newest consolidated
    record (dict or pandas row) carrying `_charter_count`."""
    try:
        n = int(rec.get("_charter_count"))
    except (AttributeError, TypeError, ValueError):
        return None
    if n <= 1:
        return None
    return (f"ROAA, ROAE, net interest margin, the net charge-off ratio and "
            f"other ratios FDIC computes on average balances are n/a for "
            f"{ticker}: its call-report figures consolidate {n} bank charters, "
            "and those averages can't be combined across charters.")


def empty_state(title: str, hint: str | None = None) -> None:
    """Standard explained-absence block. `title` says WHAT is absent
    ("No insider transactions in the last 90 days"); `hint` optionally says
    why/when it would appear. Both are plain text (HTML-escaped here)."""
    body = (f'<div class="ksk-empty"><div class="ico">&#9702;</div>'
            f'<div class="l1">{html.escape(title)}</div>')
    if hint:
        body += f'<div class="l2">{html.escape(hint)}</div>'
    body += "</div>"
    st.markdown(body, unsafe_allow_html=True)
