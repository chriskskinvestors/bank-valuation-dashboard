"""Scrape SEC filing documents (10-K/10-Q) and parse their inline XBRL.

The flat SEC companyfacts API DROPS XBRL dimensions, so dimensional disclosures
— regulatory capital (consolidated vs bank), credit-quality grades, fair-value
levels — are unavailable or ambiguous there. The filing HTML carries inline
XBRL (iXBRL) with full dimensional context, so we parse the document directly.
See docs/DATA-SOURCING-ARCHITECTURE.md and the flexible-sourcing memory.

This module is the SEC provider's core: locate the latest filing, fetch it, and
turn its iXBRL into a list of Facts (concept, scaled+signed value, period,
dimensional members). Higher layers map concepts/members to display lines.
"""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass

_UA = {"User-Agent": "KSK Investors research chris@kskinvestors.com"}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def latest_filing(cik, forms=("10-K",)) -> dict | None:
    """Most recent filing among `forms`: {accession, doc, date, form} or None."""
    cik10 = str(int(cik)).zfill(10)
    data = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik10}.json"))
    rec = data.get("filings", {}).get("recent", {})
    for i, f in enumerate(rec.get("form", [])):
        if f in forms:
            return {"accession": rec["accessionNumber"][i].replace("-", ""),
                    "doc": rec["primaryDocument"][i],
                    "date": rec["filingDate"][i], "form": f, "cik": int(cik)}
    return None


def filing_url(cik, accession, doc) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{doc}"


@dataclass
class Fact:
    concept: str            # e.g. "us-gaap:TierOneRiskBasedCapital"
    value: float            # scaled + sign-applied, in base units (dollars, %, …)
    period_end: str         # ISO date (instant or duration end)
    period_start: str | None
    members: dict           # {dimension_qname: member_qname}
    unit: str | None


def _local(tag) -> str:
    """Local name of an lxml HTML tag like 'ix:nonfraction' -> 'nonfraction'."""
    return tag.split(":")[-1].lower() if isinstance(tag, str) else ""


def parse_inline_xbrl(html_bytes: bytes) -> list[Fact]:
    """Parse iXBRL numeric facts out of a filing document.

    Inline XBRL embeds facts as <ix:nonFraction name=… contextRef=… scale=…
    sign=…> elements and their dimensional context as <xbrli:context> blocks.
    lxml's HTML parser flattens namespaces into the tag string (e.g.
    'ix:nonfraction'), so we match on the local name. A negative value can be
    signalled by sign="-" OR by accounting parentheses in the text.
    """
    from lxml import html as lhtml
    root = lhtml.fromstring(html_bytes)

    # 1. contexts: id -> (start, end, {dimension: member})
    contexts: dict[str, tuple] = {}
    for el in root.iter():
        if _local(el.tag) != "context":
            continue
        cid = el.get("id")
        if not cid:
            continue
        start = end = None
        members: dict[str, str] = {}
        for sub in el.iter():
            ln = _local(sub.tag)
            if ln == "instant" or ln == "enddate":
                end = (sub.text or "").strip()
            elif ln == "startdate":
                start = (sub.text or "").strip()
            elif ln == "explicitmember":
                dim = sub.get("dimension")
                if dim:
                    members[dim] = (sub.text or "").strip()
        contexts[cid] = (start, end, members)

    # 2. numeric facts
    facts: list[Fact] = []
    for el in root.iter():
        if _local(el.tag) != "nonfraction":
            continue
        name = el.get("name")
        cref = el.get("contextref")
        if not name or cref not in contexts:
            continue
        raw = (el.text_content() or "").strip()
        if not raw:
            continue
        neg = raw.startswith("(") or el.get("sign") == "-"
        raw = raw.strip("()").replace(",", "").replace(" ", "").strip()
        if not re.match(r"^-?\d", raw):
            continue
        try:
            val = float(raw)
        except ValueError:
            continue
        scale = el.get("scale")
        if scale:
            try:
                val *= 10 ** int(scale)
            except ValueError:
                pass
        if neg:
            val = -abs(val)
        start, end, members = contexts[cref]
        facts.append(Fact(name, val, end, start, members, el.get("unitref")))
    return facts


# ── Holdco regulatory-capital extraction ────────────────────────────────────
# Banks tag the same line under varying us-gaap spellings ("TierOne" vs "Tier1")
# and split holdco-vs-bank three ways (ParentCompanyMember, default context, or
# a custom LegalEntityAxis member). These anchored patterns + the holdco rule
# below are validated across 100 banks (tools/validate_capital_scrape.py), not
# one. A "Required/Minimum/WellCapitalized" concept is a regulatory THRESHOLD,
# never the actual value, so it's excluded.
# CET1 has the most spelling variants across filers: TierOne / Tier1 / TierI,
# an optional "RiskBased" infix, and the abbreviation "Cet1". The ratio suffix
# is "Ratio" or "ToRiskWeightedAssets".
_CET1 = r"(CommonEquityTier(One|1|I)(RiskBased)?Capital|Cet1Capital)"
_CAP_LINE_PATTERNS = {
    "cet1_cap":    re.compile(rf"^{_CET1}$", re.I),
    "cet1_ratio":  re.compile(rf"^{_CET1}(Ratio|ToRiskWeightedAssets)$", re.I),
    "t1_cap":      re.compile(r"^Tier(One|1)RiskBasedCapital$", re.I),
    "t1_ratio":    re.compile(r"^Tier(One|1)RiskBasedCapital(Ratio|ToRiskWeightedAssets)$", re.I),
    "total_cap":   re.compile(r"^Capital$", re.I),
    "total_ratio": re.compile(r"^CapitalToRiskWeightedAssets$", re.I),
    "rwa":         re.compile(r"^RiskWeightedAssets$", re.I),
    "lev_cap":     re.compile(r"^Tier(One|1)LeverageCapital$", re.I),
    "lev_ratio":   re.compile(r"^Tier(One|1)LeverageCapitalToAverageAssets$", re.I),
}
# A regulatory THRESHOLD ("required for adequacy / to be well capitalized"), a
# conservation BUFFER, or a SUPPLEMENTARY leverage line is never the actual
# headline value — exclude so they don't shadow the real concept.
_CAP_THRESHOLD = re.compile(
    r"Required|Requirement|Minimum|WellCapitalized|Adequacy|Buffer|Supplementary|Transitional", re.I)


# A member naming a BANK / trust / national-association subsidiary.
_BANK_MEMBER_HINT = re.compile(r"Bank|NationalAssociation|\bNA\b|Subsidiar|Trust", re.I)
# A member explicitly naming the holding company. Checked BEFORE the bank hint
# because a parent's legal name can contain "Bank" — "BlueRidgeBanksharesInc",
# "BankHoldingCompany" — but its corporate suffix (Inc/Bancshares/Holding/Corp)
# marks it as the parent.
_HOLDCO_MEMBER_HINT = re.compile(
    r"Holding|Bancorp|Bancshares|Bankshares|Corporation|Incorporated|\bInc\b|"
    r"Parent|Consolidated|Group|Financial", re.I)
# Confidence of a holdco match, best → worst.
_HOLDCO_RANK = {"default": 0, "parent": 1, "fuzzy": 2}


def _classify(members: dict) -> tuple[str, str | None]:
    """Classify a fact's entity dimension → (basis, confidence):
    ('holdco', 'default') — no entity dimension (consolidated holdco)
    ('holdco', 'parent')  — an explicit holdco member (ParentCompany, …Inc, …)
    ('holdco', 'fuzzy')   — a plain company-name member (likely parent, flagged)
    ('bank',   None)      — a bank/trust/NA subsidiary member."""
    for dim, mem in members.items():
        d, m = dim.split(":")[-1], mem.split(":")[-1]
        if d in ("ConsolidatedEntitiesAxis", "LegalEntityAxis"):
            if _HOLDCO_MEMBER_HINT.search(m):
                return ("holdco", "parent")
            if _BANK_MEMBER_HINT.search(m):
                return ("bank", None)
            return ("holdco", "fuzzy")
    return ("holdco", "default")


def extract_holdco_capital(facts: list[Fact], anchor_cet1: float | None = None) -> dict:
    """{period_end: {line: value, ...}} of holdco regulatory capital from iXBRL.

    Picks the best-confidence holdco candidate per (period, line) — for ties
    (e.g. standardized vs advanced RWA methodology) the fewest dimensional
    members then Standardized. When `anchor_cet1` (the bank's FDIC CET1 ratio,
    in %) is supplied, the CET1-ratio line is chosen as the candidate CLOSEST to
    that anchor within a band — cross-source disambiguation that rejects the
    regulatory-minimum and tiny-subsidiary values whose dimensional tagging
    otherwise collides with the actual. If no candidate is within band, CET1 is
    left out (n/a) rather than guessed. Each period carries '_confidence' (worst
    relied on) and '_anchored' (whether the FDIC anchor was used)."""
    # cand[(period, line)] = [(basis, conf, members, value), …]
    cand: dict[tuple, list] = {}
    for f in facts:
        local = f.concept.split(":")[-1]
        if _CAP_THRESHOLD.search(local):
            continue
        line = next((ln for ln, pat in _CAP_LINE_PATTERNS.items() if pat.match(local)), None)
        if not line:
            continue
        basis, conf = _classify(f.members)
        cand.setdefault((f.period_end, line), []).append((basis, conf, f.members, f.value))

    def _score(c):
        _b, conf, mems, _v = c
        meth = next((v for k, v in mems.items() if "Methodology" in k), "")
        return (_HOLDCO_RANK.get(conf, 9), len(mems), 0 if "Standardized" in meth else 1)

    def _pick(pool):
        """Best candidate by anchor (for cet1) or dimensional score."""
        return sorted(pool, key=_score)[0]

    out: dict[str, dict] = {}
    for period in sorted({p for (p, _l) in cand}):
        d: dict = {}
        # 1) Decide the CET1 basis: prefer the holding company, fall back to the
        #    bank subsidiary (for single-bank holdcos the filing often tags only
        #    the bank, and bank ≈ holdco). The FDIC anchor picks the right value
        #    within each basis and rejects minimums / tiny-sub outliers.
        cet1 = [c for c in cand.get((period, "cet1_ratio"), []) if 0.05 <= c[3] <= 0.60]
        chosen_basis = None
        for want in ("holdco", "bank"):
            pool = [c for c in cet1 if c[0] == want]
            if not pool:
                continue
            if anchor_cet1 is not None:
                inb = [c for c in pool if abs(c[3] * 100 - anchor_cet1) <= 6.0]
                if not inb:
                    continue
                best = min(inb, key=lambda c: abs(c[3] * 100 - anchor_cet1))
                d["_anchored"] = True
            else:
                best = _pick(pool)
            d["cet1_ratio"] = best[3]
            d["_confidence"] = best[1] or "bank"
            chosen_basis = want
            break
        # 2) Extract every other line from the SAME basis (never mix holdco CET1
        #    with bank Tier 1); fall back to holdco-class facts if that basis is
        #    absent for a line. CBLR banks have no CET1 — default to holdco.
        basis = chosen_basis or "holdco"
        for (p, line), lst in cand.items():
            if p != period or line == "cet1_ratio":
                continue
            pool = [c for c in lst if c[0] == basis] or [c for c in lst if c[0] == "holdco"]
            if not pool:
                continue
            best = _pick(pool)
            d[line] = best[3]
            prev = d.get("_confidence", "default")
            if _HOLDCO_RANK.get(best[1], 9) > _HOLDCO_RANK.get(prev, 9):
                d["_confidence"] = best[1] or prev
        if chosen_basis == "bank":
            d["_basis"] = "bank"   # provenance: bank-subsidiary basis, flagged
        if d:
            out[period] = d

    # Derive what the filing didn't tag directly (exact identities):
    #   RWA = CET1 capital ÷ CET1 ratio;  Tier 2 = Total capital − Tier 1.
    for d in out.values():
        if "rwa" not in d and d.get("cet1_ratio") and d.get("cet1_cap"):
            d["rwa"] = d["cet1_cap"] / d["cet1_ratio"]
        if "tier2_cap" not in d and d.get("total_cap") and d.get("t1_cap"):
            d["tier2_cap"] = d["total_cap"] - d["t1_cap"]
        # A bank with only a leverage ratio (no CET1) is on the Community Bank
        # Leverage Ratio election — CET1/RWA are legitimately not disclosed.
        d["_cblr"] = ("cet1_ratio" not in d and "lev_ratio" in d)
    return out


def fetch_facts(cik, forms=("10-K",)) -> tuple[dict | None, list[Fact]]:
    """Locate the latest filing among `forms`, fetch it, and parse its iXBRL.
    Returns (filing_meta, facts); ([], []) shape on failure is avoided —
    filing_meta is None when nothing is found."""
    meta = latest_filing(cik, forms)
    if not meta:
        return None, []
    html = _get(filing_url(meta["cik"], meta["accession"], meta["doc"]))
    return meta, parse_inline_xbrl(html)
