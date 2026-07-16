"""Sweep: admit OTC-traded non-SEC-filer banks into the universe (PBAM class).

Owner-approved 2026-07-16: every bank with an active FDIC cert and an FMP
price enters the universe. The hazard is the WRONG-TICKER JOIN (a bank
priced with another company's quote), so admission is exact-match only:

  1. FDIC ACTIVE institutions (one request) minus certs already covered.
  2. FMP's full symbol list (one request), US-listed common symbols.
  3. Join on EXACT normalized legal name — FMP's listing name vs the FDIC
     institution NAME or its NAMEHCR holholding-company name. Suffix-only
     differences (Inc/Corp/Co/…) are normalized away; anything less than
     full-phrase equality goes to the REVIEW file, never auto-admitted.
  4. Tickers present in SEC's company_tickers.json are EXCLUDED — those are
     the SEC discovery path's territory (their absence from the universe is
     a deliberate filter, not this sweep's gap to fill).
  5. Auto-candidates must show a real EOD price (batch check) before entry.

Run:  python -m tools.sweep_otc_banks            # report only
      python -m tools.sweep_otc_banks --apply    # + write map entries

--apply appends {cik: null, fdic_cert, fdic_score: 1.0, name} entries to
data/bank_map_resolved.json (surgical string insert, preserving the file's
format — never json.dump the whole file). The nightly refresh-universe
rebuild then admits them; the deploy coverage gate verifies every cert.
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_MAP_PATH = Path(__file__).parent.parent / "data" / "bank_map_resolved.json"
_OUT_DIR = Path(__file__).parent.parent / "tests"

# Corporate-form tokens that differ between a listing name and a legal name
# without changing identity. DISTINCTIVE words (Bancorp, Bancshares,
# Holdings, Financial, …) are deliberately NOT here — dropping them would
# collapse different institutions onto each other.
_SUFFIX = {"INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY",
           "LTD", "LIMITED", "PLC", "SA", "NA", "N.A.", "INC.", "CORP.",
           "CO.", "THE"}


def _norm(name: str) -> str:
    """Uppercase, punctuation-free, suffix-stripped phrase for EXACT compare."""
    toks = re.sub(r"[^A-Z0-9& ]+", " ", (name or "").upper()).split()
    while toks and toks[0] == "THE":
        toks.pop(0)
    while toks and toks[-1] in _SUFFIX:
        toks.pop()
    return " ".join(toks)


def _fdic_active() -> list[dict]:
    from data.http import get_with_retry
    resp = get_with_retry(
        "https://banks.data.fdic.gov/api/institutions",
        params={"filters": "ACTIVE:1",
                "fields": "NAME,CERT,NAMEHCR,ASSET,STALP,BKCLASS",
                "limit": 10000, "format": "json"},
        headers={"User-Agent": "BankValuationDashboard research@kskinvestors.com"},
        timeout=60)
    return [r["data"] for r in resp.json().get("data", [])]


def _fmp_profile_state(ticker: str) -> str | None:
    """The listing's registered state per FMP's profile — the SECOND
    independent key for admission (exact name alone joined the Philippines'
    Security Bank to an Oklahoma community bank in the first pass)."""
    from data.fmp_client import FMP_BASE, _api_key
    from data.http import get_with_retry
    try:
        resp = get_with_retry(f"{FMP_BASE}/profile",
                              params={"symbol": ticker, "apikey": _api_key()},
                              timeout=30)
        data = resp.json() if resp is not None else None
        row = data[0] if isinstance(data, list) and data else (
            data if isinstance(data, dict) else {})
        return (row.get("state") or "").strip().upper() or None
    except Exception:
        return None


def _fmp_symbols() -> list[dict]:
    from data.fmp_client import FMP_BASE, _api_key
    from data.http import get_with_retry
    resp = get_with_retry(f"{FMP_BASE}/stock-list",
                          params={"apikey": _api_key()}, timeout=120)
    return resp.json() if resp is not None else []


def _covered_certs_and_tickers() -> tuple[set, set]:
    from data.bank_mapping import BANK_MAP
    from data.bank_universe import get_universe
    certs, tickers = set(), set()
    resolved = json.loads(_MAP_PATH.read_text(encoding="utf-8"))
    for src in (BANK_MAP, resolved):
        for t, info in src.items():
            tickers.add(t.upper())
            c = (info or {}).get("fdic_cert")
            if c:
                certs.add(int(c))
    try:
        for t, info in (get_universe() or {}).items():
            tickers.add(str(t).upper())
            c = (info or {}).get("fdic_cert")
            if c:
                certs.add(int(c))
    except Exception as e:
        print(f"[sweep] universe snapshot unavailable ({e}) — map-only cover set")
    return certs, tickers


def _sec_tickers() -> set:
    from data.sec_filing_scraper import _get
    data = json.loads(_get("https://www.sec.gov/files/company_tickers.json"))
    return {v.get("ticker", "").upper() for v in data.values()}


def sweep(apply: bool = False) -> dict:
    fdic = _fdic_active()
    covered_certs, covered_tickers = _covered_certs_and_tickers()
    sec_tickers = _sec_tickers()
    symbols = _fmp_symbols()
    print(f"[sweep] FDIC active: {len(fdic)} | covered certs: "
          f"{len(covered_certs)} | FMP symbols: {len(symbols)}")

    # Index FMP symbols by normalized name. Plain-alpha tickers ≤5 chars only
    # (preferred-series/warrant suffixes never qualify); 5-letter F/Y endings
    # are foreign ordinaries/ADRs (the Philippines' "Security Bank" joined an
    # Oklahoma bank in the first pass) — never candidates. Collisions (two
    # symbols normalizing to one name) are share classes → review, not auto.
    by_name: dict[str, list[dict]] = {}
    for s in symbols:
        sym = (s.get("symbol") or "").upper()
        if not (sym.isalpha() and 2 <= len(sym) <= 5):
            continue
        if len(sym) == 5 and sym[-1] in ("F", "Y"):
            continue
        n = _norm(s.get("companyName") or s.get("name") or "")
        if n:
            by_name.setdefault(n, []).append(s)

    auto, review = [], []
    for bank in fdic:
        cert = int(bank.get("CERT") or 0)
        if not cert or cert in covered_certs:
            continue
        if (bank.get("BKCLASS") or "").upper() == "OI":
            continue        # US branch of a foreign bank — out of scope
        names = {("NAME", _norm(bank.get("NAME") or "")),
                 ("NAMEHCR", _norm(bank.get("NAMEHCR") or ""))}
        for which, n in names:
            if not n or n not in by_name:
                continue
            hits = by_name[n]
            row = {"cert": cert, "fdic_name": bank.get("NAME"),
                   "namehcr": bank.get("NAMEHCR"), "state": bank.get("STALP"),
                   "assets_k": bank.get("ASSET"), "matched_on": which,
                   "symbols": ";".join(h.get("symbol", "") for h in hits),
                   "fmp_name": hits[0].get("companyName") or hits[0].get("name")}
            sym = hits[0].get("symbol", "").upper()
            if len(hits) > 1:
                row["why"] = "multiple symbols share the name"
                review.append(row)
            elif sym in covered_tickers:
                pass                              # already in the universe
            elif sym in sec_tickers:
                row["why"] = "SEC-listed ticker (SEC path territory)"
                review.append(row)
            else:
                row["ticker"] = sym
                auto.append(row)
            break                                 # one match slot per bank

    # De-dup (a holdco with several bank certs matches once per cert — keep
    # the largest-asset cert per ticker; extra certs go to review).
    by_ticker: dict[str, dict] = {}
    for row in auto:
        t = row["ticker"]
        prev = by_ticker.get(t)
        if prev is None:
            by_ticker[t] = row
        else:
            keep, drop = ((row, prev) if (row.get("assets_k") or 0) >
                          (prev.get("assets_k") or 0) else (prev, row))
            drop["why"] = f"second cert for {t} (kept {keep['cert']})"
            review.append(drop)
            by_ticker[t] = keep
    auto = list(by_ticker.values())

    # Price gate: an auto candidate must show a REAL EOD close. Sub-$1
    # prices are dead shells or mis-joined foreign lines (FSTF at $0.009
    # "matched" a $1.3B Michigan bank in the first pass) → review.
    if auto:
        from data.fmp_client import get_eod_close_batch
        closes = get_eod_close_batch([r["ticker"] for r in auto]) or {}
        priced = []
        for r in auto:
            px = (closes.get(r["ticker"]) or {}).get("price") if isinstance(
                closes.get(r["ticker"]), dict) else closes.get(r["ticker"])
            if px and float(px) >= 1.0:
                r["eod_price"] = px
                priced.append(r)
            else:
                r["eod_price"] = px or ""
                r["why"] = ("sub-$1 price (shell / wrong line)" if px
                            else "no FMP EOD price")
                review.append(r)
        auto = priced

    # SECOND KEY: the listing's registered state must equal the FDIC state.
    # Exact legal-name equality alone is not identity — distinct banks reuse
    # generic names ("Union Financial Corp"). No profile state → review,
    # never a silent pass.
    if auto:
        confirmed = []
        for r in auto:
            st = _fmp_profile_state(r["ticker"])
            r["fmp_state"] = st or ""
            if st and st == (r.get("state") or "").upper():
                confirmed.append(r)
            else:
                r["why"] = (f"state mismatch fmp={st}" if st
                            else "no profile state to corroborate")
                review.append(r)
        auto = confirmed

    _OUT_DIR.mkdir(exist_ok=True)
    for name, rows in (("otc_sweep_auto.csv", auto),
                       ("otc_sweep_review.csv", review)):
        path = _OUT_DIR / name
        cols = ["ticker", "cert", "fdic_name", "namehcr", "fmp_name", "state",
                "fmp_state", "assets_k", "matched_on", "eod_price", "symbols",
                "why"]
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"[sweep] wrote {path.name}: {len(rows)} rows")

    if apply and auto:
        _apply(auto)
    return {"auto": auto, "review": review}


def _apply(rows: list[dict]) -> None:
    """Surgical alphabetical inserts into bank_map_resolved.json (preserve
    the file's 1-space indent style — never rewrite via json.dump)."""
    src = _MAP_PATH.read_text(encoding="utf-8")
    existing = json.loads(src)
    added = 0
    for r in sorted(rows, key=lambda x: x["ticker"]):
        t = r["ticker"]
        if t in existing:
            continue
        name = (r.get("namehcr") or r.get("fdic_name") or t).title()
        entry = (f' "{t}": {{\n  "cik": null,\n  "fdic_cert": {r["cert"]},\n'
                 f'  "fdic_score": 1.0,\n  "name": {json.dumps(name)}\n }},\n')
        anchor = None
        for k in sorted(list(existing) + [t]):
            if k > t:
                anchor = f' "{k}": {{'
                break
        if anchor and anchor in src:
            src = src.replace(anchor, entry + anchor, 1)
            existing[t] = True
            added += 1
        else:
            print(f"[sweep] NO INSERT POINT for {t} — add manually")
    _MAP_PATH.write_text(src, encoding="utf-8", newline="")
    json.loads(_MAP_PATH.read_text(encoding="utf-8"))   # must still parse
    print(f"[sweep] applied {added} entries to {_MAP_PATH.name}")


if __name__ == "__main__":
    sweep(apply="--apply" in sys.argv)
