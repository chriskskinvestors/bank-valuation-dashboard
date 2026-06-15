"""Validate the SEC-filing holdco capital scraper across many banks.

For a size-diverse sample, scrape each bank's latest 10-K, extract holdco
regulatory capital, and CROSS-CHECK the scraped holdco CET1 ratio against the
independent FDIC bank-subsidiary CET1 ratio (IDT1CER) — holdco and bank capital
track closely, so a large gap or a miss flags a problem. Reports coverage by
size tier, the holdco-selection confidence breakdown (default/parent = clean,
fuzzy = name-based parent guess), and, for misses, the capital concepts the
filing DID carry (to widen the matcher). Run:  python tools/validate_capital_scrape.py [N]
"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.sec_filing_scraper import fetch_facts, extract_holdco_capital, _CAP_LINE_PATTERNS, _CAP_THRESHOLD

UA = {"User-Agent": "KSK Investors research chris@kskinvestors.com"}


def _fdic(cert):
    """(asset_$, cet1_ratio_%) for an FDIC cert, latest period, or (None, None)."""
    url = (f"https://banks.data.fdic.gov/api/financials?filters=CERT:{cert}"
           f"&fields=ASSET,IDT1CER&sort_by=REPDTE&sort_order=DESC&limit=1&format=json")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
            d = json.load(r)["data"]
        if d:
            rec = d[0]["data"]
            return rec.get("ASSET"), rec.get("IDT1CER")
    except Exception:
        pass
    return None, None


def _tier(asset_k):
    a = (asset_k or 0) * 1000          # FDIC ASSET is $thousands
    if a >= 250e9: return "mega >250B"
    if a >= 50e9:  return "large 50-250B"
    if a >= 10e9:  return "mid 10-50B"
    if a >= 1e9:   return "small 1-10B"
    return "micro <1B"


def main(n=100):
    banks = json.load(open("data/bank_map_resolved.json"))
    rows = [(t, v) for t, v in banks.items() if v.get("cik") and v.get("fdic_cert")]
    print(f"Sizing {len(rows)} mapped banks via FDIC…", flush=True)
    sized = []
    for t, v in rows:
        asset, cet1 = _fdic(v["fdic_cert"])
        if asset:
            sized.append((t, v, asset, cet1, _tier(asset)))
        time.sleep(0.03)
    # sample evenly across tiers
    by_tier = {}
    for r in sized:
        by_tier.setdefault(r[4], []).append(r)
    tiers = ["mega >250B", "large 50-250B", "mid 10-50B", "small 1-10B", "micro <1B"]
    per = max(1, n // len(tiers))
    sample = []
    for tr in tiers:
        sample += by_tier.get(tr, [])[:per]
    sample = sample[:n]
    print(f"Sampled {len(sample)} banks across tiers: "
          f"{ {tr: min(len(by_tier.get(tr, [])), per) for tr in tiers} }\n", flush=True)

    hdr = f"{'TICKER':7}{'TIER':14}{'HOLDCO CET1':12}{'FDIC CET1':11}{'CONF':9}{'LINES':6}STATUS"
    print(hdr); print("-" * len(hdr), flush=True)
    stats = {"covered": 0, "consistent": 0, "fuzzy": 0, "cblr": 0, "miss": 0, "err": 0}
    misses = []
    for t, v, asset, fdic_cet1, tier in sample:
        try:
            meta, facts = fetch_facts(v["cik"], forms=("10-K",))
            time.sleep(0.2)
            cap = extract_holdco_capital(facts, anchor_cet1=fdic_cet1) if facts else {}
            latest = max(cap) if cap else None
            d = cap.get(latest, {}) if latest else {}
            hc = d.get("cet1_ratio")
            conf = d.get("_confidence", "—")
            nlines = len([k for k in d if not k.startswith("_")])
            if hc is None:
                # CBLR election: leverage ratio only, CET1 legitimately n/a.
                if d.get("_cblr") and d.get("lev_ratio"):
                    stats["cblr"] += 1
                    print(f"{t:7}{tier:14}{'CBLR':12}{'lev ' + format(d['lev_ratio']*100, '.2f') + '%':11}{conf:9}{nlines:<6}CBLR-ok", flush=True)
                    continue
                stats["miss"] += 1
                concepts = sorted({f.concept.split(":")[-1] for f in facts
                                   if "apital" in f.concept and not _CAP_THRESHOLD.search(f.concept)})[:6]
                misses.append((t, concepts))
                print(f"{t:7}{tier:14}{'—':12}{(f'{fdic_cet1:.2f}%' if fdic_cet1 else '—'):11}{'—':9}{0:<6}MISS", flush=True)
                continue
            stats["covered"] += 1
            if conf == "fuzzy":
                stats["fuzzy"] += 1
            hc_pct = hc * 100
            basis = d.get("_basis", "holdco")
            ok = fdic_cet1 and abs(hc_pct - fdic_cet1) <= 5.0 and 3 <= hc_pct <= 60
            status = ("ok" if ok else ("REVIEW" if fdic_cet1 else "no-fdic")) + (" [bank]" if basis == "bank" else "")
            if ok:
                stats["consistent"] += 1
            print(f"{t:7}{tier:14}{hc_pct:<12.2f}{(f'{fdic_cet1:.2f}' if fdic_cet1 else '—'):11}{conf:9}{nlines:<6}{status}", flush=True)
        except Exception as e:
            stats["err"] += 1
            print(f"{t:7}{tier:14}{'ERROR':12}{type(e).__name__}: {e}", flush=True)

    tot = len(sample)
    good = stats["covered"] + stats["cblr"]   # a correct outcome (value or legit n/a)
    print(f"\n=== SUMMARY ({tot} banks) ===")
    print(f"  holdco CET1 extracted : {stats['covered']}/{tot} ({100*stats['covered']//tot}%)")
    print(f"  CBLR (lev-only, OK)   : {stats['cblr']}")
    print(f"  EFFECTIVE coverage    : {good}/{tot} ({100*good//tot}%)  [value or correct n/a]")
    print(f"  consistent w/ FDIC    : {stats['consistent']}/{stats['covered'] or 1} of extracted")
    print(f"  fuzzy (name-guessed)  : {stats['fuzzy']}")
    print(f"  true miss             : {stats['miss']}   errors: {stats['err']}")
    if misses:
        print("\n  MISSES — capital concepts present (to widen the matcher):")
        for t, cs in misses[:25]:
            print(f"    {t:7} {cs}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 100)
