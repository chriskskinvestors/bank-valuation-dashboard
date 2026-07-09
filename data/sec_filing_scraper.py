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
from datetime import date

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
    return parse_inline_xbrl_documentset([html_bytes])


def _collect_contexts(root, contexts: dict) -> None:
    """Populate `contexts` (id -> (start, end, {dimension: member})) from one
    parsed document root. Merges in place so a document SET can share one pool."""
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


def _collect_facts(root, contexts: dict, facts: list) -> None:
    """Append the numeric iXBRL facts in one document root, resolving each
    contextRef against the shared `contexts` pool (which may have been populated
    from a DIFFERENT document -- large filers split contexts and facts apart)."""
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
        raw = raw.strip("()").replace(",", "").replace(" ", "").strip()
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


def parse_inline_xbrl_documentset(docs: list[bytes]) -> list[Fact]:
    """Parse iXBRL facts across a multi-document instance (a "document set").

    Large filers (USB, WFC, TFC) split the financial statements into a secondary
    document (e.g. usb-20251231_d2.htm) while the <xbrli:context> blocks stay in
    the primary document, so neither doc parses standalone -- the secondary's
    ~4,600 facts reference contexts defined in the primary. Collect ALL contexts
    first, then resolve EVERY document's facts against that shared pool. For a
    single-element list this is identical to the old single-doc parse (same root,
    same context ids, same fact order)."""
    from lxml import html as lhtml
    roots = [lhtml.fromstring(b) for b in docs if b]
    contexts: dict[str, tuple] = {}
    for root in roots:
        _collect_contexts(root, contexts)
    facts: list[Fact] = []
    for root in roots:
        _collect_facts(root, contexts, facts)
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
    "cet1_ratio":  re.compile(rf"^{_CET1}(Ratio|ToRiskWeightedAssets(Ratio)?)$", re.I),
    "t1_cap":      re.compile(r"^Tier(One|1)RiskBasedCapital$", re.I),
    "t1_ratio":    re.compile(r"^Tier(One|1)RiskBasedCapital(Ratio|ToRiskWeightedAssets(Ratio)?)$", re.I),
    "total_cap":   re.compile(r"^Capital$", re.I),
    "total_ratio": re.compile(r"^CapitalToRiskWeightedAssets(Ratio)?$", re.I),
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
        if d in ("ConsolidatedEntitiesAxis", "LegalEntityAxis",
                 "RegulatoryCapitalRequirementsForBanksAxis"):
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
        value = f.value
        # Some filers tag a ratio as the PERCENTAGE number (NBHC/UBSI: CET1 "14.9")
        # instead of the decimal (0.149). A real CET1/Tier-1/Total/leverage ratio
        # as a fraction never exceeds ~1.0, so a ratio value > 1 is percent-tagged —
        # normalize to a decimal so the anchor check + RWA reconstruction are right.
        if line.endswith("_ratio") and value and abs(value) > 1.0:
            value /= 100.0
        cand.setdefault((f.period_end, line), []).append((basis, conf, f.members, value))

    # Banks on the Advanced Approaches report each risk-based ratio/amount under
    # BOTH a Standardized and an Advanced methodology member. The Standardized
    # figure is the binding, headline-reported one (what FDIC/SNL show); Advanced
    # is supplemental. Drop the Advanced members wherever a Standardized one exists
    # for the same (period, line), so every capital line — ratio, capital, RWA —
    # comes from ONE consistent methodology. Without this, the FDIC CET1 anchor —
    # which for these names sits closer to the Advanced ratio (WFC: bank-sub 12.58%
    # vs Standardized 10.61% / Advanced 12.35%) — mis-selects the Advanced ratio.
    def _methodology(members):
        for v in members.values():
            m = v.split(":")[-1]
            if m == "StandardizedApproachMember":
                return "std"
            if m == "AdvancedApproachMember":
                return "adv"
        return None

    for key, lst in cand.items():
        if any(_methodology(c[2]) == "std" for c in lst):
            cand[key] = [c for c in lst if _methodology(c[2]) != "adv"]

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

    # Reconstruct each bank's capital table from ITS OWN reported components.
    # Every risk-based ratio shares one RWA (capital ÷ ratio), so derive RWA from
    # any tagged (capital, ratio) pair (they must agree), override a tagged RWA
    # that's an inconsistent sub-component (GBNY tagged $40M vs $306M implied,
    # MCHB $2.1B vs $13B), then fill any ratio/capital the filer reported only via
    # the others — exact identities, not guesses. WSBC tags CET1 capital + the
    # Tier-1/Total ratios but no CET1 ratio; this derives it rather than n/a.
    _PAIRS = (("cet1_cap", "cet1_ratio"), ("t1_cap", "t1_ratio"),
              ("total_cap", "total_ratio"))
    for d in out.values():
        implied = [d[c] / d[r] for c, r in _PAIRS if d.get(c) and d.get(r)]
        if implied and (max(implied) - min(implied) <= 0.01 * max(implied)):
            rwa = sum(implied) / len(implied)            # the agreed, reliable RWA
            if "rwa" not in d or abs(d["rwa"] - rwa) > 0.02 * rwa:
                d["rwa"] = rwa
        if d.get("rwa"):
            for c, r in _PAIRS:                          # fill gaps by exact identity
                if d.get(c) and not d.get(r):
                    d[r] = d[c] / d["rwa"]
                elif d.get(r) and not d.get(c):
                    d[c] = d[r] * d["rwa"]
        if "tier2_cap" not in d and d.get("total_cap") and d.get("t1_cap"):
            d["tier2_cap"] = d["total_cap"] - d["t1_cap"]
        # A bank with only a leverage ratio (no CET1) is on the Community Bank
        # Leverage Ratio election — CET1/RWA are legitimately not disclosed.
        d["_cblr"] = ("cet1_ratio" not in d and "lev_ratio" in d)

    # Regulatory-capital WALK (SNL "Regulatory Capital ($000)" panel). Attempt
    # the CET1 reconstruction per period from the filing's UNDIMENSIONED
    # balance-sheet tags and reconcile it to the already-extracted (anchored)
    # CET1 capital. The walk is attached ONLY where it reconciles — banks fold
    # CECL-transition, DTA/MSR-threshold and AOCI opt-out adjustments into CET1
    # that the filing doesn't tag separately, so n/a is the honest result for
    # most filers (see _build_capital_walk). Bank-basis / CBLR periods skip it.
    for period, d in out.items():
        if d.get("_basis") == "bank" or d.get("_cblr"):
            continue
        walk, ok = _build_capital_walk(_undimensioned(facts, period),
                                       d.get("cet1_cap"))
        d["_walk"] = walk
        d["_walk_reconciles"] = ok
    return out


# ── Holdco regulatory-capital WALK reconstruction ───────────────────────────
# The headline ratios/amounts are tagged inside the capital table; the WALK
# (common equity → less intangibles → ± AOCI → CET1) usually is NOT tagged as a
# machine-readable reconciliation. We rebuild it from the filing's UNDIMENSIONED
# (consolidated) balance-sheet concepts and show it ONLY when the CET1 build
# reconciles to the extracted CET1 capital — never via a plug. Each value is an
# exact filing tag; the build either reconciles or the walk renders n/a.
_WALK_CONCEPTS = {
    "total_equity":      ("StockholdersEquity",),
    "preferred":         ("PreferredStockValue",
                          "PreferredStockIncludingAdditionalPaidInCapitalNetOfDiscount"),
    "goodwill":          ("Goodwill",),
    "other_intangibles": ("IntangibleAssetsNetExcludingGoodwill",
                          "FiniteLivedIntangibleAssetsNet"),
    "aoci":              ("AccumulatedOtherComprehensiveIncomeLossNetOfTax",),
    "subordinated_debt": ("SubordinatedDebt", "SubordinatedLongTermDebt",
                          "SubordinatedBorrowings"),
}


def _undimensioned(facts: list[Fact], period: str) -> dict:
    """{concept_local: value} for facts at `period` carrying NO dimensional
    member — the consolidated, holdco balance-sheet total. First write wins, so
    a dimensional breakdown (e.g. StockholdersEquity by PreferredStockMember)
    can never be mistaken for the undimensioned total."""
    out: dict[str, float] = {}
    for f in facts:
        if f.period_end != period or f.members:
            continue
        out.setdefault(f.concept.split(":")[-1], f.value)
    return out


def _build_capital_walk(undim: dict, cet1_cap: float | None) -> tuple[dict, bool]:
    """Reconstruct the SNL regulatory-capital walk and reconcile its CET1 build
    to the extracted CET1 capital.

    walk maps each component to its tagged value (None if the filing doesn't tag
    it). Returns (walk, reconciles); reconciles is True ONLY when common equity
    and goodwill are tagged AND common equity − intangibles (with AOCI either
    retained or removed) lands within 1% of cet1_cap — never via a residual
    plug. 'aoci_treatment' records which election reconciled ('included' /
    'excluded'), so the UI can show the AOCI step honestly."""
    def pick(key):
        for c in _WALK_CONCEPTS[key]:
            if c in undim:
                return undim[c]
        return None

    total_equity = pick("total_equity")
    preferred = pick("preferred")
    goodwill = pick("goodwill")
    other_intang = pick("other_intangibles")
    aoci = pick("aoci")

    walk = {
        "common_equity": None, "preferred": preferred,
        "goodwill": goodwill, "other_intangibles": other_intang,
        "aoci": aoci, "subordinated_debt": pick("subordinated_debt"),
        "intangibles": None, "aoci_treatment": None,
    }
    if total_equity is None or goodwill is None or cet1_cap is None:
        return walk, False

    common_equity = total_equity - (preferred or 0.0)
    intangibles = goodwill + (other_intang or 0.0)
    walk["common_equity"] = common_equity
    walk["intangibles"] = intangibles

    # AOCI opt-in (retained in CET1) vs opt-out (removed) — the filing rarely
    # tags the election, so try both and accept whichever reconciles.
    base = common_equity - intangibles
    tol = max(cet1_cap * 0.01, 5e6)
    for treat, built in (("included", base), ("excluded", base - (aoci or 0.0))):
        if abs(built - cet1_cap) <= tol:
            walk["aoci_treatment"] = treat
            return walk, True
    return walk, False


# Below this many facts in the primary document, the filing is treated as a
# possible multi-document instance and the companion docs are consulted. A real
# 10-K/10-Q tags hundreds–thousands of facts, so a single-document filing never
# trips this; only split filers (USB: 2 facts in the primary) do.
_MULTIDOC_FACT_THRESHOLD = 50


def _filing_base(cik, accession) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/"


def _instance_documents(base: str) -> list[str]:
    """The htm document(s) of the iXBRL instance, from the MetaLinks 'instance'
    key — a space-joined doc list ('usb-20251231.htm usb-20251231_d2.htm'). One
    entry for a normal single-document filing."""
    try:
        ml = json.loads(_get(base + "MetaLinks.json"))
        key = next(iter(ml.get("instance", {})), "")
        return [d for d in key.split() if d.endswith((".htm", ".html"))]
    except Exception:
        return []


def instance_facts(meta: dict) -> list[Fact]:
    """All iXBRL facts for a filing, transparently handling the multi-document
    case. Parses the primary document (the fast common path); if it yields almost
    no facts the filing is a document SET whose statements live in a secondary
    document (USB/WFC/TFC/BK/…) while the contexts stay in the primary, so we
    fetch every instance document and parse them together. Single-document filings
    — the overwhelming majority — never take the fallback and parse exactly as
    before, so capital / fair-value / composition extraction is unchanged for them
    and simply gains the previously-unparseable large filers."""
    primary = _get(filing_url(meta["cik"], meta["accession"], meta["doc"]))
    facts = parse_inline_xbrl(primary)
    if len(facts) >= _MULTIDOC_FACT_THRESHOLD:
        return facts
    base = _filing_base(meta["cik"], meta["accession"])
    docs = _instance_documents(base)
    if len(docs) <= 1:
        return facts                       # genuinely single-document; nothing more
    blobs = [primary if d == meta["doc"] else _get(base + d) for d in docs]
    return parse_inline_xbrl_documentset(blobs)


def _fye_month_from_facts(facts: list[Fact]) -> str | None:
    """The filer's fiscal-year-end month ("01".."12"), derived from the filing's
    OWN annual-duration facts: the modal month among ~one-year period-ends. This is
    the filing's authoritative fiscal calendar (a June filer's annual facts all end
    in June), so it correctly identifies the FY-end month for off-cycle filers
    (AX = Jun, WAFD/CASH = Sep) instead of assuming December. None when the filing
    tags no annual duration."""
    from collections import Counter
    months: Counter = Counter()
    for f in facts:
        if f.period_start and 330 <= _days(f.period_start, f.period_end) <= 400:
            months[f.period_end[5:7]] += 1
    if not months:
        return None
    return months.most_common(1)[0][0]


def _fye_month_for(meta: dict) -> str | None:
    """Cached fiscal-year-end month for one filing (keyed per accession, sharing the
    parsed-facts cost with the extractors). Used to gate FY-end-only multi-year
    stitching on the filer's real fiscal calendar rather than a hardcoded December.
    None (→ caller falls back to December) when it cannot be derived."""
    from data import cache
    ckey = f"fyemonth:v1:{meta['accession']}"
    mon = cache.get(ckey)
    if mon is None:
        try:
            mon = _fye_month_from_facts(instance_facts(meta)) or ""
            try:
                cache.put(ckey, mon)
            except Exception:
                pass
        except Exception as e:
            print(f"[sec_scraper] fye-month failed for cik {meta.get('cik')}: "
                  f"{type(e).__name__}: {e}")
            mon = ""
    return mon or None


def fetch_facts(cik, forms=("10-K",)) -> tuple[dict | None, list[Fact]]:
    """Locate the latest filing among `forms`, fetch it, and parse its iXBRL
    (multi-document aware). Returns (filing_meta, facts); filing_meta is None when
    nothing is found."""
    meta = latest_filing(cik, forms)
    if not meta:
        return None, []
    return meta, instance_facts(meta)


def _fdic_cet1(cert) -> float | None:
    """Latest FDIC bank-sub CET1 ratio (IDT1CER, %) — the holdco anchor."""
    if not cert:
        return None
    url = (f"https://banks.data.fdic.gov/api/financials?filters=CERT:{cert}"
           f"&fields=IDT1CER&sort_by=REPDTE&sort_order=DESC&limit=1&format=json")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=15) as r:
            d = json.load(r)["data"]
        return d[0]["data"].get("IDT1CER") if d else None
    except Exception:
        return None


def _has_capital(cap: dict) -> bool:
    return bool(cap) and any("cet1_ratio" in d or d.get("_cblr") for d in cap.values())


def _holdco_capital_extract_cached(meta: dict, anchor: float | None) -> dict:
    """extract_holdco_capital for one filing, cached per accession. {} on a
    fetch/parse failure (never cache a transient exception). The FDIC anchor is
    NOT part of the key — it is the bank's (stable) latest CET1, used only to
    disambiguate within-band; the same parsed filing is reused across calls.

    Version the key (v3): the prior v2 entries predate the multi-year stitch and
    are abandoned so the freshly-extracted capital is always served, never stale."""
    from data import cache
    ckey = f"holdco_cap:v3:{meta['accession']}"
    cap = cache.get(ckey)
    if cap is None:
        try:
            cap = extract_holdco_capital(instance_facts(meta), anchor_cet1=anchor)
            try:
                cache.put(ckey, cap)
            except Exception:
                pass
        except Exception as e:
            print(f"[sec_scraper] holdco capital failed for cik {meta.get('cik')}: "
                  f"{type(e).__name__}: {e}")
            cap = {}
    return cap or {}


def holdco_capital_for(cik, cert=None) -> dict | None:
    """Cached holdco regulatory capital for a company, stitched across its own SEC
    filings into a multi-year window.

    The freshest period comes from the latest 10-Q (timeliest) when it tags the
    full capital table, else the latest 10-K (many banks tag capital only
    annually). The FISCAL-YEAR history then fills from the recent 10-Ks: each 10-K
    tags its FY-end + the prior FY-end, so a handful of filings cover up to
    _MAX_YEARS fiscal years — without this, the Financial-Highlights capital rows
    populated only the latest filing's 1-2 years (FY2021-2023 blank for ABCB).

    FY-ends are taken newest-first and de-duplicated: a year shared by two filings
    keeps the NEWER filing's value (filings agree on shared comparatives; newest is
    the as-finally-reported figure). Every filing is anchored to the bank's FDIC
    CET1 and reconcile-gated by extract_holdco_capital — the stitch only widens the
    window, it never loosens a gate. The latest-filing periods are kept as-is
    (including a 10-Q quarter-end), so capital_dynamics still shows the timeliest
    quarter. Returns {"meta": <latest filing>, "capital": {period: {...}}} or None.
    """
    if not cik:
        return None
    anchor = _fdic_cet1(cert)
    # 1) Freshest filing carrying the table (timeliest quarter / latest FY). Keep its
    #    NEWEST period (the timeliest figure — a 10-Q's quarter-end or the latest
    #    FY-end) plus its FY-end columns; drop any other stub quarter so the annual
    #    series stays clean (a non-December filer's 10-K can carry an off-cycle stub).
    latest_meta = None
    capital: dict = {}
    for forms in (("10-Q",), ("10-K",)):
        meta = latest_filing(cik, forms)
        if not meta:
            continue
        cap = _holdco_capital_extract_cached(meta, anchor)
        if _has_capital(cap):
            latest_meta = meta
            fye = _fye_month_for(meta) or "12"
            newest = max(cap)
            capital = {p: d for p, d in cap.items()
                       if p == newest or p[5:7] == fye}
            break
    if latest_meta is None:
        return None
    # 2) Stitch the FY-end history from the recent 10-Ks (newest first). Keep only
    #    FY-end periods (the filer's real fiscal-year-end month) so stub quarters of
    #    a 10-K never enter the annual series; never overwrite a period the latest
    #    filing (or a newer 10-K) already supplied.
    for meta in _list_10k_filings(cik, _MAX_YEARS):
        if meta["accession"] == latest_meta["accession"]:
            cap = capital                      # already extracted (latest is this 10-K)
        else:
            cap = _holdco_capital_extract_cached(meta, anchor)
        if not _has_capital(cap):
            continue
        fye = _fye_month_for(meta) or "12"
        for period in sorted(cap, reverse=True):
            if period[5:7] != fye or period in capital:
                continue
            capital[period] = cap[period]
        if len({p[:4] for p in capital}) >= _MAX_YEARS:
            break
    if not _has_capital(capital):
        return None
    return {"meta": latest_meta, "capital": capital}


# ── Fair-value hierarchy (ASC 820) extraction ───────────────────────────────
# The recurring fair-value hierarchy total is tagged Assets/Liabilities-
# FairValueDisclosure, split across FairValueByFairValueHierarchyLevelAxis
# (Level 1/2/3). The SAME concept also tags the NONRECURRING table (collateral-
# dependent loans, OREO) and per-instrument sub-rows, which carry a Nonrecurring
# frequency member or a FinancialInstrument/ValuationTechnique breakdown member.
# So the clean recurring TOTAL is the fact whose only dimensional members are the
# hierarchy level and (optionally) a Recurring frequency. Verified across 7
# filers (tools/_probe_fairvalue.py): ABCB/FITB/CFG/WSFS reconcile L1+L2+L3 ==
# grand exactly; a dealer (TFC) shows a derivative-netting delta surfaced as a
# reconciling line; filers tagging only per-instrument sub-rows (RF) or omitting
# the rollup (FFIN) yield n/a — never a guessed total.
_FV_HIER_AXIS = "FairValueByFairValueHierarchyLevelAxis"
_FV_FREQ_AXIS = "FairValueByMeasurementFrequencyAxis"
# Concepts per side, highest priority first (an explicit ...Recurring concept
# beats the base concept when a filer tags both — rare, but keep it deterministic).
_FV_TOTAL_CONCEPTS = {
    "assets": ("AssetsFairValueDisclosureRecurring", "AssetsFairValueDisclosure"),
    "liabilities": ("LiabilitiesFairValueDisclosureRecurring", "LiabilitiesFairValueDisclosure"),
}


def _fv_level(members: dict) -> str | None:
    """'L1'/'L2'/'L3' if the fact carries a fair-value hierarchy level member."""
    for dim, mem in members.items():
        if dim.split(":")[-1] == _FV_HIER_AXIS:
            m = mem.split(":")[-1]
            for n in ("1", "2", "3"):
                if f"FairValueInputsLevel{n}" in m:
                    return f"L{n}"
    return None


def _fv_clean_total(members: dict) -> bool:
    """True for a clean recurring-table TOTAL row: the only members allowed are
    the hierarchy-level axis and a Recurring frequency. A Nonrecurring frequency
    or ANY other breakdown axis (FinancialInstrument, ValuationTechnique,
    portfolio segment, …) marks a sub-detail / nonrecurring row — excluded."""
    for dim, mem in members.items():
        d, m = dim.split(":")[-1], mem.split(":")[-1]
        if d == _FV_HIER_AXIS:
            continue
        if d == _FV_FREQ_AXIS:
            if "Nonrecurring" in m:
                return False
            continue
        return False
    return True


def _fv_same(a: float, b: float) -> bool:
    """Two candidate values are the SAME measurement (rounding/restatement jitter),
    not two different table rows."""
    return abs(a - b) <= max(abs(a), abs(b)) * 0.01 + 1e5


def _fv_distinct(vals: list[float]) -> list[float]:
    """Collapse near-equal candidates (the same number tagged twice) to the
    materially-distinct set. >1 entry means two different tables tagged the slot."""
    out: list[float] = []
    for v in vals:
        if not any(_fv_same(v, u) for u in out):
            out.append(v)
    return out


def _undimensioned_total(facts: list[Fact], concept: str, period: str) -> float | None:
    """The balance-sheet total (us-gaap:<concept> with NO dimensional members) at
    `period` — the denominator for the disclosure-table sanity check."""
    val = None
    for f in facts:
        if (f.concept.split(":")[-1] == concept and not f.members
                and f.period_end == period):
            val = f.value
    return val


def extract_fair_value(facts: list[Fact]) -> dict:
    """{period_end: {"assets": {...}, "liabilities": {...}}} — the recurring
    ASC 820 fair-value hierarchy from a filing's iXBRL.

    Each side carries l1/l2/l3 (as tagged), total (= sum of tagged levels), grand
    (the filer's tagged grand total, if any), netting (grand − total, the
    derivative/collateral reconciling item), l3_pct (L3 ÷ total) and _reconciles
    (whether the level sum ties the tagged grand within tolerance). A side with no
    clean level total is omitted (n/a) — never summed from components.

    GUARDS against the ASC 825 fair-value-OF-financial-instruments disclosure
    table, which many filers tag under the SAME concept + hierarchy axis as the
    ASC 820 recurring table (so it passes _fv_clean_total) but whose Level 3 is the
    LOAN book — billions, e.g. HWBK $1.5B, NWBI $12.5B — which must never surface
    as recurring mark-to-model. A side is rendered n/a when: (1) a level slot has
    materially-different duplicate facts (two tables under one concept, can't
    disambiguate); (2) a tagged grand sits far below the level sum (not recurring
    netting but a different table); or (3) the FV total approaches the whole
    balance sheet (the disclosure table spans loans+deposits)."""
    prio = {c: (side, i)
            for side, cs in _FV_TOTAL_CONCEPTS.items()
            for i, c in enumerate(cs)}
    # side -> period -> slot -> [(priority, value)] — keep ALL candidates so a
    # two-table conflation under one concept is visible (duplicate values), not
    # silently collapsed.
    grab: dict = {}
    for f in facts:
        hit = prio.get(f.concept.split(":")[-1])
        if hit is None or not _fv_clean_total(f.members):
            continue
        side, p = hit
        slot = _fv_level(f.members) or "grand"
        grab.setdefault(side, {}).setdefault(f.period_end, {}).setdefault(slot, []).append((p, f.value))

    _bs = {"assets": "Assets", "liabilities": "Liabilities"}
    out: dict = {}
    for side, byperiod in grab.items():
        for period, slots in byperiod.items():
            def _pick(slot):  # noqa: B023 — bound per-iteration below
                cs = slots.get(slot, [])
                if not cs:
                    return []
                best_p = min(p for p, _ in cs)            # prefer ...Recurring concept
                return _fv_distinct([v for p, v in cs if p == best_p])

            lv = {s: _pick(s) for s in ("L1", "L2", "L3")}
            if any(len(v) > 1 for v in lv.values()):
                continue                                  # (1) two tables, one concept → n/a
            levels = {s: v[0] for s, v in lv.items() if v}
            if not levels:
                continue
            total = sum(levels.values())
            gs = _pick("grand")
            grand = gs[0] if len(gs) == 1 else (
                min(gs, key=lambda g: abs(g - total)) if gs else None)
            if grand is not None and abs(total) > 5e7 and abs(grand) < 0.35 * abs(total):
                continue                                  # (2) grand ≪ levels → not recurring netting
            bs = _undimensioned_total(facts, _bs[side], period)
            if bs and abs(total) > 0.85 * abs(bs):
                continue                                  # (3) ≈ whole balance sheet → disclosure table
            d = {
                "l1": levels.get("L1"), "l2": levels.get("L2"), "l3": levels.get("L3"),
                "total": total, "grand": grand,
                "l3_pct": (levels.get("L3", 0.0) / total) if total else None,
            }
            if grand is None:
                d["netting"], d["_reconciles"] = None, True
            else:
                d["netting"] = grand - total
                d["_reconciles"] = abs(grand - total) <= max(abs(total) * 0.01, 5e6)
            out.setdefault(period, {})[side] = d
    return out


def fair_value_for(cik) -> dict | None:
    """Cached recurring fair-value hierarchy for a company from its own latest SEC
    filing. Tries the timeliest 10-Q first, falls back to the 10-K. Cached by
    accession + version (an empty parse is cached too). Returns
    {"meta": {...}, "fair_value": {period: {...}}} or None."""
    if not cik:
        return None
    from data import cache
    for forms in (("10-Q",), ("10-K",)):
        meta = latest_filing(cik, forms)
        if not meta:
            continue
        # v2: cache only a SUCCESSFUL parse. A genuine empty (no rollup tagged) is
        # a valid result and is cached; a fetch/parse EXCEPTION is transient and
        # is NEVER cached, or one SEC hiccup would pin the company to an older
        # filing (10-Q→10-K fallback) until the cache version bumps.
        ckey = f"fair_value:v2:{meta['accession']}"
        fv = cache.get(ckey)
        if fv is None:
            try:
                fv = extract_fair_value(instance_facts(meta))
                try:
                    cache.put(ckey, fv)
                except Exception:
                    pass
            except Exception as e:
                print(f"[sec_scraper] fair value failed for cik {cik}: {type(e).__name__}: {e}")
                fv = {}
        if fv:
            return {"meta": meta, "fair_value": fv}
    return None


def _fair_value_extract_cached(meta: dict) -> dict:
    """extract_fair_value for one filing, cached per accession (shared key with
    fair_value_for). {} on failure (never cache a transient None)."""
    from data import cache
    ckey = f"fair_value:v2:{meta['accession']}"
    fv = cache.get(ckey)
    if fv is None:
        try:
            fv = extract_fair_value(instance_facts(meta))
            try:
                cache.put(ckey, fv)
            except Exception:
                pass
        except Exception as e:
            print(f"[sec_scraper] fair value failed for cik {meta.get('cik')}: "
                  f"{type(e).__name__}: {e}")
            fv = {}
    return fv or {}


def fair_value_multiyear_for(cik, n_years: int = 5) -> dict | None:
    """Multi-year recurring ASC 820 fair-value hierarchy, stitched FY-end-only from
    the bank's recent 10-Ks. Each 10-K tags its FY-end (and usually the prior
    FY-end), so a handful of filings yield up to `n_years` fiscal year-ends.
    Returns {"meta": <latest 10-K>, "filings": [...], "fair_value": {fy_period:
    {...}}} (newest-first periods), or None when no FY-end hierarchy is tagged.

    Each period entry is exactly what extract_fair_value produced — i.e. already
    reconcile-gated by the extractor (the level sum ties the filer's tagged grand
    within tolerance, the disclosure-table guards rejected; a side with no clean
    level total was omitted). Periods are de-duplicated keeping the value from the
    NEWEST filing that tagged them (filings agree on shared comparatives; newest is
    the as-finally-reported figure). Fabricates nothing — drops a period whose
    levels don't reconcile to a total."""
    if not cik:
        return None
    from data.sec_statements import _recent_10k_metas
    # ~2 FY-ends per 10-K → reach back enough filings to cover n_years.
    metas = _recent_10k_metas(cik, n_years)
    if not metas:
        return None
    fair_value: dict = {}
    used_filings: list = []
    for m in metas:                                  # newest-first
        fv = _fair_value_extract_cached(m)
        fye = _fye_month_for(m) or "12"              # filer's real FY-end month
        contributed = False
        for period in sorted(fv.keys(), reverse=True):
            if period[5:7] != fye:                   # FY-ends only (skip stub quarters)
                continue
            if period in fair_value:                 # newer filing already supplied it
                continue
            sides = fv[period]
            # Independently gate each period: keep only sides with a clean level
            # total (l1+l2+l3, computed by the extractor only from tagged levels —
            # never a component-summed guess). The extractor already enforces the
            # ASC-820 reconciliation: it omits a side with no clean level total and
            # rejects the disclosure-table conflations. A tagged grand total that
            # differs from the level sum (dealer counterparty/collateral netting) is
            # a VALID reconciling item (carried as `netting`, flagged via
            # _reconciles=False) — kept, not dropped. Drop the whole period if no
            # side has a total (n/a, never a carried-forward guess).
            kept = {sk: sv for sk, sv in sides.items()
                    if sv and sv.get("total") is not None}
            if not kept:
                continue
            fair_value[period] = kept
            contributed = True
        if contributed:
            used_filings.append(m)
        # Stop once we have the requested span of fiscal years.
        if len({p[:4] for p in fair_value}) >= n_years:
            break
    if not fair_value:
        return None
    return {"meta": metas[0], "filings": used_filings, "fair_value": fair_value}


# ── AFS / HTM debt-securities summary ────────────────────────────────────────
# The investment-securities footnote tags, undimensioned, an amortized-cost →
# fair-value bridge for each portfolio: amortized cost + gross unrealized/
# unrecognized gain − loss = fair value. This is the AOCI / "underwater bonds"
# story (HTM unrealized losses never hit the balance sheet). Concept names vary,
# so each slot has a priority list (first tagged wins). A portfolio renders only
# when amortized cost AND fair value are both tagged; the gain/loss SPLIT is shown
# only when it reconciles the bridge (else just the net, = fair value − amortized
# cost, which is an identity and always safe).
_SEC_CONCEPTS = {
    "afs": {
        "ac": ("DebtSecuritiesAvailableForSaleAmortizedCostExcludingAccruedInterestAfterAllowanceForCreditLoss",
               "DebtSecuritiesAvailableForSaleAmortizedCostExcludingAccruedInterestIncludingPortfolioLevelBasisAdjustmentsAfterAllowanceForCreditLoss",
               "DebtSecuritiesAvailableForSaleAmortizedCostExcludingAccruedInterestAndPortfolioLevelBasisAdjustmentsAfterAllowanceForCreditLoss",
               "DebtSecuritiesAvailableForSaleAmortizedCostExcludingAccruedInterestBeforeAllowanceForCreditLoss",
               "AvailableForSaleDebtSecuritiesAmortizedCostBasis",
               "DebtSecuritiesAvailableForSaleAmortizedCostIncludingPortfolioLevelBasisAdjustments",
               "DebtSecuritiesAvailableForSaleAmortizedCostAfterAllowanceForCreditLoss",
               "DebtSecuritiesAvailableForSaleAmortizedCostExcludingAccruedInterest",
               "AvailableForSaleDebtAndEquitySecuritiesAmortizedCostBasis",
               "DebtSecuritiesAvailableForSaleAmortizedCost",
               "AvailableForSaleSecuritiesAmortizedCost",
               "AvailableForSaleSecuritiesAmortizedCosts"),
        "fv": ("DebtSecuritiesAvailableForSaleExcludingAccruedInterest",
               "DebtSecuritiesAvailableForSale",
               "AvailableForSaleSecuritiesDebtSecurities",
               "AvailableForSaleSecurities"),
        "ug": ("AvailableForSaleDebtSecuritiesAccumulatedGrossUnrealizedGainBeforeTax",
               "AvailableForSaleSecuritiesGrossUnrealizedGains",
               "AvailableForSaleSecuritiesAccumulatedGrossUnrealizedGainBeforeTax"),
        "ul": ("AvailableForSaleDebtSecuritiesAccumulatedGrossUnrealizedLossBeforeTax",
               "AvailableForSaleSecuritiesGrossUnrealizedLosses",
               "AvailableForSaleSecuritiesAccumulatedGrossUnrealizedLossBeforeTax"),
    },
    "htm": {
        "ac": ("DebtSecuritiesHeldToMaturityExcludingAccruedInterestAfterAllowanceForCreditLoss",
               "DebtSecuritiesHeldToMaturityAmortizedCostAfterAllowanceForCreditLoss",
               "HeldToMaturitySecurities"),
        "fv": ("HeldToMaturitySecuritiesFairValue",
               "DebtSecuritiesHeldToMaturityFairValue"),
        "ug": ("HeldToMaturitySecuritiesAccumulatedUnrecognizedHoldingGain",
               "DebtSecuritiesHeldToMaturityAccumulatedUnrecognizedGainBeforeTax"),
        "ul": ("HeldToMaturitySecuritiesAccumulatedUnrecognizedHoldingLoss",
               "DebtSecuritiesHeldToMaturityAccumulatedUnrecognizedLossBeforeTax"),
    },
}


def _sec_pick(facts: list[Fact], concepts: tuple, period: str):
    """First undimensioned fact at `period` whose concept matches the priority
    list (earlier = preferred). None if none tagged."""
    by_concept: dict = {}
    for f in facts:
        c = f.concept.split(":")[-1]
        if c in concepts and not f.members and f.period_end == period:
            by_concept.setdefault(c, f.value)
    for c in concepts:
        if c in by_concept:
            return by_concept[c]
    return None


def _sec_candidates(facts: list[Fact], concepts: tuple, period: str) -> list:
    """All undimensioned (priority_index, value) for the concept list at `period`,
    so the amortized-cost slot can reject a higher-priority concept that tags a
    FRAGMENT (e.g. FMCB tags ...AmortizedCostAfterAllowanceForCreditLoss = $69M
    while the real held-to-maturity book is $708M) in favour of one that actually
    bridges to fair value."""
    out = []
    for f in facts:
        c = f.concept.split(":")[-1]
        if c in concepts and not f.members and f.period_end == period:
            out.append((concepts.index(c), f.value))
    return sorted(out)


def extract_securities(facts: list[Fact]) -> dict:
    """{period_end: {"afs": {...}, "htm": {...}}} — the as-reported debt-securities
    amortized-cost → fair-value bridge from a filing's iXBRL.

    Each portfolio carries amortized_cost, fair_value, net_unrealized (fair_value −
    amortized_cost, the identity), unrealized_gain / unrealized_loss (only when the
    tagged split reconciles the bridge within 1% — else None), underwater_pct
    (net_unrealized ÷ amortized_cost; negative = below cost) and _reconciles. A
    portfolio with no tagged amortized cost OR no fair value is omitted (n/a),
    never a guessed total."""
    # Collect the candidate periods from any AFS/HTM amortized-cost or fair-value tag.
    periods: set = set()
    wanted = {c for port in _SEC_CONCEPTS.values()
              for slot in ("ac", "fv") for c in port[slot]}
    for f in facts:
        if f.concept.split(":")[-1] in wanted and not f.members:
            periods.add(f.period_end)
    out: dict = {}
    for period in periods:
        for port, slots in _SEC_CONCEPTS.items():
            fv = _sec_pick(facts, slots["fv"], period)
            if fv is None:
                continue
            ug = _sec_pick(facts, slots["ug"], period)
            ul = _sec_pick(facts, slots["ul"], period)
            # Pick the highest-priority amortized-cost candidate that either bridges
            # to fair value (gross gain/loss ties) OR sits within a plausible net
            # band — rejecting a fragment that yields a nonsensical bridge.
            ac = None
            for _, v in _sec_candidates(facts, slots["ac"], period):
                if not v:
                    continue
                bridge = (ug is not None and ul is not None
                          and abs((v + ug - ul) - fv) <= max(abs(fv) * 0.01, 5e6))
                if bridge or (-0.50 <= (fv - v) / v <= 0.20):
                    ac = v
                    break
            if ac is None:
                continue
            net = fv - ac
            reconciles = (ug is not None and ul is not None
                          and abs((ac + ug - ul) - fv) <= max(abs(fv) * 0.01, 5e6))
            d = {
                "amortized_cost": ac, "fair_value": fv, "net_unrealized": net,
                "unrealized_gain": ug if reconciles else None,
                "unrealized_loss": ul if reconciles else None,
                "underwater_pct": (net / ac) if ac else None,
                "_reconciles": reconciles,
            }
            out.setdefault(period, {})[port] = d
    return out


def securities_for(cik) -> dict | None:
    """Cached AFS/HTM debt-securities summary for a company from its own latest SEC
    filing (timeliest 10-Q, then 10-K). Returns
    {"meta": {...}, "securities": {period: {...}}} or None."""
    if not cik:
        return None
    from data import cache
    for forms in (("10-Q",), ("10-K",)):
        meta = latest_filing(cik, forms)
        if not meta:
            continue
        ckey = f"securities:v1:{meta['accession']}"
        sec = cache.get(ckey)
        if sec is None:
            try:
                sec = extract_securities(instance_facts(meta))
                try:
                    cache.put(ckey, sec)
                except Exception:
                    pass
            except Exception as e:
                print(f"[sec_scraper] securities failed for cik {cik}: {type(e).__name__}: {e}")
                sec = {}
        if sec:
            return {"meta": meta, "securities": sec}
    return None


def _securities_extract_cached(meta: dict) -> dict:
    """extract_securities for one filing, cached per accession (shared key with
    securities_for). {} on failure (never cache a transient None)."""
    from data import cache
    ckey = f"securities:v1:{meta['accession']}"
    sec = cache.get(ckey)
    if sec is None:
        try:
            sec = extract_securities(instance_facts(meta))
            try:
                cache.put(ckey, sec)
            except Exception:
                pass
        except Exception as e:
            print(f"[sec_scraper] securities failed for cik {meta.get('cik')}: "
                  f"{type(e).__name__}: {e}")
            sec = {}
    return sec or {}


def securities_multiyear_for(cik, n_years: int = 5) -> dict | None:
    """Multi-year AFS/HTM amortized-cost → fair-value bridge, stitched FY-end-only
    from the bank's recent 10-Ks. Each 10-K tags its FY-end + the prior FY-end, so
    a handful of filings yield up to `n_years` fiscal year-ends. Returns
    {"meta": <latest 10-K>, "filings": [...], "securities": {fy_period: {...}}}
    (newest-first periods), or None when no FY-end portfolio is tagged.

    Each period/portfolio entry is exactly what extract_securities produced — i.e.
    already reconcile-gated: the amortized-cost candidate had to bridge fair value
    (or fall in a plausible net band), and the gross gain/loss split is carried
    only when it ties the bridge (_reconciles). Periods are de-duplicated keeping
    the value from the NEWEST filing that tagged them (filings agree on shared
    comparatives; newest is the as-finally-reported figure)."""
    if not cik:
        return None
    from data.sec_statements import _recent_10k_metas
    # ~2 FY-ends per 10-K → reach back enough filings to cover n_years.
    metas = _recent_10k_metas(cik, n_years)
    if not metas:
        return None
    securities: dict = {}
    used_filings: list = []
    for m in metas:                                  # newest-first
        sec = _securities_extract_cached(m)
        fye = _fye_month_for(m) or "12"              # filer's real FY-end month
        contributed = False
        for period in sorted(sec.keys(), reverse=True):
            if period[5:7] != fye:                   # FY-ends only (skip stub quarters)
                continue
            if period in securities:                 # newer filing already supplied it
                continue
            securities[period] = sec[period]
            contributed = True
        if contributed:
            used_filings.append(m)
        # Stop once we have the requested span of fiscal years.
        if len({p[:4] for p in securities}) >= n_years:
            break
    if not securities:
        return None
    return {"meta": metas[0], "filings": used_filings, "securities": securities}


# ── Credit quality / allowance (ACL) ─────────────────────────────────────────
# The CECL allowance and asset-quality figures the loan footnote tags
# undimensioned: allowance for credit losses on loans, gross/net loans, nonaccrual
# loans, the charge-off / recovery flows and the provision. Reconcile-gated:
# net loans + allowance must tie gross loans, or the loans/ACL pairing is rejected
# (n/a, never a guessed ratio). The loan-only ACL is preferred over the combined
# "...AndOffBalanceSheetCreditLossLiability..." (which folds in the unfunded-
# commitment reserve) so the coverage ratio is allowance-on-loans ÷ loans.
_CQ_CONCEPTS = {
    "acl": ("FinancingReceivableAllowanceForCreditLossExcludingAccruedInterest",
            "FinancingReceivableAndNetInvestmentInLeaseAllowanceForCreditLossExcludingAccruedInterest",
            "FinancingReceivableAllowanceForCreditLosses",
            "LoansReceivableAllowanceForLoanLosses",
            "LoansAndLeasesReceivableAllowance",
            "AllowanceForLoanAndLeaseLossesRealEstate"),
    "loans_gross": ("FinancingReceivableExcludingAccruedInterestBeforeAllowanceForCreditLoss",
                    "FinancingReceivableAndNetInvestmentInLeaseExcludingAccruedInterestBeforeAllowanceForCreditLoss",
                    "FinancingReceivableCoveredAndNotCoveredBeforeAllowanceForCreditLoss",
                    "FinancingReceivableExcludingAccruedInterestBeforeAllowanceForCreditLossFeeAndLoanInProcess",
                    "FinancingReceivableExcludingAccruedInterestBeforeAllowanceForCreditLossFeeAfterLoanInProcess",
                    "FinancingReceivableBeforeAllowanceForCreditLossAndFee",
                    "FinancingReceivableBeforeAllowanceForCreditLoss",
                    "LoansAndLeasesReceivableBeforeFeeGross",
                    "LoansReceivableHeldForInvestmentBeforeAllowanceForLoanLossesAndDeferredFeesAndPremiums",
                    "NotesReceivableGross",
                    "LoansReceivableGrossCarryingAmount",
                    "LoansAndLeasesReceivableGrossCarryingAmount",
                    "LoansAndLeasesReceivableGrossCarryingAmount1",
                    "LoansAndLeasesReceivablesGross"),
    "loans_net": ("FinancingReceivableExcludingAccruedInterestAfterAllowanceForCreditLoss",
                  "FinancingReceivableAndNetInvestmentInLeaseExcludingAccruedInterestAfterAllowanceForCreditLoss",
                  "FinancingReceivableExcludingAccruedInterestAfterAllowanceForCreditLossFeeAndLoanInProcess",
                  "FinancingReceivableExcludingAccruedInterestAfterAllowanceForCreditLossFeeAfterLoanInProcess",
                  "FinancingReceivableAfterAllowanceForCreditLossAndFee",
                  "FinancingReceivableAfterAllowanceForCreditLoss",
                  "LoansReceivableHeldForInvestmentNet",
                  "LoansAndLeasesReceivableNetReportedAmount",
                  "LoansAndLeasesReceivableNetOfDeferredFees",
                  "NotesReceivableNet"),
    "nonaccrual": ("FinancingReceivableExcludingAccruedInterestNonaccrual",
                   "FinancingReceivableRecordedInvestmentNonaccrualStatus"),
    "writeoff": ("FinancingReceivableExcludingAccruedInterestAllowanceForCreditLossWriteoff",
                 "FinancingReceivableAllowanceForCreditLossesWriteoff",
                 # KEY tags the rollforward charge-off with a different casing/plural
                 # ('WriteOffs', capital O) than the singular concept above; without
                 # it KEY's NCO came back None even though the value is cleanly tagged.
                 "FinancingReceivableAllowanceForCreditLossesWriteOffs",
                 "AllowanceForLoanAndLeaseLossesWriteOffs"),
    "recovery": ("FinancingReceivableExcludingAccruedInterestAllowanceForCreditLossRecovery",
                 "FinancingReceivableAllowanceForCreditLossesRecovery",
                 "AllowanceForLoanAndLeaseLossRecoveries"),
    "nco": ("FinancingReceivableExcludingAccruedInterestAllowanceForCreditLossWriteoffAfterRecovery",),
    "provision": ("ProvisionForLoanLeaseAndOtherLosses",
                  "ProvisionForLoanAndLeaseLosses",
                  "ProvisionForCreditLossExpenseReversal",
                  "ProvisionForCreditLosses"),
}


def _cecl_acl_sum(facts: list[Fact], period: str):
    """Total ACL from the per-segment CECL split (collectively + individually
    evaluated), summed over leaf segments (members==1) at `period`. None when not
    tagged this way. Within each component a value-aggregate is dropped — a member
    equal to the sum of the others is a domain/parent total, not a leaf — so a
    parent double-count can't inflate the sum."""
    coll_c = ("LoansAndLeasesReceivableCollectivelyEvaluatedForAllowance",
              "FinancingReceivableAllowanceForCreditLossesCollectivelyEvaluatedForImpairment")
    indiv_c = ("LoansAndLeasesReceivableIndividuallyEvaluatedForAllowance",
               "FinancingReceivableAllowanceForCreditLossesIndividuallyEvaluatedForImpairment1")

    def _leaf_sum(concepts):
        vals = sorted((f.value for f in facts
                       if f.concept.split(":")[-1] in concepts
                       and len(f.members) == 1 and f.period_end == period),
                      key=abs, reverse=True)
        if len(vals) >= 3 and abs(vals[0] - sum(vals[1:])) <= max(abs(vals[0]) * 0.02, 1e5):
            vals = vals[1:]                      # largest == Σ rest → a parent total
        return vals

    coll, indiv = _leaf_sum(coll_c), _leaf_sum(indiv_c)
    if not coll and not indiv:
        return None
    total = sum(coll) + sum(indiv)
    return total if total > 0 else None


def extract_credit_quality(facts: list[Fact], comp_loan_total=None,
                           comp_loan_period=None) -> dict:
    """{period_end: {...}} — the as-reported CECL allowance & asset-quality summary
    from a filing's iXBRL: allowance for credit losses (ACL), gross/net loans,
    nonaccrual loans, net charge-offs (writeoff − recovery, or the directly tagged
    net), provision, and the derived ratios (ACL ÷ loans, nonaccrual ÷ loans, ACL
    coverage of nonaccruals, NCO ÷ loans).

    Reconcile-gated: a period renders only with a tagged ACL AND gross loans; when
    net loans are also tagged, net + ACL must tie gross within 1% or the trio is
    rejected (n/a). NCO uses the tagged net writeoff-after-recovery, else
    writeoff − recovery when both are present, else None.

    `comp_loan_total` (the reconcile-gated composition loan total from
    data.sec_composition) stands in for the gross loans of a filer that tags loans
    ONLY by segment with no undimensioned total (FFIN); the ACL then comes from the
    per-segment CECL split and the ratio is plausibility-gated. `comp_loan_period`
    is the period-end the composition total is dated at: when supplied it must
    equal the filing's current anchor period or the stand-in is REFUSED (n/a) —
    a current-quarter ACL divided by a prior-FY loan total is a plausible-wrong
    ratio, never rendered. When omitted (None) the caller vouches for the period
    (legacy direct calls); the production caller always passes it."""
    # Use ONLY the filing's current (latest) loan-footnote date — never fall back
    # to a prior-year comparative, which would surface stale figures as current.
    periods: set = set()
    anchors = set(_CQ_CONCEPTS["acl"]) | set(_CQ_CONCEPTS["loans_gross"])
    for f in facts:
        if f.concept.split(":")[-1] in anchors and not f.members:
            periods.add(f.period_end)
    # Anchor the period on the COMPOSITION-derived gross loans too (some filers tag
    # no undimensioned loan total, only a by-segment breakdown → the period set
    # above can be empty even though the dimensional data is all there).
    if not periods and comp_loan_total:
        for f in facts:
            if f.concept.split(":")[-1] == "Assets" and not f.members and f.period_start is None:
                periods.add(f.period_end)
    out: dict = {}
    if periods:
        period = max(periods)
        acl = _sec_pick(facts, _CQ_CONCEPTS["acl"], period)
        if acl is None:
            # Some filers (FFIN) tag no single ACL total, only the CECL split —
            # collectively + individually evaluated = total allowance. Take the
            # undimensioned pair if tagged, else sum the per-segment leaves.
            coll = _sec_pick(facts, ("LoansAndLeasesReceivableCollectivelyEvaluatedForAllowance",
                                     "FinancingReceivableAllowanceForCreditLossesCollectivelyEvaluatedForImpairment"), period)
            indiv = _sec_pick(facts, ("LoansAndLeasesReceivableIndividuallyEvaluatedForAllowance",
                                      "FinancingReceivableAllowanceForCreditLossesIndividuallyEvaluatedForImpairment1"), period)
            acl = (coll + indiv) if (coll is not None and indiv is not None) else _cecl_acl_sum(facts, period)
        gross = _sec_pick(facts, _CQ_CONCEPTS["loans_gross"], period)
        # Dimensional fallback: the reconcile-gated composition total stands in for an
        # untagged undimensioned gross (a filer that reports loans only by segment) —
        # but ONLY when the composition total is dated at this same period end.
        # Dividing the current period's ACL by a PRIOR-period loan total is a
        # plausible-wrong ratio (audit P3) → period mismatch means n/a, never a
        # cross-period divide.
        if gross in (None, 0) and comp_loan_total and (
                comp_loan_period is None or comp_loan_period == period):
            gross = comp_loan_total
        net = _sec_pick(facts, _CQ_CONCEPTS["loans_net"], period)
        # A tagged net far from gross is a wrong-concept match (FFIN's tiny
        # NotesReceivableNet $56M vs $8.2B gross) — ignore it rather than reject.
        if net is not None and gross and not (0.80 * gross <= net <= 1.02 * gross):
            net = None
        # Render only with ACL AND gross loans at the current date. With a valid net
        # it must tie gross − ACL; without one (dimensional / net untagged) the
        # ACL/loans ratio must be plausible [0.2%, 6%] — guarding a bad sum.
        ok = acl is not None and gross not in (None, 0)
        if ok and net is not None:
            ok = abs((net + acl) - gross) <= max(abs(gross) * 0.01, 5e6)
        elif ok:
            ok = 0.002 <= acl / gross <= 0.06
        if ok:
            nonaccrual = _sec_pick(facts, _CQ_CONCEPTS["nonaccrual"], period)
            nco = _sec_pick(facts, _CQ_CONCEPTS["nco"], period)
            if nco is None:
                wo = _sec_pick(facts, _CQ_CONCEPTS["writeoff"], period)
                rec = _sec_pick(facts, _CQ_CONCEPTS["recovery"], period)
                nco = (wo - rec) if (wo is not None and rec is not None) else None
            provision = _sec_pick(facts, _CQ_CONCEPTS["provision"], period)
            out[period] = {
                "acl": acl, "loans_gross": gross, "loans_net": net,
                "nonaccrual": nonaccrual, "nco": nco, "provision": provision,
                "acl_to_loans": acl / gross,
                "nonaccrual_to_loans": (nonaccrual / gross) if nonaccrual is not None else None,
                "nco_to_loans": (nco / gross) if nco is not None else None,
                "acl_coverage_nonaccrual": (acl / nonaccrual) if nonaccrual else None,
                "_reconciles": net is not None,
            }
    return out


def credit_quality_for(cik) -> dict | None:
    """Cached CECL allowance & asset-quality summary for a company from its own
    latest SEC filing (timeliest 10-Q, then 10-K). Returns
    {"meta": {...}, "credit_quality": {period: {...}}} or None."""
    if not cik:
        return None
    from data import cache
    for forms in (("10-Q",), ("10-K",)):
        meta = latest_filing(cik, forms)
        if not meta:
            continue
        ckey = f"credit_quality:v2:{meta['accession']}"
        cq = cache.get(ckey)
        if cq is None:
            try:
                facts = instance_facts(meta)
                cq = extract_credit_quality(facts)
                if not cq:
                    # Dimensional fallback: stand in the reconcile-gated composition
                    # loan total for a filer that tags loans only by segment (FFIN).
                    try:
                        from data.sec_composition import compositions_for
                        comp = compositions_for(cik)
                        loan = comp.get("loan") if comp else None
                        comp_period = max(loan) if loan else None
                        total = loan[comp_period]["total"] if loan else None
                    except Exception:
                        comp_period = total = None
                    if total:
                        # Pass the composition total WITH its period end — the
                        # extractor refuses the stand-in unless it matches the
                        # filing's current period (never current-Q ACL ÷ a
                        # prior-FY loan total).
                        cq = extract_credit_quality(
                            facts, comp_loan_total=total,
                            comp_loan_period=comp_period)
                try:
                    cache.put(ckey, cq)
                except Exception:
                    pass
            except Exception as e:
                print(f"[sec_scraper] credit quality failed for cik {cik}: {type(e).__name__}: {e}")
                cq = {}
        if cq:
            return {"meta": meta, "credit_quality": cq}
    return None


# ── Multi-year company-reported asset quality + NIM (10-K back-history) ───────
# The "Company Reported → Financial Highlights" tab wants a multi-year (≤5y)
# trend of three ratios sourced ONLY from the bank's own SEC filings — never
# FDIC. A single 10-K discloses two-to-three comparative years per metric, so we
# fetch the latest few 10-Ks and merge by fiscal year (the NEWEST filing that
# covers a year wins, so a restated comparative is superseded by the year it was
# the current-year figure). The three metrics live in three different filing
# structures:
#   • NPL/loans  — nonaccrual (or nonperforming) loans ÷ gross loans, both tagged
#     undimensioned in the loan footnote (current + one comparative per filing).
#   • NCO/loans  — (gross charge-offs − recoveries) ÷ gross loans, from the ACL
#     rollforward. Charge-offs/recoveries are tagged as full-year durations, but
#     a filer may tag the TOTAL only under a "total loans" portfolio member in the
#     current year and undimensioned in the comparative — take whichever is the
#     reconciled total, never a per-segment sum (double-counts the total row).
#   • NIM        — the MD&A average-balance table states "Net interest margin" per
#     year (FTE). This table is NOT inline-XBRL-tagged (no NetInterestMargin /
#     AverageEarningAssets concept exists in the instance), so it is parsed from
#     the filing HTML: read the year-label header row and the "Net interest
#     margin" row, zip them positionally. Falls back to NII ÷ avg earning assets
#     (both stated in the same table) only when the explicit NIM line is absent.
# Every metric/year that doesn't cleanly disclose/reconcile → None (blank). The
# denominator for both credit ratios is period-end gross loans (self-contained in
# the same XBRL the numerators come from); average loans from the MD&A table are
# intentionally not mixed in, keeping each ratio from one consistent source.
_NONPERF_CONCEPTS = (
    "FinancingReceivableExcludingAccruedInterestNonaccrual",
    "FinancingReceivableRecordedInvestmentNonaccrualStatus",
    # Some filers tag a nonperforming-loans total rather than nonaccrual.
    "FinancingReceivableNonperformingLoans",
)
# Member-name markers that identify the TOTAL row of a by-segment charge-off /
# recovery disclosure (so we take the reconciled total, never sum the segments).
_LOAN_TOTAL_MEMBER = re.compile(
    r"TotalLoans|TotalFinancingReceivable|AllLoans|LoansReceivable|"
    r"PortfolioSegmentDomain|FinancingReceivableMember", re.I)


def _flow_total_for_period(facts: list[Fact], concepts: tuple, period_end: str):
    """Full-year (≈12-month) total of a charge-off / recovery flow ending at
    `period_end`. Prefers the undimensioned total; falls back to a single-member
    fact whose member is the loan-book TOTAL (TotalLoansMember, …). NEVER sums the
    per-segment rows (the total row would double-count). None if neither tagged."""
    undim = None
    total_mem = None
    for f in facts:
        if f.concept.split(":")[-1] not in concepts:
            continue
        if f.period_end != period_end or not f.period_start:
            continue
        if not (330 <= _days(f.period_start, period_end) <= 400):
            continue
        if not f.members:
            undim = f.value
        elif len(f.members) == 1 and _LOAN_TOTAL_MEMBER.search(
                next(iter(f.members.values())).split(":")[-1]):
            total_mem = f.value
    return undim if undim is not None else total_mem


def _annual_periods(facts: list[Fact]) -> list[str]:
    """Period-end dates (newest first) that carry an undimensioned full-year flow
    in the ACL rollforward — the fiscal years this filing reports charge-offs for."""
    ends: set = set()
    flows = set(_CQ_CONCEPTS["writeoff"]) | set(_CQ_CONCEPTS["recovery"])
    for f in facts:
        if (f.concept.split(":")[-1] in flows and f.period_start
                and 330 <= _days(f.period_start, f.period_end) <= 400):
            ends.add(f.period_end)
    return sorted(ends, reverse=True)


def _gross_loans_at(facts: list[Fact], period_end: str):
    """Undimensioned gross loans (before allowance) at `period_end`, via the same
    priority list the credit-quality extractor uses. None if untagged."""
    return _sec_pick(facts, _CQ_CONCEPTS["loans_gross"], period_end)


# Performance-status axis + nonperforming member: the dimension a filer uses to
# tag its loan total split between performing and nonperforming, rather than a
# dedicated nonaccrual concept (KEY does this — no
# FinancingReceivableRecordedInvestmentNonaccrualStatus exists in its instance).
_PERF_STATUS_AXIS = "FinancialInstrumentPerformanceStatusAxis"
_NONPERF_MEMBER = re.compile(r"nonperform|nonaccrual", re.I)


def _nonaccrual_via_perf_status(facts: list[Fact], period_end: str):
    """Total nonaccrual/nonperforming loans at `period_end` for a filer that tags
    it ONLY as the gross-loans concept sliced by the performance-status axis
    (member = Nonperforming), with no portfolio/class member — i.e. the single
    performance-status total, NOT a per-segment slice. None if not tagged this way
    or if more than one such total is present (ambiguous → never guess)."""
    hits = []
    for f in facts:
        if (f.concept.split(":")[-1] in _CQ_CONCEPTS["loans_gross"]
                and f.period_start is None and f.period_end == period_end
                and len(f.members) == 1):
            axis, member = next(iter(f.members.items()))
            if (axis.split(":")[-1] == _PERF_STATUS_AXIS
                    and _NONPERF_MEMBER.search(member.split(":")[-1])
                    and f.value is not None):
                hits.append(f.value)
    if len(hits) == 1:
        return hits[0]
    return None


def extract_npl_nco_by_year(facts: list[Fact]) -> dict:
    """{fiscal_year:int -> {"npl_loans":…, "nco_loans":…}} from ONE filing's iXBRL.

    npl_loans = nonaccrual (or nonperforming) loans ÷ gross loans at each tagged
    balance-sheet date; nco_loans = (charge-offs − recoveries) ÷ gross loans for
    each full-year rollforward period. Each ratio is emitted only when its
    numerator AND a positive gross-loan denominator are both cleanly tagged for
    that year; otherwise that cell is None (never guessed, never FDIC)."""
    out: dict[int, dict] = {}

    # NPL: every balance-sheet date with an undimensioned nonaccrual/nonperforming
    # total and a gross-loan denominator.
    for f in facts:
        if f.concept.split(":")[-1] not in _NONPERF_CONCEPTS or f.members:
            continue
        if f.period_start is not None:          # instant only
            continue
        npl = f.value
        gross = _gross_loans_at(facts, f.period_end)
        if npl is None or not gross:
            continue
        year = int(f.period_end[:4])
        ratio = npl / gross
        if 0.0 <= ratio <= 0.25:                # sane NPL band; else parse error → skip
            out.setdefault(year, {}).setdefault("npl_loans", ratio)

    # NPL fallback: filers that tag no dedicated nonaccrual concept (KEY) state the
    # nonperforming TOTAL as the gross-loans concept sliced by the performance-status
    # axis. For every balance-sheet date that has a gross-loan denominator but no
    # npl_loans yet, take that performance-status total (single, unambiguous) ÷ gross.
    bs_dates = {f.period_end for f in facts
                if f.concept.split(":")[-1] in _CQ_CONCEPTS["loans_gross"]
                and not f.members and f.period_start is None}
    for period_end in bs_dates:
        year = int(period_end[:4])
        if out.get(year, {}).get("npl_loans") is not None:
            continue
        npl = _nonaccrual_via_perf_status(facts, period_end)
        gross = _gross_loans_at(facts, period_end)
        if npl is None or not gross:
            continue
        ratio = npl / gross
        if 0.0 <= ratio <= 0.25:
            out.setdefault(year, {}).setdefault("npl_loans", ratio)

    # NCO: each full-year rollforward period; denominator = gross loans at year-end.
    for period_end in _annual_periods(facts):
        wo = _flow_total_for_period(facts, _CQ_CONCEPTS["writeoff"], period_end)
        rec = _flow_total_for_period(facts, _CQ_CONCEPTS["recovery"], period_end)
        if wo is None or rec is None:
            continue
        gross = _gross_loans_at(facts, period_end)
        if not gross:
            continue
        nco = wo - rec
        ratio = nco / gross
        if -0.05 <= ratio <= 0.25:              # net recoveries can be slightly <0
            out.setdefault(int(period_end[:4]), {}).setdefault("nco_loans", ratio)
    return out


# Numeric cell in the average-balance table ("3.79", "1,398,314", "(0.05)").
_NUM_CELL = re.compile(r"^\(?-?\d[\d,]*\.?\d*\)?$")


def _row_numbers(cells: list[str]) -> list[float]:
    """The numeric values of a table row, left→right (skips '', '%', '$', labels).
    Parentheses → negative; commas stripped."""
    out: list[float] = []
    for c in cells:
        c = c.strip()
        if not _NUM_CELL.match(c):
            continue
        neg = c.startswith("(")
        v = c.strip("()").replace(",", "")
        try:
            fv = float(v)
        except ValueError:
            continue
        out.append(-fv if neg else fv)
    return out


def _num_cell(c: str):
    """Parse one cell to a float ('3.21%', '(0.05)', '1,398') or None for a
    non-numeric/empty cell. Parentheses → negative; '%','$',commas stripped."""
    c = c.strip().replace("%", "").replace("$", "").strip()
    if not _NUM_CELL.match(c):
        return None
    neg = c.startswith("(")
    try:
        v = float(c.strip("()").replace(",", ""))
    except ValueError:
        return None
    return -v if neg else v


def _year_columns(rows: list[list[str]]) -> list[tuple[int, int]]:
    """[(column_index, fiscal_year), …] for the table's year header. The header row
    is the one with the MOST standalone 4-digit year cells (≥2). Real MD&A tables
    carry a leading label cell and often a '2025/2024 Change' column alongside the
    year labels, so the header row is NOT pure years — pick by year-cell count and
    keep each year's COLUMN INDEX so the NIM row is read positionally, never by a
    strip-then-zip that an interleaved blank or a 'Change' column would misalign."""
    best: list[tuple[int, int]] = []
    for r in rows:
        yc = [(i, int(c.strip())) for i, c in enumerate(r)
              if re.fullmatch(r"(19|20)\d{2}", c.strip())]
        if len(yc) > len(best):
            best = yc
    return best if len(best) >= 2 else []


# ── Prose-stated NIM fallback ────────────────────────────────────────────────
# Some filers (CFR, RF, ASB, COLB, KEY) state net interest margin only in MD&A
# NARRATIVE prose — "net interest margin … was 3.61 percent in 2025" — never in a
# year-headed table row, so the table parser above finds nothing. These patterns
# pull the value AND its fiscal year from the sentence, strictly, so a wrong number
# is never extracted (the cardinal rule): the value must be bound to the margin
# phrase by a verb/preposition, an explicit year must sit right next to it, and the
# value must fall in a sane NIM band. Percentage-CHANGE deltas ("increased 12 basis
# points") and net-interest-SPREAD/INCOME sentences are rejected.
_PCT = r"(?:%|\bpercent\b)"
# A different metric grabbing the value between the margin phrase and the number.
_NIM_PROSE_BREAK = re.compile(
    r"net\s+interest\s+(?:spread|income)|funding\s+cost|average\s+yield|"
    r"\byield\s+on|return\s+on|noninterest|efficiency", re.I)
# Value-then-year: "net interest margin … was/of/to X.XX% … in/during/for YYYY"
# (RF, ASB, COLB, and CFR's "…increased 13 bp from 3.53% during 2024 to 3.66%
# during 2025" — the verb 'to' binds the 3.66% and "during 2025" names its year;
# 'from 3.53%' has no binding verb so the prior-year value is not picked up).
_NIM_PROSE_A = re.compile(
    r"net\s+interest\s+margin\b(?P<mid>.{0,55}?)"
    r"\b(?:was|of|to|at|:)\s*(?P<val>\d\.\d{1,2})\s*" + _PCT +
    r"(?P<after>.{0,55})", re.I)
# The year tie after the value: a temporal preposition then the year within a small
# window — wide enough for 'for the year ended December 31, YYYY' (COLB), tight
# enough that it can't reach a different sentence's year.
_NIM_PROSE_YR_AFTER = re.compile(r"\b(?:in|during|for)\b.{0,40}?((?:19|20)\d{2})", re.I)
# Year-before-value: "for/in YYYY … net interest margin was X.XX%" (KEY states
# "Net interest income (TE) for 2025 was $4.7 billion, and the net interest margin
# was 2.69%"). Bounded so the year and the value stay in the same clause.
_NIM_PROSE_B = re.compile(
    r"\b(?:in|during|for)\b.{0,15}?((?:19|20)\d{2})"
    r".{0,90}?net\s+interest\s+margin\b.{0,25}?\b(?:was|of|to|at)\s*"
    r"(\d\.\d{1,2})\s*" + _PCT, re.I)


def extract_nim_prose(html_bytes: bytes) -> dict:
    """{fiscal_year:int -> nim_fraction} from MD&A narrative prose (see above).

    Only values in the 1.00%–6.00% band that are bound to the margin phrase AND to
    an explicit fiscal year are returned; everything else is dropped (n/a, never a
    guess). The first confident statement for a year wins."""
    from lxml import html as lhtml
    text = re.sub(r"\s+", " ", lhtml.fromstring(html_bytes).text_content())
    out: dict[int, float] = {}
    for m in _NIM_PROSE_A.finditer(text):
        if _NIM_PROSE_BREAK.search(m.group("mid")):   # a different metric owns the value
            continue
        val = float(m.group("val"))
        if not (1.0 <= val <= 6.0):
            continue
        ym = _NIM_PROSE_YR_AFTER.search(m.group("after"))
        if not ym:
            continue
        out.setdefault(int(ym.group(1)), val / 100.0)
    for m in _NIM_PROSE_B.finditer(text):
        val = float(m.group(2))
        if 1.0 <= val <= 6.0:
            out.setdefault(int(m.group(1)), val / 100.0)
    return out


def extract_nim_by_year(html_bytes: bytes) -> dict:
    """{fiscal_year:int -> nim_fraction} from the MD&A average-balance table HTML.

    The table is not inline-XBRL-tagged, so it's read from the rendered HTML: find
    the table containing a 'Net interest margin' row, read the column years from
    its header row(s), and align the NIM row's percentages to those year COLUMNS by
    position. When the explicit NIM line is absent but the table states net interest
    income and total interest-earning assets (average), NIM is computed = NII ÷ avg
    earning assets. Percentages are returned as fractions (3.79% → 0.0379). {} if
    no such table."""
    from lxml import html as lhtml
    root = lhtml.fromstring(html_bytes)
    for tbl in root.iter("table"):
        # Gate on the NIM line OR its synonym 'net yield on … earning assets'
        # (CBSH and others state the margin only under that name, in a table that
        # never says 'net interest margin' — gating on that phrase alone skipped it).
        if not re.search(r"net\s+interest\s+margin|net\s+yield\s+on\b",
                         tbl.text_content(), re.I):
            continue
        rows = [[re.sub(r"\s+", " ", td.text_content()).strip()
                 for td in tr.iter("td", "th")] for tr in tbl.iter("tr")]
        year_cols = _year_columns(rows)
        if not year_cols:
            continue
        years = [y for _, y in year_cols]

        def _row(label_pat):
            for r in rows:
                if r and re.fullmatch(label_pat, r[0].strip(), re.I):
                    return r
            return None

        # The headline NIM row: a label STARTING with 'net interest margin'
        # (allowing '(FTE)', '- TE', 'on an FTE basis', footnote refs like '(3)',
        # 'on average interest-earning assets') OR its exact synonym 'net yield on
        # interest-earning assets' (CBSH/others state NIM under that name — same
        # ratio, NII ÷ avg earning assets). The leading anchor rejects the
        # lookalikes — 'Percentage increase … in net interest margin' (CBSH) and
        # 'Information on net interest income and net interest margin:' (BOKF
        # header) — and a bare fullmatch missed every filer that suffixes the
        # label, which is why the latest FY's NIM kept landing n/a.
        _NIM_LABEL = re.compile(
            r"net\s+interest\s+margin\b|"
            r"net\s+yield\s+on\s+(average\s+|total\s+)?(interest[- ]?earning|earning)\s+assets",
            re.I)
        nim_row = next((r for r in rows if r and _NIM_LABEL.match(r[0].strip())
                        and "trading activities" not in r[0].lower()), None)

        nims: dict[int, float] = {}
        if nim_row is not None:
            # One NIM percentage per year, left→right. SEC renders a percent as one
            # cell ('3.21%') or two ('3.21','%'); _num_cell parses both. Zip to the
            # years ONLY when the count matches exactly — an off-count means the row
            # carries an extra figure (a 'Change' column value) we can't position
            # safely, so n/a rather than a misaligned guess.
            vals = [v for v in (_num_cell(c) for c in nim_row[1:])
                    if v is not None and 0 < v < 15]
            if len(vals) == len(years):
                for yr, v in zip(years, vals):
                    nims[yr] = v / 100.0
        if not nims:
            # Fallback: NII ÷ average earning assets, both from this same table.
            nii_row = _row(r"net\s+interest\s+income")
            ea_row = next((r for r in rows if r and re.search(
                r"total\s+interest[- ]earning\s+assets", r[0].strip(), re.I)), None)
            if nii_row is not None and ea_row is not None:
                nii = _row_numbers(nii_row[1:])
                # The earning-assets row is (avg balance, interest, yield) per year;
                # take the first value of each year-triplet = average balance. That
                # triplet is a POSITIONAL assumption, so require both counts to
                # match EXACTLY — any extra figure (a 'Change' column, a stray
                # footnote number) shifts the indexing and would divide the wrong
                # cells → n/a rather than a misaligned guess (audit P3).
                ea_all = _row_numbers(ea_row[1:])
                if len(ea_all) == 3 * len(years) and len(nii) == len(years):
                    for i, yr in enumerate(years):
                        ea = ea_all[i * 3]
                        if not ea:
                            continue
                        v = nii[i] / ea
                        # Plausibility band: a real NIM is 0–10%. Outside means a
                        # mis-picked numerator/denominator (e.g. the interest
                        # column taken as the balance) → n/a, never a wrong margin.
                        if 0 < v < 0.10:
                            nims[yr] = v
        if nims:
            table_nims = nims
            break
    else:
        table_nims = {}
    # Prose fills only the years the table didn't yield (table value preferred when
    # both exist). Filers that state NIM only in narrative prose (no table row) get
    # their whole series from here; the rest get nothing extra.
    merged = dict(extract_nim_prose(html_bytes))
    merged.update(table_nims)
    return merged


def _list_10k_filings(cik, limit: int) -> list[dict]:
    """Up to `limit` most-recent 10-K filing metas (newest first), each shaped like
    latest_filing()'s return so instance_facts()/filing_url() consume them as-is."""
    cik10 = str(int(cik)).zfill(10)
    data = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik10}.json"))
    rec = data.get("filings", {}).get("recent", {})
    out: list[dict] = []
    for i, f in enumerate(rec.get("form", [])):
        if f == "10-K":
            out.append({"accession": rec["accessionNumber"][i].replace("-", ""),
                        "doc": rec["primaryDocument"][i],
                        "date": rec["filingDate"][i], "form": f, "cik": int(cik)})
            if len(out) >= limit:
                break
    return out


# How many recent 10-Ks to merge. Many filers (ABCB) tag only the current + ONE
# prior comparative year of nonaccrual AND of the charge-off rollforward — so each
# 10-K contributes at most one NEW fiscal year beyond the overlap, and three
# filings reach only FY..FY-3, leaving the oldest in-window year (FY-4) n/a even
# though it IS disclosed (in the FY-3 10-K's prior-year column / the FY-4 10-K's
# own column). Fetch _MAX_YEARS filings so the full _MAX_YEARS window is covered
# in that worst case; NIM/charge-off years that overlap are the consistency check
# (newest filing wins on any shared year). Truncated to _MAX_YEARS after merge.
_MAX_YEARS = 5
_HISTORY_FILINGS = _MAX_YEARS


def extract_asset_quality_nim(metas_and_docs: list[tuple]) -> dict:
    """Merge per-year {npl_loans, nco_loans, nim} across several 10-Ks.

    `metas_and_docs` is [(meta, facts, html_bytes), …] NEWEST FIRST. For each
    fiscal year a metric is filled by the FIRST (newest) filing that cleanly
    discloses it, so a current-year figure supersedes the same year's later
    restated comparative. Returns {year:int -> {"npl_loans","nco_loans","nim"}}
    truncated to the newest _MAX_YEARS years."""
    merged: dict[int, dict] = {}

    def _set(year, key, val):
        if val is None:
            return
        merged.setdefault(int(year), {}).setdefault(key, val)  # first (newest) wins

    for _meta, facts, html_bytes in metas_and_docs:
        cr = extract_npl_nco_by_year(facts)
        for yr, d in cr.items():
            _set(yr, "npl_loans", d.get("npl_loans"))
            _set(yr, "nco_loans", d.get("nco_loans"))
        if html_bytes:
            for yr, nim in extract_nim_by_year(html_bytes).items():
                _set(yr, "nim", nim)

    # Normalise every year to all three keys (None where a metric is absent) and
    # keep only the newest _MAX_YEARS years.
    out: dict[int, dict] = {}
    for yr in sorted(merged, reverse=True)[:_MAX_YEARS]:
        d = merged[yr]
        out[yr] = {"npl_loans": d.get("npl_loans"),
                   "nco_loans": d.get("nco_loans"),
                   "nim": d.get("nim")}
    return out


def company_asset_quality_nim(cik) -> dict | None:
    """Company-REPORTED multi-year asset-quality & NIM trend from a bank's own
    10-K filings (NEVER FDIC), for the Financial-Highlights tab.

    Fetches the latest few 10-Ks, scrapes each one's nonaccrual/loans, net-charge-
    offs/loans (ACL rollforward) and MD&A net-interest-margin, and merges them by
    fiscal year (newest filing wins). Returns
    {"meta": <latest 10-K meta>, "by_year": {2025: {"npl_loans","nco_loans","nim"},
    …}} keyed by fiscal-year int with values as FRACTIONS (0.0053 = 0.53%), or None
    when no 10-K is found. Each fetched filing is cached by accession; the merged
    result is cached under the latest accession + the history depth."""
    if not cik:
        return None
    from data import cache
    filings = _list_10k_filings(cik, _HISTORY_FILINGS)
    if not filings:
        return None
    latest = filings[0]
    # v4: deeper filing window (_HISTORY_FILINGS = _MAX_YEARS) recovers the oldest
    # in-window year's NPL/NCO that the 3-filing window dropped. v3: prose-NIM
    # fallback + performance-status nonaccrual + WriteOffs casing. (_HISTORY_FILINGS
    # is in the key, so the depth bump alone already invalidates old entries.)
    ckey = f"asset_quality_nim:v4:{latest['accession']}:{_HISTORY_FILINGS}"
    by_year = cache.get(ckey)
    if by_year is None:
        bundle: list[tuple] = []
        for meta in filings:
            try:
                html_bytes = _get(filing_url(meta["cik"], meta["accession"], meta["doc"]))
                facts = parse_inline_xbrl(html_bytes)
                if len(facts) < _MULTIDOC_FACT_THRESHOLD:
                    facts = instance_facts(meta)   # split filer — fetch the doc set
                bundle.append((meta, facts, html_bytes))
            except Exception as e:
                print(f"[sec_scraper] asset_quality_nim filing {meta.get('accession')} "
                      f"failed for cik {cik}: {type(e).__name__}: {e}")
        try:
            by_year = extract_asset_quality_nim(bundle)
        except Exception as e:
            print(f"[sec_scraper] asset_quality_nim failed for cik {cik}: {type(e).__name__}: {e}")
            by_year = {}
        # JSON cache coerces int keys to str — keep raw dict in memory; cache a
        # str-keyed copy that we re-coerce on read.
        try:
            cache.put(ckey, {str(k): v for k, v in by_year.items()})
        except Exception:
            pass
    else:
        by_year = {int(k): v for k, v in by_year.items()}
    if by_year:
        return {"meta": latest, "by_year": by_year}
    return None


# ── Performance analysis (as-reported profitability) ─────────────────────────
def _days(start: str, end: str) -> int:
    try:
        return (date.fromisoformat(end) - date.fromisoformat(start)).days
    except Exception:
        return -1


def _prior_year_end(iso: str) -> str | None:
    try:
        d = date.fromisoformat(iso)
        return d.replace(year=d.year - 1).isoformat()
    except Exception:
        return None


def extract_performance(facts: list[Fact]) -> dict:
    """{fiscal_year_end: {...}} — the as-reported full-year profitability summary
    from a filing's iXBRL: net interest income, noninterest income, total revenue,
    noninterest expense, pre-provision net revenue, provision, net income, EPS, and
    the efficiency ratio plus ROA/ROE.

    Every figure is a directly tagged full-year (≈12-month) income-statement line or
    a transparent combination of them (revenue = NII + noninterest income; PPNR =
    revenue − noninterest expense; efficiency = noninterest expense ÷ revenue).
    ROA/ROE use average assets/equity — the bank's tagged average when present, else
    the (beginning + ending) ÷ 2 of the balance-sheet comparatives, flagged
    `_avg_computed`. A confidence flag `_reconciles` records whether the income
    walk (NII + noninterest income − noninterest expense − provision − tax) ties net
    income. n/a unless the core lines (NII, noninterest income/expense, net income)
    are all tagged for the latest fiscal year."""
    NI = ("NetIncomeLoss", "ProfitLoss")
    fy_end = None
    for f in facts:
        if (f.concept.split(":")[-1] in NI and not f.members
                and f.period_start and 330 <= _days(f.period_start, f.period_end) <= 400):
            if fy_end is None or f.period_end > fy_end:
                fy_end = f.period_end
    if fy_end is None:
        return {}

    def ann(concepts):
        by_c: dict = {}
        for f in facts:
            c = f.concept.split(":")[-1]
            if (c in concepts and not f.members and f.period_end == fy_end
                    and f.period_start and 330 <= _days(f.period_start, fy_end) <= 400):
                by_c.setdefault(c, f.value)
        for c in concepts:
            if c in by_c:
                return by_c[c]
        return None

    nii = ann(("InterestIncomeExpenseNet",))
    if nii is None:
        # Many filers tag gross interest income and interest expense separately
        # rather than the net (ACNB/ALLY/AROW/CPBI…) — net interest income is their
        # difference (faithful, the bank's own components).
        int_inc = ann(("InterestAndDividendIncomeOperating",
                       "InterestAndDividendIncomeSecuritiesAndOtherEarningAssetsOperating",
                       "InterestAndFeeIncomeLoansAndLeases"))
        int_exp = ann(("InterestExpense",))
        if int_inc is not None and int_exp is not None:
            nii = int_inc - int_exp
    nonint_inc = ann(("NoninterestIncome", "NoninterestIncomeLoss"))
    nonint_exp = ann(("NoninterestExpense",))
    net_income = ann(NI)
    if None in (nii, nonint_inc, nonint_exp, net_income):
        return {}
    provision = ann(_CQ_CONCEPTS["provision"])
    tax = ann(("IncomeTaxExpenseBenefit",))
    eps = ann(("EarningsPerShareDiluted",
               "IncomeLossFromContinuingOperationsPerDilutedShare"))
    revenue = nii + nonint_inc
    ppnr = revenue - nonint_exp
    reconciles = (provision is not None and tax is not None
                  and abs((nii + nonint_inc - nonint_exp - provision - tax) - net_income)
                  <= max(abs(net_income) * 0.02, 5e6))

    # Average balances: prefer a tagged average, else (beginning + ending) ÷ 2.
    def _instant(concept, when):
        for f in facts:
            if (f.concept.split(":")[-1] == concept and not f.members
                    and f.period_start is None and f.period_end == when):
                return f.value
        return None

    def avg(tagged_avg_concepts, instant_concept):
        for c in tagged_avg_concepts:
            v = ann((c,))
            if v is not None:
                return v, False
        cur = _instant(instant_concept, fy_end)
        py = _prior_year_end(fy_end)
        prior = _instant(instant_concept, py) if py else None
        if cur is not None and prior is not None:
            return (cur + prior) / 2.0, True
        # No prior-year instant → we cannot form a real (begin+end)/2 average.
        # A single period-END balance is NOT an average; ROA/ROE computed on it
        # would be a different, overstated metric flagged identically to a true
        # average (AUDIT-2026-07-02 #23). n/a over that mislabel (cardinal rule).
        return (None, True)

    avg_assets, a_comp = avg(("AssetsAverageOutstanding",), "Assets")
    avg_equity, e_comp = avg(("StockholdersEquityAverageOutstanding",), "StockholdersEquity")
    roa = (net_income / avg_assets) if avg_assets else None
    roe = (net_income / avg_equity) if avg_equity else None
    return {fy_end: {
        "nii": nii, "noninterest_income": nonint_inc, "revenue": revenue,
        "noninterest_expense": nonint_exp, "ppnr": ppnr, "provision": provision,
        "net_income": net_income, "eps_diluted": eps,
        # Efficiency = expense ÷ revenue, meaningful only with positive revenue. In a
        # securities-repositioning loss year noninterest income can swamp NII and
        # drive revenue ≤ 0 (e.g. HBNC FY25 NII $229M, noninterest income −$256M),
        # where a ratio is nonsensical → n/a. The faithfully-tagged lines still show.
        "efficiency": (nonint_exp / revenue) if revenue and revenue > 0 else None,
        "roa": roa, "roe": roe,
        "_avg_computed": bool(a_comp or e_comp),
        "_reconciles": reconciles,
    }}


def performance_for(cik) -> dict | None:
    """Cached as-reported full-year profitability summary for a company from its own
    latest 10-K (annual figures). Returns {"meta": {...}, "performance": {...}} or
    None."""
    if not cik:
        return None
    from data import cache
    meta = latest_filing(cik, ("10-K",))
    if not meta:
        return None
    ckey = f"performance:v1:{meta['accession']}"
    perf = cache.get(ckey)
    if perf is None:
        try:
            perf = extract_performance(instance_facts(meta))
            try:
                cache.put(ckey, perf)
            except Exception:
                pass
        except Exception as e:
            print(f"[sec_scraper] performance failed for cik {cik}: {type(e).__name__}: {e}")
            perf = {}
    if perf:
        return {"meta": meta, "performance": perf}
    return None


# ── Financial highlights (one-page snapshot) ─────────────────────────────────
def extract_financial_highlights(facts: list[Fact], anchor_cet1=None) -> dict:
    """A one-page snapshot composed from a single 10-K's facts: balance-sheet
    totals (assets, loans, deposits, equity), the full-year profitability headline
    (net income, revenue, diluted EPS, ROA, ROE, efficiency), the headline CET1
    ratio and the allowance/asset-quality headline (ACL ÷ loans, nonaccrual ÷
    loans). Every value is sourced from the same already-built reconcile-gated
    extractors or a directly tagged balance-sheet total — nothing new is guessed.
    n/a-per-field; returns {} only when total assets aren't tagged."""
    # Latest balance-sheet date = newest period_end carrying undimensioned Assets.
    bs_period = None
    for f in facts:
        if (f.concept.split(":")[-1] == "Assets" and not f.members
                and f.period_start is None):
            if bs_period is None or f.period_end > bs_period:
                bs_period = f.period_end
    if bs_period is None:
        return {}
    assets = _undimensioned_total(facts, "Assets", bs_period)
    deposits = _undimensioned_total(facts, "Deposits", bs_period)
    equity = _undimensioned_total(facts, "StockholdersEquity", bs_period)

    perf = extract_performance(facts)
    p = perf[max(perf)] if perf else {}
    cq = extract_credit_quality(facts)
    c = cq[max(cq)] if cq else {}
    cap = extract_holdco_capital(facts, anchor_cet1=anchor_cet1)
    cp = cap[max(cap)] if cap else {}

    return {
        "period": bs_period,
        "assets": assets, "loans": c.get("loans_gross"),
        "deposits": deposits, "equity": equity,
        "net_income": p.get("net_income"), "revenue": p.get("revenue"),
        "eps_diluted": p.get("eps_diluted"),
        "roa": p.get("roa"), "roe": p.get("roe"), "efficiency": p.get("efficiency"),
        "cet1": cp.get("cet1_ratio"),
        "acl_to_loans": c.get("acl_to_loans"),
        "nonaccrual_to_loans": c.get("nonaccrual_to_loans"),
        "fy": max(perf) if perf else None,
    }


def financial_highlights_for(cik, anchor_cet1=None) -> dict | None:
    """Cached one-page financial-highlights snapshot for a company from its own
    latest 10-K. Returns {"meta": {...}, "highlights": {...}} or None."""
    if not cik:
        return None
    from data import cache
    meta = latest_filing(cik, ("10-K",))
    if not meta:
        return None
    ckey = f"highlights:v1:{meta['accession']}:{anchor_cet1}"
    hi = cache.get(ckey)
    if hi is None:
        try:
            hi = extract_financial_highlights(instance_facts(meta), anchor_cet1=anchor_cet1)
            try:
                cache.put(ckey, hi)
            except Exception:
                pass
        except Exception as e:
            print(f"[sec_scraper] highlights failed for cik {cik}: {type(e).__name__}: {e}")
            hi = {}
    if hi:
        return {"meta": meta, "highlights": hi}
    return None


# ── Business segment reporting ───────────────────────────────────────────────
_SEG_AXIS = "StatementBusinessSegmentsAxis"
_CONSOL_AXIS = "ConsolidationItemsAxis"


def _seg_of(members: dict) -> str | None:
    """Segment member qname for a CLEAN per-reportable-segment fact: exactly one
    member on the business-segments axis, and any consolidation-items member must
    be OperatingSegments (so totals / intersegment eliminations / reconciling
    columns are excluded). None otherwise."""
    seg = None
    for dim, mem in members.items():
        d, m = dim.split(":")[-1], mem.split(":")[-1]
        if d == _SEG_AXIS:
            if seg is not None:
                return None
            seg = m
        elif d == _CONSOL_AXIS:
            if "OperatingSegments" not in m:
                return None
        else:
            return None
    return seg


def _seg_label(qname: str) -> str:
    """Readable segment name from the member qname (RetailBankingSegmentMember →
    'Retail Banking')."""
    s = qname
    for suf in ("SegmentMember", "SegmentsMember", "Member", "Segment"):
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", s)
    return s.strip() or qname


# Disclosed per-segment measures to fall back to when a filer tags NO per-segment
# net income (ZION, VLY: the new ASC 280 disaggregated-expense disclosure tags
# per-segment revenue/expense/pretax under the segment axis but not a segment NI
# line). Tried in this priority order; the FIRST that has ≥2 clean per-segment
# facts AND a consolidated total AND reconciles (corporate/other residual smaller
# than the consolidated) is surfaced — clearly labelled as the disclosed measure,
# never relabelled "net income". Dollar measures only (no ratios/percentages).
# Each entry: (human label, (concept aliases…)).
_SEG_FALLBACK_MEASURES = (
    ("Pre-tax income ($)", (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments")),
    ("Total revenue ($)", (
        "Revenues", "RevenuesNetOfInterestExpense")),
    ("Net interest income ($)", (
        "InterestIncomeExpenseNet", "InterestIncomeExpenseAfterProvisionForLoanLoss")),
)


def extract_segments(facts: list[Fact]) -> dict:
    """{fiscal_year_end: {"segments": [...], ...}} — the as-reported business-segment
    net income (with revenue and assets when tagged) reconstructed from a filing's
    iXBRL, presented GAAP-style: each reportable segment's directly tagged figures
    plus an explicit 'Corporate / other & reconciling items' residual (consolidated
    − Σ reportable) so the segments tie the consolidated total. The net-income
    measure (NetIncomeLoss or ProfitLoss) is chosen to match the consolidated tag.

    When a filer tags NO per-segment net income but DOES tag a reconciling
    per-segment dollar measure (pretax income, revenue, or net interest income —
    the ASC 280 disaggregated-expense filers like ZION/VLY), the segment table is
    surfaced on that disclosed measure instead, clearly labelled as what it is
    (`ni_measure` is None; `disclosed_*` keys carry the measure). n/a unless ≥2
    reportable segments and a reconciling consolidated total are tagged for the
    latest fiscal year."""
    fy_end = None
    for f in facts:
        if (f.concept.split(":")[-1] in ("NetIncomeLoss", "ProfitLoss") and not f.members
                and f.period_start and 330 <= _days(f.period_start, f.period_end) <= 400):
            if fy_end is None or f.period_end > fy_end:
                fy_end = f.period_end
    if fy_end is None:
        # No undimensioned annual NI — derive the FY-end from the modal annual
        # period so the disclosed-measure fallback can still find its segments.
        fye_mon = _fye_month_from_facts(facts)
        if not fye_mon:
            return {}
        for f in facts:
            if (f.period_start and f.period_end[5:7] == fye_mon
                    and 330 <= _days(f.period_start, f.period_end) <= 400):
                if fy_end is None or f.period_end > fy_end:
                    fy_end = f.period_end
        if fy_end is None:
            return {}

    def _annual(f):
        return f.period_start and f.period_end == fy_end and 330 <= _days(f.period_start, fy_end) <= 400

    def _seg_values(concepts):
        """{seg_member: value} for the first concept in `concepts` tagged for ≥2
        clean reportable segments at fy_end, plus its undimensioned consolidated
        total (or None). ({}, None) if none qualifies."""
        for concept in concepts:
            segvals: dict = {}
            consol = None
            for f in facts:
                if f.concept.split(":")[-1] != concept or not _annual(f):
                    continue
                seg = _seg_of(f.members)
                if seg:
                    segvals.setdefault(seg, f.value)
                elif not f.members and consol is None:
                    consol = f.value
            if len(segvals) >= 2:
                return segvals, consol
        return {}, None

    def _aux(seg_keys):
        """Optional per-segment revenue and assets for the given segment set."""
        seg_rev: dict = {}
        seg_assets: dict = {}
        for f in facts:
            seg = _seg_of(f.members)
            if not seg or seg not in seg_keys:
                continue
            c = f.concept.split(":")[-1]
            if c in ("Revenues", "RevenuesNetOfInterestExpense") and _annual(f):
                seg_rev.setdefault(seg, f.value)
            elif c == "Assets" and f.period_start is None and f.period_end == fy_end:
                seg_assets.setdefault(seg, f.value)
            elif c == "AssetsAverageOutstanding" and _annual(f):
                seg_assets.setdefault(seg, f.value)
        return seg_rev, seg_assets

    # Primary path: per-segment net income. Choose the NI concept the consolidated
    # total uses, then read segments with the SAME concept so the residual is
    # consistent.
    for ni_concept in ("NetIncomeLoss", "ProfitLoss"):
        seg_ni, consol = _seg_values((ni_concept,))
        if not seg_ni or consol is None:
            continue
        seg_rev, seg_assets = _aux(seg_ni)
        segments = [{
            "label": _seg_label(s), "net_income": v,
            "revenue": seg_rev.get(s), "assets": seg_assets.get(s),
        } for s, v in seg_ni.items()]
        segments.sort(key=lambda x: -(x["net_income"] if x["net_income"] is not None else 0))
        residual = consol - sum(seg_ni.values())
        # A clean decomposition has a corporate/other residual SMALLER than the
        # consolidated total. A residual exceeding it means the segment set is
        # unreliable — typically a member that is itself the total/parent
        # double-counted (CTBI tags a "Corporate" segment = consolidated NI; BOTJ
        # an "All Other" = consolidated) → reject (try the other measure, else
        # the disclosed-measure fallback, else n/a), never a misleading breakdown.
        if consol and abs(residual) > abs(consol):
            continue
        return {fy_end: {
            "segments": segments, "consolidated_net_income": consol,
            "reconciling_residual": residual, "ni_measure": ni_concept,
        }}

    # Fallback path: no per-segment NI tagged. Surface the highest-priority
    # disclosed per-segment dollar measure that reconciles (residual smaller than
    # the consolidated). The per-segment values are tagged leaf facts on the
    # segment axis (OperatingSegments only, via _seg_of) — totals/eliminations are
    # excluded — so summing them does NOT double-count; the residual is the
    # explicit corporate/other reconciling item. Labelled as the disclosed measure,
    # never as net income.
    for label, concepts in _SEG_FALLBACK_MEASURES:
        seg_vals, consol = _seg_values(concepts)
        if not seg_vals or consol is None:
            continue
        residual = consol - sum(seg_vals.values())
        if consol and abs(residual) > abs(consol):
            continue
        seg_rev, seg_assets = _aux(seg_vals)
        segments = [{
            "label": _seg_label(s), "net_income": None, "disclosed": v,
            "revenue": seg_rev.get(s), "assets": seg_assets.get(s),
        } for s, v in seg_vals.items()]
        segments.sort(key=lambda x: -(x["disclosed"] if x["disclosed"] is not None else 0))
        return {fy_end: {
            "segments": segments, "consolidated_net_income": None,
            "reconciling_residual": None, "ni_measure": None,
            "disclosed_label": label, "disclosed_consolidated": consol,
            "disclosed_residual": residual,
        }}
    return {}


def _segments_extract_cached(meta: dict) -> dict:
    """extract_segments for one filing, cached per accession (shared key with
    segments_for). {} on failure (never cache a transient None)."""
    from data import cache
    ckey = f"segments:v1:{meta['accession']}"
    seg = cache.get(ckey)
    if seg is None:
        try:
            seg = extract_segments(instance_facts(meta))
            try:
                cache.put(ckey, seg)
            except Exception:
                pass
        except Exception as e:
            print(f"[sec_scraper] segments failed for cik {meta.get('cik')}: "
                  f"{type(e).__name__}: {e}")
            seg = {}
    return seg or {}


def segments_for(cik) -> dict | None:
    """Cached business-segment summary for a company from its own latest 10-K.
    Returns {"meta": {...}, "segments": {...}} or None."""
    if not cik:
        return None
    meta = latest_filing(cik, ("10-K",))
    if not meta:
        return None
    seg = _segments_extract_cached(meta)
    if seg:
        return {"meta": meta, "segments": seg}
    return None


def segments_multiyear_for(cik, n_years: int = 5) -> dict | None:
    """Multi-year business-segment summary, stitched FY-end-only from the bank's
    recent 10-Ks. extract_segments reports a SINGLE FY-end (the filing's own latest
    fiscal year), so each 10-K contributes one fiscal year; reaching back a handful
    of filings yields up to `n_years` fiscal year-ends. Returns
    {"meta": <latest 10-K>, "filings": [...], "segments": {fy_period: {...}}}
    (newest-first periods), or None when no FY-end segment breakdown is tagged.

    Each period entry is exactly what extract_segments produced — i.e. already
    reconcile-gated: ≥2 reportable segments + a consolidated total were tagged and
    the corporate/other residual (consolidated − Σ reportable) is SMALLER than the
    consolidated total (a residual exceeding it means a double-counted parent member
    → that filing's period is dropped, n/a, never a misleading breakdown). Periods
    are de-duplicated keeping the value from the NEWEST filing that tagged them
    (filings agree on shared comparatives; newest is the as-finally-reported figure).
    Segments are unioned across years by their as-reported label at render time —
    nothing is carried forward here."""
    if not cik:
        return None
    from data.sec_statements import _recent_10k_metas
    metas = _recent_10k_metas(cik, n_years)
    if not metas:
        return None
    segments: dict = {}
    used_filings: list = []
    for m in metas:                                  # newest-first
        seg = _segments_extract_cached(m)
        fye = _fye_month_for(m) or "12"              # filer's real FY-end month
        contributed = False
        for period in sorted(seg.keys(), reverse=True):
            if period[5:7] != fye:                   # FY-ends only (skip any stub)
                continue
            if period in segments:                   # newer filing already supplied it
                continue
            segments[period] = seg[period]
            contributed = True
        if contributed:
            used_filings.append(m)
        # Stop once we have the requested span of fiscal years.
        if len({p[:4] for p in segments}) >= n_years:
            break
    if not segments:
        return None
    return {"meta": metas[0], "filings": used_filings, "segments": segments}


# ── Interest-rate risk (embedded, from securities marks vs capital) ───────────
# Forward NII/EVE rate-shock sensitivity is narrative Item 7A MD&A with company-
# specific (often untagged) extension elements — not standardised iXBRL, so it
# can't be extracted reliably. What IS reliable and reconcile-gated is the
# REALISED rate risk already on the books: the AFS + HTM unrealised loss measured
# against equity and CET1 capital (the post-2023 "underwater securities erode
# tangible capital" story). Composed from the audited securities and capital
# extractors plus the tagged equity total.
def extract_rate_risk(facts: list[Fact], anchor_cet1=None) -> dict:
    """{period: {...}} — embedded interest-rate risk: AFS/HTM net unrealised gain
    (loss), the total, and that total as a share of equity and of CET1 capital.
    n/a unless at least one securities portfolio and total equity are tagged."""
    sec = extract_securities(facts)
    if not sec:
        return {}
    period = max(sec)
    afs = sec[period].get("afs")
    htm = sec[period].get("htm")
    afs_net = afs["net_unrealized"] if afs else None
    htm_net = htm["net_unrealized"] if htm else None
    if afs_net is None and htm_net is None:
        return {}
    total = (afs_net or 0.0) + (htm_net or 0.0)
    equity = _undimensioned_total(facts, "StockholdersEquity", period)
    if equity is None or equity == 0:
        return {}
    cap = extract_holdco_capital(facts, anchor_cet1=anchor_cet1)
    # Period-matched only (audit P3): the unrealized marks are measured at
    # `period`; CET1 capital tagged only at a DIFFERENT period end (e.g. a
    # prior-FY comparative in the capital table) must not denominate them —
    # the CET1 share renders n/a rather than a cross-period mix.
    cet1_cap = (cap.get(period) or {}).get("cet1_cap") if cap else None
    return {period: {
        "afs_unrealized": afs_net, "htm_unrealized": htm_net,
        "total_unrealized": total, "equity": equity, "cet1_capital": cet1_cap,
        "unrealized_to_equity": total / equity,
        "unrealized_to_cet1": (total / cet1_cap) if cet1_cap else None,
    }}


def rate_risk_for(cik, anchor_cet1=None) -> dict | None:
    """Cached embedded interest-rate-risk snapshot for a company from its own latest
    filing (timeliest 10-Q, then 10-K). Returns {"meta": {...}, "rate_risk": {...}}
    or None."""
    if not cik:
        return None
    from data import cache
    for forms in (("10-Q",), ("10-K",)):
        meta = latest_filing(cik, forms)
        if not meta:
            continue
        ckey = f"rate_risk:v1:{meta['accession']}:{anchor_cet1}"
        rr = cache.get(ckey)
        if rr is None:
            try:
                rr = extract_rate_risk(instance_facts(meta), anchor_cet1=anchor_cet1)
                try:
                    cache.put(ckey, rr)
                except Exception:
                    pass
            except Exception as e:
                print(f"[sec_scraper] rate risk failed for cik {cik}: {type(e).__name__}: {e}")
                rr = {}
        if rr:
            return {"meta": meta, "rate_risk": rr}
    return None
