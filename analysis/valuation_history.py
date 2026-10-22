"""The daily P/TBV & P/E series behind the Corporate Profile valuation card.

Pure data layer extracted from ui.bank_detail.valuation_series (2026-10-22)
so the card (_valuation_history_chart), the Valuation Model's long-run
context and the nightly agent export (data/export/tables_valuation) build
the SAME frame from the same code. Callers load the inputs — the chart live
through its usual seams, the export from persisted caches only — and pass
them in; nothing here fetches.
"""
from __future__ import annotations

import pandas as pd


def build_valuation_series(ends_all: list, ps: dict, facts: dict | None,
                           px: pd.DataFrame | None) -> pd.DataFrame | None:
    """One row per price bar in `px`: close, the most recently FILED tangible
    book value per share and TTM diluted EPS, and the P/TBV / P/E they imply.

    ends_all: FDIC quarter-end datetimes, ascending (the per-share anchors).
    ps:       ui.financial_highlights._per_share_for_ends(cik, ends_all) output.
    facts:    the companyfacts blob (TTM EPS is summed from it).
    px:       price history frame with `date` and `close`.

    Fundamentals step in on their 10-Q / 10-K filing date (no lookahead);
    price moves daily. None when there are fewer than four quarter-ends, no
    price history, or no positive book value anywhere.

    Besides the plotted columns each row carries the provenance of the
    fundamentals it was divided by: period_end (the quarter-end anchor),
    eff_date / eff_basis (the date they stepped in: "filed" = the 10-Q/10-K
    filing date, "period_end" = no filing date known, the anchor itself),
    bs_date (the balance-sheet date the TBV was built at), adj_basis
    (intangibles deduction basis), sh_src (share-count source), form / accn
    (the filing that reported the equity)."""
    from ui.financial_highlights import _flow_for, _sec_map
    if len(ends_all) < 4:
        return None
    if px is None or px.empty or "close" not in px.columns:
        return None
    px = px.dropna(subset=["close"]).copy()
    px["date"] = pd.to_datetime(px["date"], errors="coerce")
    px = px.dropna(subset=["date"]).sort_values("date")

    # Trailing-twelve-month diluted EPS at each quarter-end = sum of the four
    # single-quarter EPS ending there (Q4 derived as FY − 9-mo). TTM is what makes
    # P/E meaningful between reports; its latest value ties to the snapshot's
    # "EPS (TTM)".
    eps_q = _sec_map(facts, "EarningsPerShareDiluted", instant=False, span="quarter") if facts else {}
    eps_a = _sec_map(facts, "EarningsPerShareDiluted", instant=False, span="annual") if facts else {}
    sq = [_flow_for(e, eps_q, eps_a, True)[0] for e in ends_all]
    ttm = {}
    for i, e in enumerate(ends_all):
        w = sq[i - 3:i + 1]
        ttm[e] = sum(w) if (i >= 3 and len(w) == 4 and None not in w) else None

    # Each quarter's book value / TTM EPS steps in on the date its 10-Q/10-K was
    # actually FILED (not the period-end), so a given day's multiple only uses
    # fundamentals the market already had — no lookahead. The filing is the
    # one that reported the equity TBVPS was built on (either equity tag).
    frows = []
    for e in ends_all:
        rec = ps.get(e) or {}
        tbvps, eps_ttm = rec.get("tbvps"), ttm.get(e)
        if tbvps is None and eps_ttm is None:
            continue
        prov = rec.get("_eq_prov")
        eff = (pd.to_datetime(prov["filed"], errors="coerce")
               if (prov and prov.get("filed")) else pd.Timestamp(e))
        basis = "filed"
        if pd.isna(eff) or not (prov and prov.get("filed")):
            eff, basis = pd.Timestamp(e), "period_end"
        frows.append({"date": eff, "tbvps": tbvps, "ttm_eps": eps_ttm,
                      "period_end": pd.Timestamp(e), "eff_date": eff,
                      "eff_basis": basis, "bs_date": rec.get("_eq_date"),
                      "adj_basis": rec.get("_adj_basis"), "sh_src": rec.get("_sh_src"),
                      "form": (prov or {}).get("form"), "accn": (prov or {}).get("accn")})
    if not frows:
        return None
    # The price history is split-adjusted by the vendor; a split declared
    # after the latest balance sheet (facts["_stock_split"], SFBS 2:1
    # 2026-08-21) leaves every FILED per-share figure pre-split — ÷ ratio so
    # the history's P/TBV and P/E sit on the quote's basis.
    split = facts.get("_stock_split") if facts else None
    if split and split.get("ratio"):
        for fr in frows:
            for k in ("tbvps", "ttm_eps"):
                if fr[k] is not None:
                    fr[k] = fr[k] / split["ratio"]
    fund = (pd.DataFrame(frows).dropna(subset=["date"]).sort_values("date")
            .drop_duplicates("date", keep="last"))

    # Daily valuation over the selected window: each trading day ÷ the most
    # recently filed fundamentals (merge_asof backward). The window is whatever
    # `period` returned; fundamentals reach back further only to seed the first
    # day's lookup (so even 1W/1M start with the right book value / EPS).
    px = px[px["date"] >= fund["date"].iloc[0]]
    if px.empty:
        return None
    val = pd.merge_asof(px, fund, on="date", direction="backward").copy()
    val["ptbv"] = (val["close"] / val["tbvps"]).where(val["tbvps"] > 0)
    val["pe"] = (val["close"] / val["ttm_eps"]).where(val["ttm_eps"] > 0)
    if val.empty or val["ptbv"].isna().all():
        return None
    return val
