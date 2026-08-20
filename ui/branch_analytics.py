"""
Market Analysis branch tabs (SNL-BUILD-PLAN §11): Branch List, Branch Map,
Branch Competitors, Branch Proximity, Merger Planning (HHI / market overlap),
and Market Demographics — all on the persisted FDIC SOD store
(data/branches_store), demographics on the Census ACS client.

Every figure is FDIC SOD as stored (deposits are $thousands in SOD; the store
keeps them as reported — displayed via $-formatters at ×1000) or a transparent
calculation on it (shares, HHI = Σ(share%²) on the DOJ 0-10,000 scale).
A bank with no SOD rows renders an honest empty state, never zeros.
"""
from __future__ import annotations

import math

import pandas as pd
import streamlit as st

from data.bank_mapping import get_bank_info, get_fdic_cert
from utils.formatting import fmt_dollars


def _cert(ticker):
    try:
        return get_fdic_cert(ticker)
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def _roster(cert: int) -> pd.DataFrame:
    from data.branches_store import get_branches_by_cert
    return get_branches_by_cert(cert)


@st.cache_data(ttl=3600, show_spinner=False)
def _county_banks(stcntybr: str, year: int) -> pd.DataFrame:
    from data.branches_store import get_banks_by_county
    return get_banks_by_county(stcntybr, year=year)


@st.cache_data(ttl=3600, show_spinner=False)
def _state_branches(state: str, year: int) -> pd.DataFrame:
    from data.branches_store import get_branches_by_state
    return get_branches_by_state(state, year=year)


def _dep_usd(v) -> str:
    """SOD deposits are $thousands — convert at the display boundary."""
    return "—" if v is None or pd.isna(v) else fmt_dollars(float(v) * 1000, 1)


def _empty(ticker):
    st.caption(f"No FDIC Summary-of-Deposits branch records stored for "
               f"{ticker} — n/a. (The SOD store covers FDIC-insured branch "
               "networks; some charters report no branch offices.)")


# ── Branch List ──────────────────────────────────────────────────────────────
def render_branch_list(ticker):
    cert = _cert(ticker)
    if not cert:
        return _empty(ticker)
    df = _roster(cert)
    if df.empty:
        return _empty(ticker)
    yr = int(df.iloc[0]["year"])
    total = df["deposits"].sum(skipna=True)
    st.markdown(f"**{len(df)} branches** · {df['state'].nunique()} states · "
                f"{df['stcntybr'].nunique()} counties · "
                f"deposits {_dep_usd(total)} — FDIC SOD {yr}")
    out = df[["branch_name", "address", "city", "state", "county", "msa_name",
              "deposits"]].copy()
    out["share_of_bank"] = (df["deposits"] / total * 100).round(2) if total else None
    out["deposits"] = df["deposits"].map(_dep_usd)
    out.columns = ["Branch", "Address", "City", "ST", "County", "MSA",
                   "Deposits", "% of bank"]
    st.dataframe(out, use_container_width=True, hide_index=True, height=520)


# ── Branch Map ───────────────────────────────────────────────────────────────
def render_branch_map(ticker):
    cert = _cert(ticker)
    if not cert:
        return _empty(ticker)
    df = _roster(cert)
    pts = df.dropna(subset=["lat", "lng"]) if not df.empty else df
    if pts.empty:
        return _empty(ticker)
    import plotly.express as px
    from ui.geo_view import _fit_viewport
    center, zoom = _fit_viewport(pts["lat"], pts["lng"])
    fig = px.scatter_mapbox(
        pts, lat="lat", lon="lng",
        size=pts["deposits"].fillna(0).clip(lower=1),
        hover_name="branch_name",
        hover_data={"city": True, "state": True, "lat": False, "lng": False,
                    "deposits": ":,"},
        size_max=18, zoom=zoom, center=center)
    fig.update_layout(mapbox_style="carto-positron", height=560,
                      margin=dict(l=0, r=0, t=0, b=0))
    st.plotly_chart(fig, use_container_width=True, key=f"brmap_{ticker}")
    st.caption(f"{len(pts)} mapped branches (dot size = SOD deposits, "
               f"$thousands as reported); survey year {int(pts.iloc[0]['year'])}.")


# ── Branch Competitors ───────────────────────────────────────────────────────
def render_branch_competitors(ticker):
    """Competitors inside the bank's own county footprint: for every county the
    bank operates in, aggregate every OTHER bank's branches/deposits there."""
    cert = _cert(ticker)
    if not cert:
        return _empty(ticker)
    df = _roster(cert)
    if df.empty:
        return _empty(ticker)
    yr = int(df.iloc[0]["year"])
    name = (get_bank_info(ticker) or {}).get("name") or ticker
    footprint = sorted(df["stcntybr"].dropna().unique())
    rows: dict[str, dict] = {}
    fp_total = 0.0
    for fips in footprint:
        cb = _county_banks(str(fips), yr)
        if cb.empty:
            continue
        fp_total += float(cb["total_deposits"].sum())
        for _, r in cb.iterrows():
            key = r["bank_name"]
            d = rows.setdefault(key, {"ticker": r["ticker"], "counties": 0,
                                      "branches": 0, "deposits": 0.0})
            d["counties"] += 1
            d["branches"] += int(r["n_branches"])
            d["deposits"] += float(r["total_deposits"] or 0)
    if not rows:
        return _empty(ticker)
    tbl = (pd.DataFrame([{"Bank": k, **v} for k, v in rows.items()])
           .sort_values("deposits", ascending=False).head(26))
    tbl["share_of_footprint"] = (tbl["deposits"] / fp_total * 100).round(2)
    tbl["deposits"] = tbl["deposits"].map(_dep_usd)
    tbl = tbl.rename(columns={"ticker": "Ticker", "counties": "Shared counties",
                              "branches": "Branches", "deposits": "Deposits",
                              "share_of_footprint": "% of footprint deposits"})
    st.markdown(f"**Competitors across {name}'s {len(footprint)}-county "
                f"footprint** — FDIC SOD {yr} (subject bank included for rank "
                "context)")
    st.dataframe(tbl, use_container_width=True, hide_index=True, height=520)


# ── Branch Proximity ─────────────────────────────────────────────────────────
def _haversine_miles(lat1, lng1, lat2, lng2):
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def render_branch_proximity(ticker):
    """Nearest competitor branch to each subject branch (within the subject's
    states), plus how contested the network is at 1 / 5 miles."""
    cert = _cert(ticker)
    if not cert:
        return _empty(ticker)
    df = _roster(cert)
    pts = df.dropna(subset=["lat", "lng"]) if not df.empty else df
    if pts.empty:
        return _empty(ticker)
    yr = int(pts.iloc[0]["year"])
    comp_frames = []
    for stt in sorted(pts["state"].dropna().unique()):
        sb = _state_branches(stt, yr)
        if not sb.empty:
            comp_frames.append(sb[sb["cert"] != cert].dropna(subset=["lat", "lng"]))
    if not comp_frames:
        return _empty(ticker)
    comp = pd.concat(comp_frames, ignore_index=True)
    if comp.empty:
        st.caption("No competitor branches stored in this bank's states — n/a.")
        return
    import numpy as np
    clat = np.radians(comp["lat"].to_numpy())
    clng = np.radians(comp["lng"].to_numpy())
    out_rows = []
    for _, b in pts.iterrows():
        p1 = math.radians(b["lat"]); l1 = math.radians(b["lng"])
        a = (np.sin((clat - p1) / 2) ** 2
             + math.cos(p1) * np.cos(clat) * np.sin((clng - l1) / 2) ** 2)
        d = 2 * 3958.8 * np.arcsin(np.sqrt(a))
        i = int(d.argmin())
        out_rows.append({"Branch": b["branch_name"], "City": b["city"],
                         "ST": b["state"],
                         "Nearest competitor": comp.iloc[i]["branch_name"],
                         "Competitor bank": comp.iloc[i]["bank_name"],
                         "Distance (mi)": round(float(d[i]), 2)})
    tbl = pd.DataFrame(out_rows).sort_values("Distance (mi)")
    dists = tbl["Distance (mi)"]
    st.markdown(
        f"**Median nearest-competitor distance {dists.median():.2f} mi** · "
        f"{(dists <= 1).mean() * 100:.0f}% of branches contested within 1 mi · "
        f"{(dists <= 5).mean() * 100:.0f}% within 5 mi — FDIC SOD {yr}, "
        "competitors limited to the bank's own states")
    st.dataframe(tbl, use_container_width=True, hide_index=True, height=500)


# ── Merger Planning (HHI / market overlap) ───────────────────────────────────
def render_merger_planning(ticker, ctx=None):
    """Pro-forma deposit-HHI screen for a hypothetical combination: shared
    counties, each side's share, pre/post HHI and ΔHHI on the DOJ 0-10,000
    scale, flagged against the banking guideline (post > 1,800 AND Δ > 200)."""
    cert = _cert(ticker)
    if not cert:
        return _empty(ticker)
    df = _roster(cert)
    if df.empty:
        return _empty(ticker)
    yr = int(df.iloc[0]["year"])
    from data.branches_store import get_branch_counts_by_bank
    banks = get_branch_counts_by_bank()
    banks = banks[banks["cert"] != cert] if not banks.empty else banks
    if banks.empty:
        return _empty(ticker)
    opts = {f"{r['ticker'] or '—'} — {r['bank_name']}": int(r["cert"])
            for _, r in banks.iterrows()}
    pick = st.selectbox("Hypothetical partner", sorted(opts),
                        key=f"mp_partner_{ticker}")
    p_cert = opts[pick]
    p_df = _roster(p_cert)
    if p_df.empty:
        st.caption("Partner has no stored SOD branches — n/a.")
        return
    shared = sorted(set(df["stcntybr"].dropna()) & set(p_df["stcntybr"].dropna()))
    if not shared:
        st.markdown("**No overlapping counties** — a combination raises no "
                    "market-concentration screen on deposit HHI.")
        return
    rows = []
    for fips in shared:
        cb = _county_banks(str(fips), yr)
        if cb.empty:
            continue
        tot = float(cb["total_deposits"].sum())
        if not tot:
            continue
        shares = cb["total_deposits"].astype(float) / tot * 100
        pre = float((shares ** 2).sum())
        # Identify the two parties' county deposits by matching the roster rows.
        subj = float(df[df["stcntybr"] == fips]["deposits"].sum() or 0)
        part = float(p_df[p_df["stcntybr"] == fips]["deposits"].sum() or 0)
        s_share, p_share = subj / tot * 100, part / tot * 100
        post = pre - s_share ** 2 - p_share ** 2 + (s_share + p_share) ** 2
        delta = post - pre
        rows.append({
            "County": str(cb.iloc[0].get("county") or fips),
            "ST": str(cb.iloc[0].get("state") or ""),
            "Subject share %": round(s_share, 2),
            "Partner share %": round(p_share, 2),
            "Market deposits": _dep_usd(tot),
            "Pre HHI": round(pre), "Post HHI": round(post),
            "ΔHHI": round(delta),
            "Screen": "FLAG" if (post > 1800 and delta > 200) else "clear",
        })
    if not rows:
        return _empty(ticker)
    tbl = pd.DataFrame(rows).sort_values("ΔHHI", ascending=False)
    n_flag = int((tbl["Screen"] == "FLAG").sum())
    st.markdown(f"**{len(shared)} overlapping counties · {n_flag} flagged** "
                "(banking guideline: post-merger HHI > 1,800 AND ΔHHI > 200) — "
                f"deposit HHI on FDIC SOD {yr}; a screen flag is an analytical "
                "indicator, not a legal determination.")
    st.dataframe(tbl, use_container_width=True, hide_index=True, height=480)


# ── Market Demographics ──────────────────────────────────────────────────────
def render_market_demographics(ticker):
    """Census ACS demographics for the bank's deposit-weighted footprint
    counties. Honest n/a until CENSUS_API_KEY is configured."""
    cert = _cert(ticker)
    if not cert:
        return _empty(ticker)
    df = _roster(cert)
    if df.empty:
        return _empty(ticker)
    from data.census_client import get_county_demographics
    by_cty = (df.groupby("stcntybr")
              .agg(deposits=("deposits", "sum"), county=("county", "max"),
                   state=("state", "max"))
              .sort_values("deposits", ascending=False).head(15))
    rows, missing = [], 0
    for fips, r in by_cty.iterrows():
        demo = None
        if fips and len(str(fips)) >= 5:
            demo = get_county_demographics(str(fips)[:2], str(fips)[2:5])
        if not demo:
            missing += 1
            continue
        rows.append({
            "County": f"{r['county']}, {r['state']}",
            "Bank deposits": _dep_usd(r["deposits"]),
            "Population": f"{demo['population']:,.0f}" if demo.get("population") else "—",
            "Median HH income": (fmt_dollars(demo["median_hh_income"], 0)
                                 if demo.get("median_hh_income") else "—"),
            "Median home value": (fmt_dollars(demo["median_home_value"], 0)
                                  if demo.get("median_home_value") else "—"),
            "Unemployment %": (f"{demo['unemployment_rate_pct']:.1f}"
                               if demo.get("unemployment_rate_pct") is not None else "—"),
            "Vintage": demo.get("vintage", ""),
        })
    if not rows:
        st.caption(
            "Census demographics unavailable — set CENSUS_API_KEY (free signup: "
            "api.census.gov/data/key_signup.html) in the environment / Secret "
            "Manager. The bank's footprint counties are ready to join the "
            "moment the key lands.")
        return
    st.markdown(f"**Top {len(rows)} footprint counties by deposits** — Census "
                "ACS 5-year joined on FDIC SOD county FIPS")
    if missing:
        st.caption(f"{missing} counties without a Census response are omitted.")
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                 height=460)
