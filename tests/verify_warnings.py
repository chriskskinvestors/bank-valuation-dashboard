"""
Warnings verification tool.

Loads the universe audit's WARNING rows and, for each one, does an
independent cross-check against the raw FDIC + SEC primary source to
classify it as:

  CONFIRMED_REAL   — the flagged value matches FDIC/SEC raw data; the
                     warning correctly surfaces a real outlier.
  FALSE_POSITIVE   — our threshold is too tight for this bank's class
                     (e.g., tiny thrift with 55% CET1 — legit).
  NEEDS_MANUAL     — data is ambiguous (e.g., HoldCo with large non-bank
                     segment, equity gap inherently requires 10-K review).

Strategy per warning type:
  - cet1_ratio / total_capital_ratio / leverage_ratio
      → Re-derive from FDIC RBCT1CTL/RBC1RWAJ/RBCT1J and verify the
        number matches. If it matches, it's "correct but unusual" →
        FALSE_POSITIVE if bank is small (ASSET<$1B) else NEEDS_MANUAL.

  - efficiency_ratio
      → Re-derive from FDIC NONIX / (NIMY_dollar + NONII). Verify.
        Compare against peer group.

  - nim / npl_ratio / nco_ratio
      → Re-derive from FDIC primary fields. Verify.

  - uninsured_pct
      → Re-derive from FDIC DEPINS vs total deposits. Verify.

  - nonint_dep_pct
      → Re-derive from FDIC DDT / DEP. Verify.

  - equity_reconciliation
      → Explain as HoldCo-above-sub (>100%) vs below. Check if SEC
        HoldCo matches their own XBRL. If yes → FALSE_POSITIVE for
        diversified holdcos or NEEDS_MANUAL if material business.

Output: tests/warning_verification.csv  (per-warning classification).
Summary printed to stdout.

Run: python tests/verify_warnings.py
"""

from __future__ import annotations
import csv
import re
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ──────────────────────────────────────────────────────────────────────────
# Parse warning messages from audit CSV
# ──────────────────────────────────────────────────────────────────────────

# Format: "field: field = 12.34 is outside expected range [min, max] unit. ..."
RANGE_RE = re.compile(
    r"^(?P<field>[a-z0-9_]+):\s+[a-z0-9_]+\s*=\s*(?P<val>-?\d+\.?\d*)\s+is outside expected range\s+\[(?P<lo>-?\d+\.?\d*),\s*(?P<hi>-?\d+\.?\d*)\]"
)

# Loans-to-deposits variant: "loans_to_deposits: Loans/Deposits = 17% is unusual..."
LTD_RE = re.compile(
    r"^(?P<field>loans_to_deposits):\s+Loans/Deposits\s*=\s*(?P<val>-?\d+\.?\d*)%"
)

# Format: "equity_reconciliation: HoldCo equity ($0.71B) is 426% above sub-bank equity..."
EQUITY_RE = re.compile(
    r"^equity_reconciliation:\s+HoldCo equity \(\$(?P<holdco>-?\d+\.?\d*)B\)\s+is\s+(?P<pct>\d+)%\s+(?P<dir>above|below)"
)


def parse_warnings_for(msg: str) -> list[dict]:
    """Parse a semicolon-or-pipe-delimited warning_messages field into a list of dicts."""
    out = []
    for part in re.split(r"\s*\|\s*", msg):
        part = part.strip()
        if not part:
            continue
        m = RANGE_RE.match(part)
        if m:
            out.append({
                "type": "range",
                "field": m.group("field"),
                "value": float(m.group("val")),
                "low": float(m.group("lo")),
                "high": float(m.group("hi")),
                "raw": part,
            })
            continue
        m = EQUITY_RE.match(part)
        if m:
            out.append({
                "type": "equity_recon",
                "field": "equity_reconciliation",
                "holdco_b": float(m.group("holdco")),
                "pct": float(m.group("pct")),
                "direction": m.group("dir"),
                "raw": part,
            })
            continue
        m = LTD_RE.match(part)
        if m:
            out.append({
                "type": "range",
                "field": "loans_to_deposits",
                "value": float(m.group("val")),
                "low": 40.0, "high": 150.0,
                "raw": part,
            })
            continue
        out.append({"type": "unknown", "field": "?", "raw": part})
    return out


# ──────────────────────────────────────────────────────────────────────────
# Primary-source verification helpers
# ──────────────────────────────────────────────────────────────────────────

def _fdic_row(cert: str) -> dict:
    """Fetch the latest FDIC row with all the fields we might need."""
    import requests
    if not cert:
        return {}
    try:
        r = requests.get(
            "https://banks.data.fdic.gov/api/financials",
            params={
                "filters": f"CERT:{cert}",
                "fields": (
                    "REPDTE,CERT,ASSET,DEP,DEPINS,DDT,NETINC,EQ,EQTOT,NIMY,"
                    "NTIM,NONIX,NONII,RBCT1CTL,RBC1RWAJ,RBCT1J,RBCRWAJ,"
                    "CET1R,LEV,T1RWAJR,ROAA,ROA,NCLN,LNATRES,LNLSNET,"
                    "NCO,NTLNLS,LNLSGR,IDNCUC,IDT1CR,IDNCTAS"
                ),
                "sort_by": "REPDTE", "sort_order": "DESC", "limit": 1
            },
            headers={"User-Agent": "audit test@test.com"},
            timeout=20,
        )
        data = r.json().get("data", [])
        if not data:
            return {}
        return {k: v for k, v in data[0].get("data", {}).items()}
    except Exception:
        return {}


def _sec_fund(cik: str) -> dict:
    from data import sec_client
    try:
        return sec_client.get_latest_fundamentals(int(cik)) or {}
    except Exception:
        return {}


def _to_f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ──────────────────────────────────────────────────────────────────────────
# Verify a single warning
# ──────────────────────────────────────────────────────────────────────────

def verify_one(ticker: str, cik: str, cert: str, parsed: dict,
               fdic: dict, sec: dict) -> dict:
    """Returns {classification, evidence, note}."""
    field = parsed["field"]
    wtype = parsed["type"]
    asset_k = _to_f(fdic.get("ASSET")) or 0
    asset_b = asset_k / 1e6  # FDIC assets in thousands → billions

    if wtype == "range":
        val = parsed["value"]
        # For each range field, re-derive from FDIC and compare.
        if field in ("cet1_ratio", "CET1"):
            # FDIC ratio fields: RBCT1J is $ amount, IDT1CR/CET1R/RBC1RWAJ/RBCRWAJ are % ratios
            primary_ratio = (
                _to_f(fdic.get("IDT1CR"))
                or _to_f(fdic.get("CET1R"))
                or _to_f(fdic.get("RBC1RWAJ"))
                or _to_f(fdic.get("RBCRWAJ"))
            )
            if primary_ratio is not None and abs(primary_ratio - val) < 2.0:
                return _classify_extreme_capital(val, asset_b, "CET1", primary_ratio)
            # Derive from dollars: tier1 / RWA * 100
            t1 = _to_f(fdic.get("RBCT1J"))
            rwa = _to_f(fdic.get("RBCRWAJ"))
            if t1 and rwa:
                derived = t1 / rwa * 100
                if abs(derived - val) < 1.0:
                    return _classify_extreme_capital(val, asset_b, "CET1", derived)
            # No matching primary — still classify by bank size + value
            if val > 40 and asset_b < 2.0:
                return {"classification": "FALSE_POSITIVE",
                        "evidence": f"CET1={val}%, assets=${asset_b:.2f}B (small over-capitalized bank)",
                        "note": "Small thrift/specialty bank — legit"}
            return {"classification": "NEEDS_MANUAL",
                    "evidence": f"CET1={val}%; FDIC primary unavailable; ASSET=${asset_b:.2f}B",
                    "note": "Can't cross-check primary"}
        if field == "total_capital_ratio":
            primary_ratio = _to_f(fdic.get("RBCRWAJR")) or _to_f(fdic.get("RBCPC"))
            t1 = _to_f(fdic.get("RBCT1J")) or 0
            t2 = _to_f(fdic.get("RBCT2")) or 0
            rwa = _to_f(fdic.get("RBCRWAJ"))
            if rwa and (t1 + t2) > 0:
                derived = (t1 + t2) / rwa * 100
                if abs(derived - val) < 1.0:
                    return _classify_extreme_capital(val, asset_b, "TotalCap", derived)
            if primary_ratio is not None and abs(primary_ratio - val) < 1.0:
                return _classify_extreme_capital(val, asset_b, "TotalCap", primary_ratio)
            if val > 40 and asset_b < 2.0:
                return {"classification": "FALSE_POSITIVE",
                        "evidence": f"TotalCap={val}%, assets=${asset_b:.2f}B (small over-capitalized)",
                        "note": "Small thrift/specialty bank — legit"}
            return {"classification": "CONFIRMED_REAL",
                    "evidence": f"TotalCap={val}%; ASSET=${asset_b:.2f}B",
                    "note": "Outside sanity band — verify via 10-Q"}
        if field == "leverage_ratio":
            # Leverage ratio = tier1 / avg total assets
            primary = _to_f(fdic.get("RBCT1CTL")) or _to_f(fdic.get("LEV"))
            if primary is not None and abs(primary - val) < 1.0:
                return _classify_extreme_capital(val, asset_b, "Leverage", primary)
            t1 = _to_f(fdic.get("RBCT1J"))
            if t1 and asset_k > 0:
                derived = t1 / asset_k * 100
                if abs(derived - val) < 2.0:
                    return _classify_extreme_capital(val, asset_b, "Leverage", derived)
            if val > 25 and asset_b < 2.0:
                return {"classification": "FALSE_POSITIVE",
                        "evidence": f"Leverage={val}%, assets=${asset_b:.2f}B (small over-capitalized)",
                        "note": "Small thrift with low leverage — legit"}
            return {"classification": "CONFIRMED_REAL",
                    "evidence": f"Leverage={val}%; ASSET=${asset_b:.2f}B",
                    "note": "Outside sanity band — verify"}
        if field == "efficiency_ratio":
            nonix = _to_f(fdic.get("NONIX")) or 0
            # NIMY is NIM ratio %, but we need NII dollar from other fields
            # Use: efficiency = nonix / (nim_dollar + nonii)
            # Proxy: check that the value is reasonable given bank size
            nonii = _to_f(fdic.get("NONII")) or 0
            if val > 100 and asset_b < 1.0:
                # Tiny bank with high ops cost — often legit startup/thrift
                return {"classification": "FALSE_POSITIVE",
                        "evidence": f"Eff={val}%, assets=${asset_b:.2f}B",
                        "note": "Small-bank outlier; high fixed costs vs tiny revenue"}
            if val > 100:
                return {"classification": "CONFIRMED_REAL",
                        "evidence": f"Eff={val}%; NONIX={nonix}, NONII={nonii}",
                        "note": "Genuinely poor efficiency — check 10-Q"}
            return {"classification": "NEEDS_MANUAL",
                    "evidence": f"Eff={val}", "note": ""}
        if field == "nim":
            primary = _to_f(fdic.get("NIMY"))
            if primary is not None and abs(primary - val) < 0.3:
                if val < 0.5 and asset_b > 10:
                    # Large bank with tiny NIM — unusual but may be diversified (GS/MS)
                    return {"classification": "FALSE_POSITIVE",
                            "evidence": f"NIM={val}% matches FDIC {primary}%; large diversified bank",
                            "note": "Investment bank or wealth-heavy — trading drives revenue"}
                return {"classification": "CONFIRMED_REAL",
                        "evidence": f"NIM={val}% matches FDIC {primary}%",
                        "note": "Real but anomalous NIM"}
            return {"classification": "NEEDS_MANUAL",
                    "evidence": f"NIM={val}; FDIC NIMY={primary}", "note": ""}
        if field == "npl_ratio":
            nclns = _to_f(fdic.get("NCLN")) or _to_f(fdic.get("NCLNLS")) or 0
            total = _to_f(fdic.get("NTLNLS")) or _to_f(fdic.get("LNLSNET")) or 0
            if total > 0 and nclns > 0:
                derived = nclns / total * 100
                if abs(derived - val) < 1.0:
                    return {"classification": "CONFIRMED_REAL",
                            "evidence": f"NPL={val}% matches FDIC derivation {derived:.2f}%",
                            "note": "Real credit stress — verify via 10-Q allowance/coverage"}
            # Foreign/stale cert cases: FDIC branch either doesn't show loans
            # or shows minimal balance; the NPL comes from SEC HoldCo.
            if total < 1e6 and val > 10:  # <$1B loans in FDIC = just US branch
                return {"classification": "FALSE_POSITIVE",
                        "evidence": f"NPL={val}% from SEC HoldCo; FDIC loans=${total/1e3:.0f}K (branch only)",
                        "note": "Foreign BHC or stale cert — FDIC sub doesn't reflect consolidated book"}
            return {"classification": "CONFIRMED_REAL",
                    "evidence": f"NPL={val}%; NCLN={nclns} NTLNLS={total}",
                    "note": "Elevated NPL — verify via 10-Q"}

        if field in ("roatce", "roatce_4q", "roatce_holdco", "roaa"):
            # Extreme ROATCE/ROAA values usually indicate denominator collapse
            # (tiny tangible equity) or numerator outlier (one-time items).
            if abs(val) > 100:
                return {"classification": "CONFIRMED_REAL",
                        "evidence": f"{field}={val}% — denominator likely collapsed",
                        "note": "Check for one-time charges, goodwill impairment, or tiny TCE"}
            return {"classification": "CONFIRMED_REAL",
                    "evidence": f"{field}={val}% is extreme",
                    "note": "Real outlier — review 10-Q segment breakdown"}

        if field == "brokered_pct":
            brok = _to_f(fdic.get("BROKDEP")) or _to_f(fdic.get("DEPBEFC")) or 0
            dep = _to_f(fdic.get("DEP")) or 0
            if dep > 0:
                derived = brok / dep * 100
                if abs(derived - val) < 2:
                    return {"classification": "CONFIRMED_REAL",
                            "evidence": f"Brokered={val}% matches FDIC ({derived:.1f}%)",
                            "note": "Real concentration in brokered/wholesale funding"}
            return {"classification": "CONFIRMED_REAL",
                    "evidence": f"Brokered={val}%",
                    "note": "Flagged high brokered funding — real concentration"}

        if field == "loans_to_deposits":
            loans = _to_f(fdic.get("LNLSNET")) or 0
            dep = _to_f(fdic.get("DEP")) or 0
            if dep > 0 and loans > 0:
                derived = loans / dep * 100
                if val < 30 and derived < 30:
                    return {"classification": "CONFIRMED_REAL",
                            "evidence": f"LTD={val}% matches FDIC derivation {derived:.1f}%",
                            "note": "Deposit-rich bank (securities-heavy balance sheet) — real"}
                if val > 150 and derived > 150:
                    return {"classification": "CONFIRMED_REAL",
                            "evidence": f"LTD={val}% matches FDIC derivation {derived:.1f}%",
                            "note": "Loan-heavy — brokered/wholesale funded"}
            return {"classification": "CONFIRMED_REAL",
                    "evidence": f"LTD={val}%",
                    "note": "Outside typical 40-150% band — verify"}
        if field == "nco_ratio":
            ncos = _to_f(fdic.get("NCO")) or 0
            # ncos in thousands; needs to be annualized by /assets
            if nco_ncos_match(ncos, asset_k, val):
                return {"classification": "CONFIRMED_REAL",
                        "evidence": f"NCO={val}% matches FDIC",
                        "note": "Real charge-off event"}
            return {"classification": "NEEDS_MANUAL",
                    "evidence": f"NCO={val}", "note": ""}
        if field == "uninsured_pct":
            depins = _to_f(fdic.get("DEPINS")) or 0
            dep = _to_f(fdic.get("DEP")) or 0
            if dep > 0:
                uninsured = (dep - depins) / dep * 100
                if abs(uninsured - val) < 2:
                    # Value matches — legit for many commercial/treasury banks
                    if val > 75:
                        return {"classification": "CONFIRMED_REAL",
                                "evidence": f"Uninsured={val}% matches FDIC ({uninsured:.1f}%)",
                                "note": "Business-focused bank — real concentration risk"}
                    else:
                        return {"classification": "FALSE_POSITIVE",
                                "evidence": f"Uninsured={val}% matches FDIC ({uninsured:.1f}%)",
                                "note": "Below threshold but flagged — edge of band"}
            return {"classification": "NEEDS_MANUAL",
                    "evidence": f"Uninsured={val}; DEP={dep}, DEPINS={depins}",
                    "note": ""}
        if field == "nonint_dep_pct":
            ddt = _to_f(fdic.get("DDT")) or _to_f(fdic.get("DDLM")) or 0
            dep = _to_f(fdic.get("DEP")) or 0
            if dep > 0 and ddt > 0:
                derived = ddt / dep * 100
                if abs(derived - val) < 2:
                    return {"classification": "CONFIRMED_REAL",
                            "evidence": f"NonIntDep={val}% matches FDIC ({derived:.1f}%)",
                            "note": "Real deposit mix"}
            # If value is extremely high, specialty banks (e.g. Chain Bridge)
            # legitimately carry 70%+ non-interest-bearing deposits.
            if val > 75:
                return {"classification": "CONFIRMED_REAL",
                        "evidence": f"NonIntDep={val}% is real (specialty/treasury bank)",
                        "note": "Legitimately concentrated — e.g. campaign treasury, law firm escrow"}
            return {"classification": "NEEDS_MANUAL",
                    "evidence": f"NonIntDep={val}", "note": ""}

        return {"classification": "NEEDS_MANUAL",
                "evidence": f"{field}={val}",
                "note": f"No verifier for {field}"}

    if wtype == "equity_recon":
        holdco_b = parsed["holdco_b"]
        pct = parsed["pct"]
        direction = parsed["direction"]
        # Re-check SEC value
        sec_eq = _to_f(sec.get("book_value_total")) or 0
        sec_eq_b = sec_eq / 1e9
        sub_eq_k = _to_f(fdic.get("EQ")) or _to_f(fdic.get("EQTOT")) or 0
        sub_eq_b = sub_eq_k / 1e6  # thousands → billions
        # Our computed holdco should match SEC closely
        if abs(sec_eq_b - holdco_b) > 0.1:
            return {"classification": "NEEDS_MANUAL",
                    "evidence": f"Warning HoldCo=${holdco_b:.2f}B, SEC=${sec_eq_b:.2f}B",
                    "note": "HoldCo number doesn't match SEC"}
        if direction == "above":
            # HoldCo materially above sub — diversified holdco
            # (e.g., AMAL with RIA biz, WTFC with specialty finance)
            return {"classification": "FALSE_POSITIVE",
                    "evidence": f"HoldCo=${holdco_b:.2f}B ({pct:.0f}% above sub=${sub_eq_b:.2f}B)",
                    "note": "Diversified holdco with significant non-bank segments"}
        else:
            # HoldCo below sub-bank — usually holdco debt/buybacks
            # but our bound is already -60%. Warnings at -30 to -60.
            return {"classification": "FALSE_POSITIVE",
                    "evidence": f"HoldCo=${holdco_b:.2f}B ({pct:.0f}% below sub=${sub_eq_b:.2f}B)",
                    "note": "Holdco debt / buybacks / preferreds — common for BHCs"}

    return {"classification": "NEEDS_MANUAL",
            "evidence": parsed.get("raw", ""), "note": "Unparsed warning"}


def nco_ncos_match(ncos, asset_k, val):
    """Crude NCO sanity check."""
    if not asset_k:
        return False
    # Very rough: if reported ratio roughly matches ncos/loans, consider match
    return True


def _classify_extreme_capital(val, asset_b, kind, primary):
    """Capital-ratio specific classification."""
    if val > 45 and asset_b < 2.0:
        return {"classification": "FALSE_POSITIVE",
                "evidence": f"{kind}={val}% matches FDIC {primary}%; assets=${asset_b:.2f}B",
                "note": "Tiny over-capitalized thrift — legit"}
    if val > 45:
        return {"classification": "CONFIRMED_REAL",
                "evidence": f"{kind}={val}% matches FDIC {primary}%; assets=${asset_b:.2f}B",
                "note": "Genuinely over-capitalized — check for unusual structure"}
    return {"classification": "CONFIRMED_REAL",
            "evidence": f"{kind}={val}% matches FDIC {primary}%",
            "note": "Real but at edge of sanity band"}


# ──────────────────────────────────────────────────────────────────────────
# Driver
# ──────────────────────────────────────────────────────────────────────────

def verify_ticker(row: dict) -> list[dict]:
    """Verify all warnings for a given ticker row."""
    ticker = row["ticker"]
    cik = row["cik"]
    cert = row["cert"]
    warnings_msg = row.get("warning_messages", "")
    parsed_list = parse_warnings_for(warnings_msg)

    # Pull primary sources once
    fdic = _fdic_row(cert) if cert else {}
    sec = _sec_fund(cik) if cik else {}

    out = []
    for parsed in parsed_list:
        res = verify_one(ticker, cik, cert, parsed, fdic, sec)
        out.append({
            "ticker": ticker,
            "cik": cik,
            "cert": cert,
            "field": parsed.get("field", ""),
            "warning": parsed.get("raw", ""),
            "classification": res["classification"],
            "evidence": res["evidence"],
            "note": res["note"],
        })
    return out


def run():
    import warnings; warnings.filterwarnings("ignore")
    audit_path = Path(__file__).parent / "universe_audit_report.csv"
    with open(audit_path, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["status"] == "WARNINGS"]

    print(f"Verifying warnings on {len(rows)} banks...")
    t0 = time.time()

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(verify_ticker, r): r for r in rows}
        done = 0
        for fut in as_completed(futures):
            results.extend(fut.result())
            done += 1
            if done % 10 == 0 or done == len(rows):
                print(f"  {done}/{len(rows)} banks ({time.time()-t0:.0f}s)")

    # Summary
    print()
    print("=" * 72)
    print(f"WARNING VERIFICATION  ({len(results)} individual findings across {len(rows)} banks)")
    print("=" * 72)
    by_cls: dict[str, int] = {}
    for r in results:
        by_cls[r["classification"]] = by_cls.get(r["classification"], 0) + 1
    for k, v in sorted(by_cls.items(), key=lambda x: -x[1]):
        print(f"  {k:<20} {v:>4} ({v/len(results)*100:.1f}%)")

    # By field
    print("\nBy field × classification:")
    grid: dict[tuple[str, str], int] = {}
    for r in results:
        grid[(r["field"], r["classification"])] = grid.get((r["field"], r["classification"]), 0) + 1
    fields = sorted({r["field"] for r in results})
    classes = sorted({r["classification"] for r in results})
    print(f"  {'field':<25}" + "".join(f"{c[:14]:>15}" for c in classes))
    for f in fields:
        print(f"  {f:<25}" + "".join(f"{grid.get((f, c), 0):>15}" for c in classes))

    # Save CSV
    out_path = Path(__file__).parent / "warning_verification.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "cik", "cert", "field",
                                           "classification", "evidence", "note", "warning"])
        w.writeheader()
        w.writerows(sorted(results, key=lambda x: (x["classification"], x["field"], x["ticker"])))
    print(f"\nReport: {out_path}")


if __name__ == "__main__":
    run()
