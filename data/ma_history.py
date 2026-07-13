"""
Completed M&A deal history for one bank — the FDIC leg of the Transactions
section's Detailed M&A History table (docs/SNL-BUILD-PLAN.md §14).

Assembles, per FDIC cert, every COMPLETED structure deal in both directions:

  whole_company / acquisition — institutions absorbed by this cert
      (810/811/812 survivor-side rows via data/fdic_structure)
  whole_company / sale        — this cert's own terminal absorption
      (2xx dying-side row), the endcap for defunct banks
  branch / acquisition        — branch-package purchases (712 header rows on
      this cert; OUT_* = seller; the package's offices are 722 rows here)
  branch / sale               — branch packages this bank sold (reverse query
      OUT_CERT:{cert} AND CHANGECODE:712 — sellers record nothing on their
      own cert; the header + offices live on the buyer's cert)

Each whole-company deal carries TARGET TOTAL ASSETS at the last FDIC REPDTE
on or before the ANNOUNCE date when one resolved (the SNL convention),
else on or before completion — target_assets_repdte says which anchor was
used. FDIC financials survive for dead certs (verified: Columbia State
Bank 33826 still reports after its 2023 death).
Assets are converted $thousands -> raw dollars AT THIS BOUNDARY (the units
contract: downstream *_assets are always raw dollars). Branch deals get no
target assets (branch-level assets aren't in SDI) — n/a, never a guess.
Non-SDI targets (e.g. trust-company affiliates) also render n/a.

Whole-company deals are enriched with the deal's ANNOUNCEMENT — announce
date + deal value (stated in the PR, or computed for ratio-only all-stock
deals: ratio × acquirer prior close × target cover shares, labeled via
value_basis/value_note) from the announcement 8-K via EDGAR full-text
search (data/ma_announcements; strict guards, n/a over guess; EFTS
coverage is 2001+ so older deals honestly carry None). Branch deals are
not enriched (announcement linkage is too noisy to guard — see that
module). TERMINATED deals (announced, never completed — no FDIC anchor)
come from find_terminated_deals when the caller supplies the holdco CIK:
status='terminated' rows with termination + announce dates, counterparty
and value (live-verified: FHN/TD $13.4B stated, announced 2022-02-28,
terminated 2023-05-04). Completed rows carry status='completed'. FDIC
history itself is completions only (EFFDATE).

Cache: ``ma_history:v5:{cert}:{cik or 0}`` for 7 days — structure changes are rare;
the key is versioned so a deal-schema change never serves stale rows. Any
fetch failure — history pages, a target-assets lookup, or an announcement
fetch — skips the cache put so a transient outage is never frozen as a
wrong/empty table; history failure returns [] (never a partial table
missing a whole deal class). First uncached render for a serial acquirer
does one EFTS query + a few document fetches per whole-company deal
(~seconds; Umpqua-scale worst case tens of seconds) — a warm job is the
later lever if that bites.
"""

from __future__ import annotations

from datetime import datetime

from data.fdic_structure import (
    CACHE_TTL_SECONDS,
    STRUCTURE_CODES_FILTER,
    fetch_history_rows,
    parse_event,
    to_cert,
)

_BRANCH_FIELDS = ",".join([
    "CERT", "EFFDATE", "CHANGECODE", "CHANGECODE_DESC",
    "OUT_CERT", "OUT_INSTNAME", "ACQ_CERT", "ACQ_INSTNAME",
    "OFF_NUM", "OFF_NAME",
])
_LOG = "ma_history"


def _is_fresh(cached: dict | None) -> bool:
    from data.freshness import is_fresh
    return is_fresh(cached, CACHE_TTL_SECONDS)


# ── Target assets at completion ───────────────────────────────────────────

def _assets_before(cert: int | None, iso_date: str) -> tuple[int | None, str | None, bool]:
    """
    Total assets at the last FDIC REPDTE on or before ``iso_date`` for a cert
    (works for dead certs — SDI keeps their filings).

    Returns (assets_raw_dollars | None, repdte 'YYYY-MM-DD' | None, ok).
    ok=False means the lookup FAILED (don't cache the assembly); ok=True with
    None assets means the target has no SDI financials (honest n/a — e.g. a
    non-depository trust affiliate).
    """
    if not cert or not iso_date:
        return None, None, True
    from data.fdic_client import FDIC_FINANCIALS_URL
    from data.http import get_with_retry

    repdte_max = iso_date.replace("-", "")[:8]
    try:
        resp = get_with_retry(FDIC_FINANCIALS_URL, {
            "filters": f"CERT:{int(cert)} AND REPDTE:[19000101 TO {repdte_max}]",
            "fields": "CERT,REPDTE,ASSET",
            "sort_by": "REPDTE",
            "sort_order": "DESC",
            "limit": 1,
        }, timeout=30)
        if resp is None:
            print(f"[{_LOG}] assets cert {cert}: retries exhausted (429)")
            return None, None, False
        data = resp.json().get("data", [])
    except Exception as e:
        print(f"[{_LOG}] assets cert {cert} error: {type(e).__name__}: {e}")
        return None, None, False
    if not data:
        return None, None, True
    rec = data[0].get("data", {})
    asset_k = rec.get("ASSET")
    repdte = str(rec.get("REPDTE") or "")
    if asset_k is None or len(repdte) != 8:
        return None, None, True
    # FDIC reports $thousands; downstream *_assets are ALWAYS raw dollars.
    return int(asset_k) * 1000, f"{repdte[:4]}-{repdte[4:6]}-{repdte[6:]}", True


# ── Branch-package extraction (712 headers + 722 office rows) ─────────────

def _branch_purchases(rows: list[dict], own_cert: int) -> list[dict]:
    """
    Branch-package purchases from one cert's 712/722 rows.

    A deal = a 712 header row (OUT_* = seller, no office). The package's
    offices are 722 rows with the same EFFDATE. Attribution of office rows to
    a header is by date: with exactly one header that date the count is exact;
    with several same-date headers the split is unknowable -> count None. An
    office group with NO header (old records lack headers, e.g. Banner's 1992
    Dayton branch) is still a purchase — seller unknown, count from the group.
    A header naming ``own_cert`` itself as seller (phantom self-transfer) is
    dropped, never shown as a deal.
    """
    headers: list[dict] = []
    office_counts: dict[str, int] = {}
    office_meta: dict[str, tuple] = {}  # date -> (code, desc) of first office row
    for d in rows:
        date = str(d.get("EFFDATE") or "")[:10]
        if not date:
            continue
        seller = to_cert(d.get("OUT_CERT"))
        if d.get("CHANGECODE") == 712 and seller is not None:
            if seller == own_cert:
                continue
            headers.append({"date": date, "seller_cert": seller,
                            "seller_name": d.get("OUT_INSTNAME") or "",
                            "desc": d.get("CHANGECODE_DESC") or "",
                            "code": d.get("CHANGECODE")})
        elif d.get("CHANGECODE") in (712, 722):
            office_counts[date] = office_counts.get(date, 0) + 1
            office_meta.setdefault(
                date, (d.get("CHANGECODE"), d.get("CHANGECODE_DESC") or ""))

    by_date: dict[str, int] = {}
    for h in headers:
        by_date[h["date"]] = by_date.get(h["date"], 0) + 1

    deals = []
    for h in headers:
        n = office_counts.get(h["date"])
        deals.append({
            "completion_date": h["date"],
            "deal_kind": "branch",
            "direction": "acquisition",
            "counterparty": {"name": h["seller_name"], "cert": h["seller_cert"]},
            "branch_count": n if (n and by_date[h["date"]] == 1) else None,
            "event_code": h["code"],
            "event_desc": h["desc"],
            "target_assets": None,
            "target_assets_repdte": None,
            "announce_date": None,
            "value_usd": None,
            "value_basis": None,
            "value_note": None,
            "announce_url": None,
        })
    for date, n in office_counts.items():
        if date not in by_date:  # orphan office group — headerless old record
            code, desc = office_meta[date]
            deals.append({
                "completion_date": date,
                "deal_kind": "branch",
                "direction": "acquisition",
                "counterparty": None,
                "branch_count": n,
                "event_code": code,
                "event_desc": desc,
                "target_assets": None,
                "target_assets_repdte": None,
                "announce_date": None,
                "value_usd": None,
                "value_basis": None,
                "value_note": None,
                "announce_url": None,
            })
    return deals


def get_ma_history(cert: int, cik: int | None = None) -> list[dict]:
    """
    All completed structure deals for an FDIC cert, newest-first.

    Returns [{completion_date 'YYYY-MM-DD',
              deal_kind 'whole_company' | 'branch',
              direction 'acquisition' | 'sale',
              counterparty {name, cert} | None,   # whole acq: target;
                                                  # whole sale: survivor;
                                                  # branch acq: seller;
                                                  # branch sale: buyer
              branch_count int | None,            # branch deals only
              event_code int, event_desc str,     # FDIC CHANGECODE verbatim
              target_assets int | None,           # RAW DOLLARS at the last
              target_assets_repdte str | None,    # REPDTE ≤ completion;
                                                  # whole-company deals only
              announce_date str | None,           # announcement 8-K (EFTS);
              value_usd int | None,               # RAW DOLLARS
              value_basis 'stated' | 'computed' | None,
              value_note str | None,              # computed formula verbatim
              announce_url str | None}]           # all four: whole-company
                                                  # deals only, 2001+

    [] on any history-fetch failure (never a partial deal list); a failed
    assets or announcement lookup renders that deal's fields n/a and skips
    the cache put.
    """
    if not cert:
        return []
    cert = int(cert)
    from data import cache

    # v5: announce-anchored target assets (2026-07-13). The key carries the
    # holdco CIK because the terminated leg only runs when one is supplied.
    key = f"ma_history:v5:{cert}:{int(cik) if cik else 0}"
    cached = cache.get(key)
    if _is_fresh(cached) and isinstance(cached.get("deals"), list):
        return cached["deals"]

    structure = fetch_history_rows(f"CERT:{cert} AND {STRUCTURE_CODES_FILTER}",
                                   log_tag=_LOG)
    branch = fetch_history_rows(
        f"CERT:{cert} AND (CHANGECODE:712 OR CHANGECODE:722)",
        fields=_BRANCH_FIELDS, log_tag=_LOG)
    sold = fetch_history_rows(f"OUT_CERT:{cert} AND CHANGECODE:712",
                              fields=_BRANCH_FIELDS, log_tag=_LOG)
    if structure is None or branch is None or sold is None:
        return []

    from data.ma_announcements import resolve_announcement

    deals: list[dict] = []
    cache_ok = True
    subject_name = next(
        (d.get("ACQ_INSTNAME") or d.get("INSTNAME")
         for d in structure
         if to_cert(d.get("ACQ_CERT")) == cert or to_cert(d.get("CERT")) == cert),
        "") or ""

    # Whole-company deals, both directions, from institution-level events.
    # Announcement enrichment (announce date + stated value via EDGAR
    # full-text search) uses the PARTY NAMES FROM THE ROW — the names at
    # deal time, which survive later renamings (South Umpqua Bank ->
    # Umpqua Bank -> Columbia Bank). Branch deals are not enriched (see
    # data/ma_announcements docstring) — honest n/a.
    for d in structure:
        ev = parse_event(cert, d)
        if not ev or not ev["other_institution"]:
            continue
        if ev["direction"] == "acquired":
            target_cert = ev["other_institution"]["cert"]
            direction = "acquisition"
            target_name = d.get("OUT_INSTNAME") or ""
            acquirer_name = d.get("ACQ_INSTNAME") or d.get("SUR_INSTNAME") or ""
        elif ev["direction"] == "was_acquired":
            target_cert = cert  # we are the target; counterparty = survivor
            direction = "sale"
            target_name = d.get("OUT_INSTNAME") or ""
            acquirer_name = d.get("SUR_INSTNAME") or d.get("ACQ_INSTNAME") or ""
        else:
            continue
        # A probe fetch at completion decides announcement eligibility:
        # linkage only for targets that were REAL operating banks (SDI
        # financials exist). Non-SDI targets are affiliate consolidations
        # (trust companies, phantom reorgs) whose "match" would be a
        # main-merger 8-K merely mentioning them — the verified Columbia
        # Trust Company mislinkage. n/a over a plausible-wrong date.
        assets, repdte, ok = _assets_before(target_cert, ev["date"])
        cache_ok = cache_ok and ok
        ann, ann_ok = (None, True)
        if assets is not None and ok:
            ann, ann_ok = resolve_announcement(target_name, acquirer_name,
                                               ev["date"])
            cache_ok = cache_ok and ann_ok
            # Spec: target assets AT ANNOUNCEMENT. Re-anchor at the announce
            # date when one resolved (the completion-anchored probe already
            # proved SDI coverage); deals with no announcement keep the
            # completion anchor — target_assets_repdte says which.
            if (ann or {}).get("announce_date"):
                a_assets, a_repdte, a_ok = _assets_before(
                    target_cert, ann["announce_date"])
                cache_ok = cache_ok and a_ok
                if a_ok and a_assets is not None:
                    assets, repdte = a_assets, a_repdte
        deals.append({
            "completion_date": ev["date"],
            "deal_kind": "whole_company",
            "direction": direction,
            "counterparty": ev["other_institution"],
            "branch_count": None,
            "event_code": ev["event_type"],
            "event_desc": ev["description"],
            "target_assets": assets,
            "target_assets_repdte": repdte,
            "announce_date": (ann or {}).get("announce_date"),
            "value_usd": (ann or {}).get("value_usd"),
            "value_basis": (ann or {}).get("value_basis"),
            "value_note": (ann or {}).get("value_note"),
            "announce_url": (ann or {}).get("url"),
        })

    # Branch-package purchases (712/722 rows on this cert).
    deals.extend(_branch_purchases(branch, cert))

    # Branch-package sales: header + offices live on the BUYER's cert. Run the
    # purchase extraction on each buyer to recover this deal's branch count.
    buyer_cache: dict[int, list[dict] | None] = {}
    for d in sold:
        date = str(d.get("EFFDATE") or "")[:10]
        buyer_cert = to_cert(d.get("ACQ_CERT")) or to_cert(d.get("CERT"))
        if not date or to_cert(d.get("OUT_CERT")) != cert or buyer_cert is None:
            continue
        if buyer_cert not in buyer_cache:
            buyer_cache[buyer_cert] = fetch_history_rows(
                f"CERT:{buyer_cert} AND (CHANGECODE:712 OR CHANGECODE:722)",
                fields=_BRANCH_FIELDS, log_tag=_LOG)
        buyer_rows = buyer_cache[buyer_cert]
        count = None
        if buyer_rows is None:
            cache_ok = False  # count unknown due to fetch failure — don't cache
        else:
            count = next((p["branch_count"]
                          for p in _branch_purchases(buyer_rows, buyer_cert)
                          if p["completion_date"] == date
                          and (p["counterparty"] or {}).get("cert") == cert), None)
        deals.append({
            "completion_date": date,
            "deal_kind": "branch",
            "direction": "sale",
            "counterparty": {"name": d.get("ACQ_INSTNAME") or "",
                             "cert": buyer_cert},
            "branch_count": count,
            "event_code": d.get("CHANGECODE"),
            "event_desc": d.get("CHANGECODE_DESC") or "",
            "target_assets": None,
            "target_assets_repdte": None,
            "announce_date": None,
            "value_usd": None,
            "value_basis": None,
            "value_note": None,
            "announce_url": None,
        })

    for d in deals:
        d["status"] = "completed"
        d["termination_date"] = None

    # Terminated / withdrawn deals (EFTS sweep) — only with a holdco CIK.
    if cik:
        from data.ma_announcements import find_terminated_deals
        terminated, t_ok = find_terminated_deals(cik, subject_name)
        cache_ok = cache_ok and t_ok
        for t in terminated:
            deals.append({
                "completion_date": None,
                "deal_kind": "whole_company",
                "direction": t["direction"],
                "counterparty": {"name": t["counterparty_name"], "cert": None},
                "branch_count": None,
                "event_code": None,
                "event_desc": "Merger agreement terminated",
                "target_assets": None,
                "target_assets_repdte": None,
                "announce_date": t["announce_date"],
                "value_usd": t["value_usd"],
                "value_basis": t["value_basis"],
                "value_note": t["value_note"],
                "announce_url": t["announce_url"],
                "status": "terminated",
                "termination_date": t["termination_date"],
            })

    deals.sort(key=lambda x: x["completion_date"] or x["termination_date"] or "",
               reverse=True)
    if cache_ok:
        cache.put(key, {"deals": deals,
                        "cached_at": datetime.now().isoformat()})
    return deals


if __name__ == "__main__":
    # LIVE smoke — hand-verified ground truth (2026-07-13 probes):
    #   Umpqua/Columbia cert 17266: whole-company acquisition of Columbia
    #   State Bank (33826) completed 2023-03-01, target assets $18,597,100
    #   thousand at 2021-09-30 (announce-anchored); Pacific Premier 2025-09-01;
    #   a branch SALE of six Oregon branches to Banner Bank 2014-06-20.
    #   Banner cert 28489: the same deal as a branch PURCHASE, count 6.
    #   Announcements: Columbia announced 2021-10-12 (all-stock MOE, no
    #   stated value); Banner/Skagit announced 2018-07-26 at $191.1M stated.
    deals = get_ma_history(17266)
    print(f"Umpqua/Columbia (17266): {len(deals)} deals")
    for x in deals:
        cp = x["counterparty"] or {}
        print(f"  {x['completion_date']}  {x['deal_kind']:<13} {x['direction']:<11}"
              f" {cp.get('name', '—'):<32} branches={x['branch_count']}"
              f" assets={x['target_assets']} ann={x['announce_date']}"
              f" value={x['value_usd']}")

    col = next(x for x in deals
               if (x["counterparty"] or {}).get("cert") == 33826)
    assert col["deal_kind"] == "whole_company" and col["direction"] == "acquisition"
    assert col["completion_date"] == "2023-03-01", col
    # Announcement-anchored (2021-09-30 ≤ announce 2021-10-12), hand-verified.
    assert col["target_assets"] == 18_597_100_000, col
    assert col["target_assets_repdte"] == "2021-09-30", col
    assert col["announce_date"] == "2021-10-12", col
    # All-stock MOE: computed 0.5958 x COLB $39.57 (2021-10-11) x 220,133,236
    # UMPQ shares ~= $5.19B vs press-reported ~$5.2B. Range-asserted (FMP
    # could restate a close); the exact math is pinned in unit tests.
    assert col["value_basis"] == "computed", col
    assert 5_000_000_000 < col["value_usd"] < 5_400_000_000, col
    assert "0.5958" in (col["value_note"] or ""), col
    ppb = next(x for x in deals
               if (x["counterparty"] or {}).get("cert") == 32172)
    assert ppb["completion_date"] == "2025-09-01", ppb
    # PR sentence hand-verified 2026-07-13: "The merger is valued at
    # approximately $2.0 billion, or $20.83 per Pacific Premier share".
    assert ppb["announce_date"] == "2025-04-23", ppb
    assert ppb["value_usd"] == 2_000_000_000 and ppb["value_basis"] == "stated", ppb
    # Non-SDI affiliate consolidation (trust company): never linked to an
    # announcement — the guard against the verified 2023-01-10 mislinkage.
    trust = next(x for x in deals
                 if (x["counterparty"] or {}).get("cert") == 34227)
    assert trust["announce_date"] is None and trust["target_assets"] is None, trust
    sale = next(x for x in deals if x["direction"] == "sale"
                and (x["counterparty"] or {}).get("cert") == 28489)
    assert sale["deal_kind"] == "branch"
    assert sale["completion_date"] == "2014-06-20" and sale["branch_count"] == 6, sale

    banner = get_ma_history(28489)
    print(f"\nBanner (28489): {len(banner)} deals")
    for x in banner:
        cp = x["counterparty"] or {}
        print(f"  {x['completion_date']}  {x['deal_kind']:<13} {x['direction']:<11}"
              f" {cp.get('name', '—'):<32} branches={x['branch_count']}"
              f" assets={x['target_assets']} ann={x['announce_date']}"
              f" value={x['value_usd']}")
    buy = next(x for x in banner
               if x["deal_kind"] == "branch" and x["direction"] == "acquisition"
               and (x["counterparty"] or {}).get("cert") == 17266)
    assert buy["completion_date"] == "2014-06-20" and buy["branch_count"] == 6, buy
    skagit = next(x for x in banner
                  if (x["counterparty"] or {}).get("cert") == 17874)
    assert skagit["announce_date"] == "2018-07-26", skagit
    assert skagit["value_usd"] == 191_100_000, skagit
    assert skagit["value_basis"] == "stated", skagit
    print("\nSMOKE OK: Umpqua/Columbia + Banner deals verified against probes.")
