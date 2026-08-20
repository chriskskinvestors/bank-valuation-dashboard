"""
Market Analysis branch tabs (SNL-BUILD-PLAN §11): Branch List, Branch Map,
Branch Competitors, and Market Demographics — all on the persisted FDIC SOD
store (data/branches_store), demographics on the Census ACS client.
(Branch Proximity + Merger Planning live in their dedicated modules — see
the note above render_market_demographics.)

Every figure is FDIC SOD as stored (deposits are $thousands in SOD; the store
keeps them as reported — displayed via $-formatters at ×1000) or a transparent
calculation on it (shares, HHI = Σ(share%²) on the DOJ 0-10,000 scale).
A bank with no SOD rows renders an honest empty state, never zeros.
"""
from __future__ import annotations

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


# Branch Proximity + Merger Planning moved to the dedicated modules
# ui/branch_proximity.py + ui/merger_planning.py (merge of two parallel
# 2026-08-20 builds — those ride the shared tested geo/HHI layer in
# data/branches_store + analysis/merger_hhi); this module keeps the four
# leaves above/below.


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
