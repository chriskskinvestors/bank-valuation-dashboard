"""
Comparable Deal Analysis engine (docs/SNL-BUILD-PLAN.md §14) — COMPUTED
bank-M&A deal comps across the universe: announced deal values ÷ target
financials at announcement.

Multiples per priced whole-company deal:
  P/TBV paid        value ÷ target tangible common equity at the last
                    reported period ≤ announce
  Price / assets    value ÷ target total assets at the same anchor
  Core deposit      (value − TBV) ÷ core deposits — the classic bank-deal
  premium           premium; FDIC bank-sub basis only (see below)

TBV BASIS (owner-decided 2026-07-13): SEC holdco when the deal's priced
entity resolves to a CIK, FDIC bank-sub otherwise — every multiple labeled.
The priced entity is the RATIO's target side (deal row ``target_cik``), NOT
the FDIC counterparty: in an MOE the bank-level survivor can be the
opposite side of the holdco-level target (Columbia/Umpqua, live-verified —
pairing the $5.19B value with Columbia State Bank's TBV would be
plausible-wrong). On the holdco basis, price/assets uses HOLDCO assets and
the core-deposit premium is n/a (bank-sub core deposits belong to the
other side in a flipped deal). On the bank-sub basis (private targets —
cash deals, no flip in practice) FDIC EQ−INTAN / COREDEP / ASSET at the
last REPDTE ≤ announce are used. A residual flip risk exists only for
stated-value stock deals whose PR quotes no ratio — a P/TBV sanity band
(0.2×–8×) flags and n/a's any such mismatch rather than displaying it.

The universe snapshot is compiled by jobs/refresh_deal_comps (nightly walk
that also warms every bank's ma_history cache) and stored under ONE cache
key; the UI only ever reads the snapshot. Deals are deduped across banks
by announcement accession, else (counterparty cert, completion date).

Pending (announced) deals flow through from ma_history's two pending legs
(Rule 425 episodes for stock deals; the open-announcement 8-K sweep for
cash deals, which file no 425) — status='pending' rows with full multiples
where the target's financials resolve (private cash targets price off the
FDIC bank-sub basis when their cert links).
"""

from __future__ import annotations

from datetime import datetime

SNAPSHOT_KEY = "deal_comps_snapshot:v1"
_PTBV_SANE = (0.2, 8.0)         # outside → basis-mismatch guard, n/a + flag
_MAX_TBV_AGE_DAYS = 200


def _fdic_at(cert, asof_iso: str):
    """(tbv, core_deposits, assets, repdte_iso, ok) — FDIC bank-sub tangible
    equity (EQ − INTAN), core deposits and assets at the last REPDTE ≤ asof,
    all raw dollars. ok=False on fetch failure."""
    if not cert or not asof_iso:
        return None, None, None, None, True
    from data.fdic_client import FDIC_FINANCIALS_URL
    from data.http import get_with_retry

    try:
        resp = get_with_retry(FDIC_FINANCIALS_URL, {
            "filters": (f"CERT:{int(cert)} AND "
                        f"REPDTE:[19000101 TO {asof_iso.replace('-', '')[:8]}]"),
            "fields": "CERT,REPDTE,EQ,INTAN,COREDEP,ASSET",
            "sort_by": "REPDTE", "sort_order": "DESC", "limit": 1,
        }, timeout=30)
        if resp is None:
            return None, None, None, None, False
        data = resp.json().get("data", [])
    except Exception as e:
        print(f"[deal_comps] fdic cert {cert}: {type(e).__name__}: {e}")
        return None, None, None, None, False
    if not data:
        return None, None, None, None, True
    rec = data[0].get("data", {})
    repdte = str(rec.get("REPDTE") or "")
    if len(repdte) != 8:
        return None, None, None, None, True
    eq, intan = rec.get("EQ"), rec.get("INTAN")
    tbv = (eq - (intan or 0)) * 1000 if eq is not None else None
    core = rec.get("COREDEP")
    assets = rec.get("ASSET")
    return (tbv,
            core * 1000 if core is not None else None,
            assets * 1000 if assets is not None else None,
            f"{repdte[:4]}-{repdte[4:6]}-{repdte[6:]}", True)


def _sec_assets_at(cik: int, asof_iso: str):
    """Holdco total assets at the last reported end ≤ asof (raw dollars)."""
    import pandas as pd
    from data.sec_client import get_historical_fundamentals

    try:
        df = get_historical_fundamentals(int(cik), "Assets")
        if df is None or df.empty:
            return None, None
        df = df.dropna(subset=["val"])
        df["end_ts"] = df["end"].map(lambda e: pd.Timestamp(e).normalize())
        asof = pd.Timestamp(asof_iso).normalize()
        elig = df[df["end_ts"] <= asof]
        if elig.empty:
            return None, None
        row = elig.loc[elig["end_ts"].idxmax()]
        if (asof - row["end_ts"]).days > _MAX_TBV_AGE_DAYS:
            return None, None
        return float(row["val"]), row["end_ts"].date().isoformat()
    except Exception as e:
        print(f"[deal_comps] sec assets cik {cik}: {type(e).__name__}: {e}")
        return None, None


def compute_multiples(deal: dict) -> tuple[dict, bool]:
    """Comps fields for one priced whole-company deal row (see module doc).
    Returns (fields, ok) — ok=False on a lookup failure (skip snapshot
    caching of this build)."""
    out = {"tbv_usd": None, "tbv_basis": None, "tbv_asof": None,
           "p_tbv": None, "price_assets": None, "core_dep_premium": None,
           "comp_assets": None, "flagged": None}
    value = deal.get("value_usd")
    anchor = deal.get("announce_date") or deal.get("completion_date") \
        or deal.get("termination_date")
    if not value or not anchor or deal.get("deal_kind") != "whole_company":
        return out, True

    ok = True
    tgt_cik = deal.get("target_cik")
    if tgt_cik:
        from data.sec_per_share import tangible_common_equity_at
        tce, tce_end = tangible_common_equity_at(int(tgt_cik), anchor,
                                                 max_age_days=_MAX_TBV_AGE_DAYS)
        if tce:
            out.update(tbv_usd=tce, tbv_basis="holdco", tbv_asof=tce_end)
            assets, _aend = _sec_assets_at(int(tgt_cik), anchor)
            out["comp_assets"] = assets
    if out["tbv_usd"] is None:
        cert = (deal.get("counterparty") or {}).get("cert")
        tbv, core, assets, repdte, f_ok = _fdic_at(cert, anchor)
        ok = ok and f_ok
        if tbv and tbv > 0:
            out.update(tbv_usd=tbv, tbv_basis="bank-sub", tbv_asof=repdte,
                       comp_assets=assets)
            if core and assets and core > 0.10 * assets:
                out["core_dep_premium"] = (value - tbv) / core

    if out["tbv_usd"]:
        p = value / out["tbv_usd"]
        if _PTBV_SANE[0] <= p <= _PTBV_SANE[1]:
            out["p_tbv"] = p
        else:
            # Outside any real bank-deal range — a basis mismatch (flipped
            # stated-value stock deal) or bad denominator. n/a + flag.
            out.update(p_tbv=None, core_dep_premium=None,
                       flagged=f"P/TBV {p:.2f}x outside sanity band")
    if out["comp_assets"]:
        out["price_assets"] = value / out["comp_assets"]
    return out, ok


def _dedupe_key(deal: dict, buyer_cert) -> tuple:
    acc = None
    url = deal.get("announce_url") or ""
    if url:
        acc = url.rsplit("/", 2)[-2] if "/" in url else url
    if acc:
        return ("acc", acc)
    cp = (deal.get("counterparty") or {}).get("cert")
    return ("deal", cp, deal.get("completion_date"),
            deal.get("termination_date"), buyer_cert)


def build_comps_snapshot(banks: list[dict]) -> dict | None:
    """
    Compile the universe deal-comps snapshot from each bank's (cached)
    ma_history. ``banks``: [{ticker, cert, cik}]. Returns the snapshot dict
    (also cached under SNAPSHOT_KEY) or None when lookups failed badly
    enough that caching would freeze wrong data.
    """
    from data import cache
    from data.ma_history import get_ma_history

    rows, seen = [], set()
    lookups_ok = True
    covered = 0
    for b in banks:
        cert, cik = b.get("cert"), b.get("cik")
        if not cert:
            continue
        deals = get_ma_history(cert, cik=cik, name=b.get("name"))
        if not deals:
            continue
        covered += 1
        for d in deals:
            if d.get("deal_kind") != "whole_company":
                continue
            if d.get("direction") == "sale" and d.get("status") in (
                    "completed", "pending"):
                # The acquirer's row carries the deal — a pending deal
                # otherwise appears under BOTH filers' 425 episodes.
                continue
            k = _dedupe_key(d, cert)
            if k in seen:
                continue
            seen.add(k)
            mult, m_ok = compute_multiples(d)
            lookups_ok = lookups_ok and m_ok
            rows.append({
                "buyer_ticker": b.get("ticker"),
                "buyer_name": b.get("name") or b.get("ticker"),
                "buyer_cert": cert,
                "target_name": (d.get("counterparty") or {}).get("name"),
                "target_cert": (d.get("counterparty") or {}).get("cert"),
                "status": d.get("status"),
                "announce_date": d.get("announce_date"),
                "completion_date": d.get("completion_date"),
                "termination_date": d.get("termination_date"),
                "value_usd": d.get("value_usd"),
                "value_basis": d.get("value_basis"),
                "value_note": d.get("value_note"),
                "announce_url": d.get("announce_url"),
                "target_assets": d.get("target_assets"),
                "target_assets_repdte": d.get("target_assets_repdte"),
                **mult,
            })
    if not lookups_ok:
        print("[deal_comps] lookups failed during build — snapshot NOT cached")
        return None

    rows.sort(key=lambda r: (r.get("announce_date")
                             or r.get("completion_date")
                             or r.get("termination_date") or ""),
              reverse=True)
    snapshot = {
        "built_at": datetime.now().isoformat(),
        "banks_covered": covered,
        "deals_total": len(rows),
        "deals_priced": sum(1 for r in rows if r.get("p_tbv")),
        "deals": rows,
    }
    cache.put(SNAPSHOT_KEY, snapshot)
    return snapshot


def get_comps_snapshot() -> dict | None:
    """The compiled universe snapshot (UI read path — never builds). The
    nightly job refreshes it; staleness is shown via built_at, not hidden."""
    from data import cache
    snap = cache.get(SNAPSHOT_KEY, max_age_s=None)
    if isinstance(snap, dict) and isinstance(snap.get("deals"), list):
        return snap
    return None


if __name__ == "__main__":
    # LIVE mini-snapshot from locally-cached banks. Ground truths:
    #   Banner/Skagit: $191.1M stated; Banner's OWN investor deck stated
    #     "Price / tangible book value per share of 237%" — bank-sub basis
    #     lands near 2.4x (holdco TBV slightly differs; band-checked).
    #   Columbia/Umpqua: $5,189,818,466 computed, priced entity = UMPQ
    #     holdco (target_cik 1077771) — P/TBV must use UMPQ TCE (~2x),
    #     NEVER Columbia State Bank's bank-sub TBV (~3.4x plausible-wrong).
    from data.bank_mapping import get_cik, get_fdic_cert
    banks = [{"ticker": t, "name": t, "cert": get_fdic_cert(t),
              "cik": get_cik(t)} for t in ("BANR", "COLB", "FHN")]
    snap = build_comps_snapshot(banks)
    assert snap, "snapshot build failed"
    print(f"{snap['deals_total']} deals, {snap['deals_priced']} priced, "
          f"{snap['banks_covered']} banks")
    for r in snap["deals"]:
        if r.get("p_tbv") or r.get("value_usd"):
            print(f"  {r['announce_date'] or '—'}  {r['buyer_ticker']:<5}"
                  f" -> {(r['target_name'] or '')[:34]:<34}"
                  f" val={r['value_usd']} basis={r['tbv_basis']}"
                  f" P/TBV={r['p_tbv'] and round(r['p_tbv'], 2)}"
                  f" P/A={r['price_assets'] and round(r['price_assets'], 3)}"
                  f" CDP={r['core_dep_premium'] and round(r['core_dep_premium'], 3)}"
                  f" {r['flagged'] or ''}")

    skagit = next(r for r in snap["deals"]
                  if r.get("target_cert") == 17874)
    assert skagit["tbv_basis"] == "bank-sub", skagit
    assert skagit["p_tbv"] and 2.0 < skagit["p_tbv"] < 2.8, skagit
    colb = next(r for r in snap["deals"]
                if r.get("value_usd") == 5_189_818_466)
    assert colb["tbv_basis"] == "holdco", colb
    assert colb["p_tbv"] and 1.6 < colb["p_tbv"] < 2.4, colb
    print("SMOKE OK")
