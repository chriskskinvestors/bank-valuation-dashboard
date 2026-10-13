"""Branch Proximity view model — exactly what the page shows, built once.

One builder, two callers (owner decision 2026-10-13, "store what the page
shows"):
  • jobs/refresh_sod builds every universe bank × radius after the nightly
    ownership pass and after the monthly full sweep, and stores each view;
  • the page serves the stored view while the branches store is unchanged
    since it was built (data.branches_store.store_version), else builds its
    one radius live with this same code.

A view is small even for JPM: the stat-pill numbers, the top-50 competitor
banks, the 25 x 10 per-branch rows, the map points. Full pair lists are NOT
stored (JPM is ~334k pairs at 5 mi, ~1.2M at 10 mi); the page's Export builds
them on click from the live store. Building never materialises the full pair
frame either — a light (subject, candidate, distance) index carries the
counts and only the rendered rows get their columns.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from data import branches_store as bs

RADII = (1, 3, 5, 10)

# Render caps (UX-P0-06b): JPM at 5 mi — 5,142 subject branches, 40,842
# in-range competitor branches — built a 53,000 px HTML page that froze the
# tab. Caps bound what is RENDERED only; the export always carries every
# pair and the stat pills keep the full counts. Each applied cap is captioned.
TABLE_MAX_BRANCHES = 25        # largest subject branches (by subj_deposits)
TABLE_MAX_COMPETITORS = 10     # nearest competitors shown per subject branch
ROLLUP_MAX_BANKS = 50          # competitor banks, by in-range deposits
MAP_MAX_COMP_BRANCHES = 5_000  # above this, map only the top banks' branches

MAP_COLS = ["bank_name", "branch_name", "city", "state", "deposits",
            "lat", "lng"]
ROLLUP_COLS = ["cert", "ticker", "bank_name", "n_branches", "deposits"]
SHOWN_COLS = ["subj_brnum", "subj_branch_name", "subj_address", "subj_city",
              "subj_state", "cert", "ticker", "bank_name", "branch_name",
              "city", "state", "deposits", "distance_miles"]

SNAP_PREFIX = "branch_proximity_v1"


def bank_rollup(uniq_comp: pd.DataFrame) -> pd.DataFrame:
    """One row per competitor bank — unique in-range branches + their total
    deposits — largest in-range deposits first (cert breaks ties so a cap
    cut is deterministic)."""
    return (uniq_comp.groupby("cert", dropna=False)
            .agg(ticker=("ticker", "first"), bank_name=("bank_name", "first"),
                 n_branches=("brnum", "count"), deposits=("deposits", "sum"))
            .reset_index()
            .sort_values(["deposits", "cert"], ascending=[False, True]))


def map_competitors(uniq_comp: pd.DataFrame, max_branches: int,
                    n_banks: int) -> pd.DataFrame:
    """Competitor branches to plot: all of them up to `max_branches`;
    beyond that, only the in-range branches of the top `n_banks` competitor
    banks by in-range deposits."""
    if len(uniq_comp) <= max_branches:
        return uniq_comp
    top = bank_rollup(uniq_comp).head(n_banks)["cert"]
    return uniq_comp[uniq_comp["cert"].isin(top)]


def select_branch_rows(pairs: pd.DataFrame, n_branches: int,
                       n_competitors: int) -> pd.DataFrame:
    """The rendered slice of `pairs`: the `n_branches` largest subject
    branches by subj_deposits (absent deposits rank last), each with its
    `n_competitors` nearest competitors. Returns a new frame — `pairs` is
    never modified. Needs only subj_brnum, subj_deposits, distance_miles."""
    subj = (pairs.drop_duplicates("subj_brnum")[["subj_brnum", "subj_deposits"]]
            .assign(_dep=lambda d: pd.to_numeric(d["subj_deposits"],
                                                 errors="coerce"))
            .sort_values(["_dep", "subj_brnum"], ascending=[False, True],
                         na_position="last"))
    rows = pairs[pairs["subj_brnum"].isin(subj["subj_brnum"].head(n_branches))]
    return (rows.sort_values(["subj_brnum", "distance_miles"], kind="mergesort")
            .groupby("subj_brnum", sort=False).head(n_competitors))


def _empty_view(inp: dict, radius: float) -> dict:
    wc = inp["with_coords"]
    return {
        "radius": float(radius), "year": inp["year"], "reason": inp["reason"],
        "n_subject_branches": int(len(inp["subj"])),
        "n_subject_missing_coords": int(inp["n_subject_missing_coords"]),
        "n_competitor_missing_coords": int(inp["n_competitor_missing_coords"]),
        "n_pairs": 0, "n_comp_banks": 0, "n_comp_branches": 0,
        "comp_deposits": None,
        "subj_points": (wc[MAP_COLS].reset_index(drop=True) if len(wc)
                        else pd.DataFrame(columns=MAP_COLS)),
        "comp_points": pd.DataFrame(columns=MAP_COLS),
        "rollup": pd.DataFrame(columns=ROLLUP_COLS), "n_rollup": 0,
        "shown": pd.DataFrame(columns=SHOWN_COLS), "shown_n_in_range": {},
        "n_branches_with_comp": 0,
    }


def view_from_inputs(inp: dict, radius: float) -> dict:
    """The page's view for one radius from data.branches_store
    .proximity_inputs (whose bounding box must cover `radius`)."""
    view = _empty_view(inp, radius)
    if inp["reason"]:
        return view
    wc = inp["with_coords"].reset_index(drop=True)
    cand = inp["cand"].reset_index(drop=True)
    si, ci, d = bs._in_range(wc, cand, float(radius))
    if not len(si):
        view["reason"] = f"no competitor branches within {float(radius)} miles"
        return view
    # Light pair index, in the export's order (subject order, candidate
    # order, then by (subj_brnum, distance_miles)).
    light = (pd.DataFrame({
        "si": si, "ci": ci,
        "subj_brnum": wc["brnum"].to_numpy()[si].astype(int),
        "subj_deposits": wc["deposits"].to_numpy()[si],
        "distance_miles": d})
        .sort_values(["subj_brnum", "distance_miles"])
        .reset_index(drop=True))
    # A competitor branch in range of two subject branches is one branch:
    # (cert, brnum) is the store's key within a year, i.e. one candidate row.
    first = light.drop_duplicates("ci")["ci"].to_numpy()
    uniq_comp = cand.iloc[first].reset_index(drop=True)
    for col in ("cert", "brnum"):
        uniq_comp[col] = uniq_comp[col].astype(int)
    for col in ("lat", "lng"):
        uniq_comp[col] = uniq_comp[col].astype(float)
    ro = bank_rollup(uniq_comp)
    n_in_range = light.groupby("subj_brnum").size()
    sel = select_branch_rows(light, TABLE_MAX_BRANCHES, TABLE_MAX_COMPETITORS)
    shown = bs._pairs_frame(wc, cand, sel["si"].to_numpy(),
                            sel["ci"].to_numpy(),
                            sel["distance_miles"].to_numpy())
    view.update({
        "n_pairs": int(len(light)),
        "n_comp_banks": int(uniq_comp["cert"].nunique()),
        "n_comp_branches": int(len(uniq_comp)),
        "comp_deposits": float(uniq_comp["deposits"].astype(float).sum()),
        "comp_points": map_competitors(uniq_comp, MAP_MAX_COMP_BRANCHES,
                                       ROLLUP_MAX_BANKS)[MAP_COLS]
                       .reset_index(drop=True),
        "rollup": ro.head(ROLLUP_MAX_BANKS)[ROLLUP_COLS].reset_index(drop=True),
        "n_rollup": int(len(ro)),
        "shown": shown[SHOWN_COLS].reset_index(drop=True),
        "shown_n_in_range": {int(k): int(n_in_range[k])
                             for k in shown["subj_brnum"].unique()},
        "n_branches_with_comp": int(len(n_in_range)),
    })
    return view


def build_views(cert: int, radii=RADII, year: int | None = None) -> dict:
    """{radius: view} for one bank — ONE subject + candidate fetch sized
    for the largest radius serves every radius."""
    inp = bs.proximity_inputs(int(cert), float(max(radii)), year)
    return {float(r): view_from_inputs(inp, r) for r in radii}


# ── Snapshot (de)serialisation ──────────────────────────────────────────────
_FRAMES = ("subj_points", "comp_points", "rollup", "shown")


def _py(v):
    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return None if np.isnan(v) else float(v)
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def serialize_view(view: dict) -> dict:
    out = {}
    for k, v in view.items():
        if k in _FRAMES:
            out[k] = {"columns": list(v.columns),
                      "data": [[_py(x) for x in row]
                               for row in v.itertuples(index=False)]}
        elif k == "shown_n_in_range":
            out[k] = [[int(a), int(b)] for a, b in v.items()]
        else:
            out[k] = _py(v)
    return out


def deserialize_view(payload: dict) -> dict:
    view = dict(payload)
    for k in _FRAMES:
        view[k] = pd.DataFrame(payload[k]["data"], columns=payload[k]["columns"])
    view["shown_n_in_range"] = {int(a): int(b)
                                for a, b in payload["shown_n_in_range"]}
    return view


def snapshot_key(owner_key: str, radius: float) -> str:
    return f"{SNAP_PREFIX}:{owner_key}:{float(radius):g}"


def load_view(cert: int, radius: float) -> tuple[dict, str]:
    """(view, "snapshot" | "live") for the page. The stored view is served
    only when it was built against the CURRENT branches-store version and
    survey year; anything else — no snapshot, an older store, an unknown
    version, an unreadable cache — builds this radius live (same code)."""
    year = bs.get_latest_year()
    if year is not None:
        try:
            from data import cache
            snap = cache.get(snapshot_key(bs._owner_key_of(int(cert), year),
                                          radius), max_age_s=None)
            ver = bs.store_version()
            if (snap and ver and snap.get("version") == ver
                    and snap.get("year") == year):
                return deserialize_view(snap["view"]), "snapshot"
        except Exception:                                    # noqa: BLE001
            pass
    inp = bs.proximity_inputs(int(cert), float(radius), year)
    return view_from_inputs(inp, radius), "live"


def build_snapshots(tickers, radii=RADII, log=print) -> dict:
    """Build and store every view for the given universe tickers — one per
    owning company (sibling tickers / charters share an owner key). The
    store version is read BEFORE building, so a write that lands mid-build
    leaves these snapshots stale (served live) rather than wrongly fresh.
    Returns {"built", "skipped", "failed", "seconds"}."""
    from data import cache
    from data.bank_mapping import get_fdic_cert
    t0 = time.time()
    stats = {"built": 0, "skipped": 0, "failed": 0, "seconds": 0.0}
    year = bs.get_latest_year()
    if year is None:
        log("[proximity] branches store is empty — nothing to build")
        return stats
    ver = bs.store_version()
    if ver is None:                       # first run: no stamp yet
        bs._bump_store_version()
        ver = bs.store_version()
    if ver is None:
        log("[proximity] store version unavailable — not building (a "
            "snapshot without a version can never be served)")
        stats["failed"] = 1
        return stats
    seen: set[str] = set()
    for t in tickers:
        try:
            cert = get_fdic_cert(t)
            if not cert:
                stats["skipped"] += 1
                continue
            okey = bs._owner_key_of(int(cert), year)
            if okey in seen:
                stats["skipped"] += 1
                continue
            seen.add(okey)
            for r, v in build_views(int(cert), radii, year).items():
                cache.put(snapshot_key(okey, r),
                          {"version": ver, "year": year,
                           "built_at": time.time(), "view": serialize_view(v)})
            stats["built"] += 1
        except Exception as e:                               # noqa: BLE001
            stats["failed"] += 1
            log(f"[proximity] {t}: {type(e).__name__}: {e}")
    stats["seconds"] = round(time.time() - t0, 1)
    log(f"[proximity] {stats['built']} banks x {len(radii)} radii built, "
        f"{stats['skipped']} skipped, {stats['failed']} failed in "
        f"{stats['seconds']}s (store version {ver})")
    return stats
