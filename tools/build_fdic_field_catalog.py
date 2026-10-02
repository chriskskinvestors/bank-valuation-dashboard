"""Regenerate data/fdic_field_catalog.json from FDIC's financials dictionary.

The screener's "+ Call report field" picker offers the numeric fields of the
FDIC BankFind financials API. The dictionary is published as YAML at
https://api.fdic.gov/banks/docs/risview_properties.yaml; the result is vendored
as JSON so the app needs no YAML dependency and tests stay hermetic.

WHY VALUE EVIDENCE (audit 2026-09-30): the dictionary's metadata cannot say
which fields are dollars and which are ratios. Its "double" tag marks 25
dollar items (ALLOTHL "all other liabilities", EEFF "efficiency ratio
expense", OTHBRF …) and misses ~20 real ratios (LNLSNTV, DEPDASTR, NIMYQ,
LNCIT1R …); titles mislead too ("…QUARTERLY RATIO" on a $ provision). A ratio
shown as dollars, or dollars as a ratio, is a wrong number — so each field is
classified from the 50 largest banks' actual values plus its title:

  exclude — flags, identifiers, codes, geography (never screenable measures)
  count   — counts of offices/accounts/employees/loans; shown as reported;
            summed across a multi-charter group
  level   — some top-50 bank reports |value| > 10,000: a $thousands amount
            (a ratio cannot reach that at those banks). Converted to dollars.
  ratio   — a ratio signal (double tag or ratio-style title) and every top-50
            value ≤ 10,000. Shown as reported (FDIC percent units).
  asis    — no conclusive evidence (e.g. a niche dollar item that is tiny even
            at the megabanks, or a field they don't report). Shown EXACTLY as
            FDIC reports it, labeled so; never scaled; n/a for multi-charter
            groups (it cannot be proven summable).

Run:  python tools/build_fdic_field_catalog.py   (needs PyYAML locally)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import requests
import yaml

URL = "https://api.fdic.gov/banks/docs/risview_properties.yaml"
FIN = "https://api.fdic.gov/banks/financials"
OUT = Path(__file__).parent.parent / "data" / "fdic_field_catalog.json"
LEVEL_FLOOR = 10_000          # $thousands — see docstring

# Non-measures that are numeric in the API (identifiers, codes, dates, geo).
_EXCLUDE = {
    "CERT", "REPDTE", "ID", "RSSDHCR", "FED_RSSD", "DOCKET", "RSSDID", "CALLYM",
    "CALLYMD", "RISDATE", "RUNDATE", "FDICDBS", "FDICSUPV", "FED", "OCCDIST",
    "STCNTY", "STNUM", "ZIP", "INSDATE", "ESTYMD", "EFFDATE", "PROCDATE",
    "DATEUPDT", "YEAR", "QUARTER", "QTRNO", "CALLFORM", "CLCODE", "CNTYNUM",
    "ENTTYPE", "FDICAREA", "QBPRSAVS", "QBPRCOML", "SIMS_LAT", "SIMS_LONG",
    "SPECGRP", "TRUSTPWR", "TRACT", "USA", "ACTIVE", "INSTCNT", "BRANCH",
    "EDGECODE", "N", "METRO", "NTINCHPP", "NTINQHPP", "TREXER", "TRPOWER",
    "CBSA_NO", "CSA_NO", "MSA_NO", "FLDOFF_NO",
    "CBLRIND",          # "Community Bank Ratio" is a 0/1 CBLR-election flag
}
_FLAG_RE = re.compile(r"\bFLAG\b|\bFLG\b", re.I)
# Counts: explicit count wording (NOT "offices" — "Deposits held in domestic
# offices" is a $ amount) or an office-count code (OFFDOM, OFFTOT …). A title
# that also says RATIO is a ratio of counts, not a count.
_COUNT_RE = re.compile(r"^NUMBER\b|\bNUMBER OF\b|\bNUM OF\b|-NUM\b|\bNUM$", re.I)


def _is_count(code: str, title: str) -> bool:
    if re.search(r"\bRATIO\b", title, re.I):
        return False
    return bool(_COUNT_RE.search(title)) or code.startswith("OFF")
# A "/" that is part of an abbreviation, not a quotient.
_SLASH_ABBR = re.compile(r"\b(N/C|P/D|W/|N/SECUR|G/L|S/B)", re.I)
_RATIO_TITLE = re.compile(
    r"\bRATIO\b|\bYIELD\b|\bMARGIN\b|RETURN ON|PER EMPLOYEE|COST OF FUNDING|"
    r"\bGROWTH\b|-Y1\b|\bPERCENT|%", re.I)


def _ratio_signal(p: dict, title: str) -> bool:
    if p.get("x-elastic-type") == "double" or _RATIO_TITLE.search(title):
        return True
    return "/" in _SLASH_ABBR.sub("", title)


def _top50_values(codes: list[str], repdte: str) -> dict[str, list[float]]:
    certs = [d["data"]["CERT"] for d in requests.get(FIN, params={
        "filters": f"REPDTE:{repdte}", "fields": "CERT,ASSET",
        "sort_by": "ASSET", "sort_order": "DESC", "limit": 50}, timeout=60).json()["data"]]
    flt = f"REPDTE:{repdte} AND CERT:(" + " OR ".join(str(c) for c in certs) + ")"
    vals: dict[str, list[float]] = {}
    for i in range(0, len(codes), 300):
        chunk = codes[i:i + 300]
        rows = requests.get(FIN, params={"filters": flt, "fields": ",".join(chunk),
                                         "limit": 100}, timeout=120).json()["data"]
        for r in rows:
            for k in chunk:
                v = r["data"].get(k)
                if isinstance(v, (int, float)):
                    vals.setdefault(k, []).append(float(v))
    return vals


def build(repdte: str) -> dict:
    props = yaml.safe_load(requests.get(URL, timeout=60).text)[
        "properties"]["data"]["properties"]
    numeric = {k: p for k, p in props.items() if p.get("type") == "number"}
    vals = _top50_values(sorted(numeric), repdte)
    out = {}
    for code, p in sorted(numeric.items()):
        title = " ".join(str(p.get("title") or code).split())
        v = vals.get(code, [])
        if code in _EXCLUDE or _FLAG_RE.search(title) or (v and set(v) <= {0.0, 1.0}
                                                          and not _is_count(code, title)
                                                          and not _ratio_signal(p, title)):
            continue
        big = any(abs(x) > LEVEL_FLOOR for x in v)
        if _is_count(code, title):
            kind = "count"
        elif big:
            kind = "level"
        elif _ratio_signal(p, title) and v:
            kind = "ratio"
        else:
            kind = "asis"
        out[code] = {"title": title, "kind": kind}
    # Second pass over EVERY filer for the ratio/asis candidates:
    #  * integer evidence — FDIC reports $ amounts as whole $thousands and
    #    computes ratios as decimals. A field whose every nonzero value across
    #    all filers is an integer is a $K amount even if its title has a "/"
    #    (NTCOMREQ "COMMERCIAL RE CHG-OFF/…" is a $ charge-off; review 2026-10-02
    #    found 5 such "ratios" and 53 integer "asis" fields shown 1000x off).
    #  * magnitude — an "asis" field (no ratio signal) with any |value| >
    #    LEVEL_FLOOR at some filer is a $K amount.
    #  A ratio-signalled field needs more than integers: constant ratios
    #  (ASSETR/LIABEQR/IDNTILR are always 100) are whole numbers too, so it
    #  also needs a value > 100 or "amount" in its title (AVPPPPLG).
    cand = [k for k, v in out.items() if v["kind"] in ("ratio", "asis")]
    for code, (mx, n_nonzero, all_int) in _all_filers_stats(cand, repdte).items():
        kind = out[code]["kind"]
        amount = re.search(r"\bAMOUNT\b|\bAMT\b", out[code]["title"], re.I)
        if n_nonzero and all_int and (kind == "asis" or mx > 100 or amount):
            out[code]["kind"] = "level"
        elif kind == "asis" and mx > LEVEL_FLOOR:
            out[code]["kind"] = "level"
    return out


def _all_filers_stats(codes: list[str], repdte: str) -> dict[str, tuple]:
    """(max |value|, nonzero count, every nonzero value an integer) per field
    across every filer of the quarter."""
    st: dict[str, list] = {}
    for i in range(0, len(codes), 50):
        chunk = codes[i:i + 50]
        rows = requests.get(FIN, params={"filters": f"REPDTE:{repdte}",
                                         "fields": ",".join(chunk), "limit": 10000},
                            timeout=180).json()["data"]
        for r in rows:
            for k in chunk:
                v = r["data"].get(k)
                if isinstance(v, (int, float)):
                    s = st.setdefault(k, [0.0, 0, True])
                    s[0] = max(s[0], abs(float(v)))
                    if v != 0:
                        s[1] += 1
                        s[2] = s[2] and float(v).is_integer()
    return {k: tuple(v) for k, v in st.items()}


if __name__ == "__main__":
    repdte = sys.argv[1] if len(sys.argv) > 1 else "20260630"
    cat = build(repdte)
    OUT.write_text(json.dumps(cat, indent=0, sort_keys=True, ensure_ascii=False),
                   encoding="utf-8")
    kinds: dict[str, int] = {}
    for v in cat.values():
        kinds[v["kind"]] = kinds.get(v["kind"], 0) + 1
    print(f"wrote {OUT} — {len(cat)} fields {kinds} (evidence: top-50 banks {repdte})",
          file=sys.stderr)
