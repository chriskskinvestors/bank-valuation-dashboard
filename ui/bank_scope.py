"""
Shared bank-scope selector — the one place both Screen and Compare resolve "which
banks am I looking at" into a concrete metrics subset.

Scope types:
  • All banks         — the full loaded universe
  • Asset-size tier   — dynamic cohort (analysis/peer_groups.group_banks → by_size)
  • Business mix      — dynamic cohort (… → by_mix)
  • Saved group       — a named, persisted list (data/bank_groups)
  • Manual            — an ad-hoc multiselect

Returns ``(metrics_subset, tickers, label)``. The subset is sliced from the already
loaded ``all_metrics`` (the full-universe snapshot), so every scope shares ONE load
path and ONE freshness — fixing the old "Watchlist vs All Banks were the same set
but loaded differently" bug.
"""
from __future__ import annotations

import streamlit as st

from analysis.peer_groups import group_banks
from data import bank_groups, bank_geography
from data.bank_mapping import get_name

SCOPE_TYPES = ["All banks", "Asset-size tier", "Business mix",
               "State", "Region", "Saved group", "Manual"]


def _subset(all_metrics: list[dict], tickers) -> list[dict]:
    wanted = set(tickers)
    return [m for m in all_metrics if m.get("ticker") in wanted]


def _seed_once():
    """Seed the Portfolio group from portfolio.json one time per session."""
    if not st.session_state.get("_bank_groups_seeded"):
        try:
            bank_groups.ensure_portfolio_seed()
        except Exception:
            pass
        st.session_state["_bank_groups_seeded"] = True


def scope_type_options(include_manual: bool = True) -> list[str]:
    """The scope-type choices (the first selectbox). Exposed so a caller can render
    the type picker on its own (e.g. inside Screen's one-row segmented toolbar) and
    render the secondary picker separately via ``render_scope_sub``."""
    return SCOPE_TYPES if include_manual else [t for t in SCOPE_TYPES if t != "Manual"]


def render_scope_selector(
    all_metrics: list[dict],
    key_prefix: str,
    *,
    include_manual: bool = True,
) -> tuple[list[dict], list[str], str]:
    """Render the scope picker (type + secondary) inline and return
    (metrics_subset, tickers, label). Used where stacking the two controls is fine
    (e.g. Compare). Screen splits them — type in the toolbar, secondary below —
    via ``scope_type_options`` + ``render_scope_sub`` so its segmented toolbar
    stays one row tall.

    Streamlit widgets are keyed by ``key_prefix`` so Screen and Compare keep
    independent selections.
    """
    scope_type = st.selectbox("Scope", scope_type_options(include_manual),
                              key=f"{key_prefix}_scope_type")
    return render_scope_sub(all_metrics, scope_type, key_prefix,
                            include_manual=include_manual)


def render_scope_sub(
    all_metrics: list[dict],
    scope_type: str,
    key_prefix: str,
    *,
    include_manual: bool = True,
) -> tuple[list[dict], list[str], str]:
    """Resolve a chosen ``scope_type`` into (metrics_subset, tickers, label),
    rendering the SECONDARY picker (cohort / state / region / group / manual) only
    when the type needs one. For 'All banks' it renders nothing — so a caller can
    place this below a toolbar without adding height for the no-sub-picker case."""
    _seed_once()

    if scope_type == "All banks":
        return list(all_metrics), [m.get("ticker") for m in all_metrics], "All banks"

    if scope_type in ("Asset-size tier", "Business mix"):
        groups = group_banks(all_metrics)
        bucket = groups["by_size"] if scope_type == "Asset-size tier" else groups["by_mix"]
        options = list(bucket.keys())
        if not options:
            st.info("No cohorts computed yet — load more banks.")
            return [], [], scope_type
        picked = st.selectbox(scope_type, options, key=f"{key_prefix}_cohort")
        subset = bucket.get(picked, [])
        return subset, [m.get("ticker") for m in subset], picked

    if scope_type in ("State", "Region"):
        # HQ state of the FDIC bank subsidiary (authoritative; unknowns bucket
        # under "Unknown" rather than being guessed into a state).
        states = bank_geography.get_states_for(
            [m.get("ticker") for m in all_metrics if m.get("ticker")])
        buckets: dict[str, list] = {}
        for m in all_metrics:
            st_code = states.get(m.get("ticker"), "")
            if scope_type == "State":
                key = st_code or "Unknown"
            else:
                key = bank_geography.region_for_state(st_code)
            buckets.setdefault(key, []).append(m)
        options = sorted(buckets, key=lambda k: (k in ("Unknown", "Other"), k))
        if not options:
            st.info("No geography resolved yet.")
            return [], [], scope_type
        picked = st.selectbox(
            scope_type, options,
            format_func=lambda k: f"{k} ({len(buckets[k])})",
            key=f"{key_prefix}_geo_{scope_type}",
        )
        subset = buckets.get(picked, [])
        return subset, [m.get("ticker") for m in subset], f"{scope_type}: {picked}"

    if scope_type == "Saved group":
        glist = bank_groups.list_groups()
        if not glist:
            st.info("No saved groups yet. Build one with the **Groups** button on a "
                    "screen, or pick **Manual** to assemble one.")
            return [], [], "Saved group"
        # Order by tag (folder) then name so tagged groups cluster; the option label
        # carries the tag + count, and the group's description shows beneath.
        glist = sorted(glist, key=lambda g: (g.get("tag", "") or "~", g["name"]))
        by_name = {g["name"]: g for g in glist}
        picked = st.selectbox(
            "Group", [g["name"] for g in glist],
            format_func=lambda n: (
                (f"[{by_name[n].get('tag')}] " if by_name[n].get("tag") else "")
                + f"{n} ({by_name[n]['count']})"),
            key=f"{key_prefix}_group",
        )
        _desc = by_name.get(picked, {}).get("description", "")
        if _desc:
            st.caption(_desc)
        tickers = bank_groups.get_group_tickers(picked)
        subset = _subset(all_metrics, tickers)
        missing = len(tickers) - len(subset)
        label = f"{picked} ({len(subset)})"
        if missing > 0:
            label += f" · {missing} not in universe"
        return subset, [m.get("ticker") for m in subset], label

    # Manual
    available = sorted({m.get("ticker") for m in all_metrics if m.get("ticker")})
    picked = st.multiselect(
        "Pick banks", available,
        format_func=lambda t: f"{t} — {get_name(t)}" if get_name(t) and get_name(t) != t else t,
        key=f"{key_prefix}_manual",
    )
    subset = _subset(all_metrics, picked)
    return subset, list(picked), f"Manual ({len(picked)})"
