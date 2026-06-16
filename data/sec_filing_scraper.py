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


def holdco_capital_for(cik, cert=None) -> dict | None:
    """Cached holdco regulatory capital for a company, from its own SEC filing.

    Prefers the FRESHEST filing that actually carries the capital table: tries
    the latest 10-Q first (timeliest), and falls back to the latest 10-K when
    the 10-Q doesn't tag the full table (many banks tag capital only annually).
    Scrapes the filing's inline XBRL and anchors to the bank's FDIC CET1. Cached
    by accession — the ~7 MB fetch+parse runs once per filing, and an empty
    result is cached too so a capital-less 10-Q isn't re-fetched. Returns
    {"meta": {...}, "capital": {period: {...}}} or None."""
    if not cik:
        return None
    anchor = _fdic_cet1(cert)
    from data import cache
    for forms in (("10-Q",), ("10-K",)):
        meta = latest_filing(cik, forms)
        if not meta:
            continue
        # Version the key: bump when the extraction shape changes so old cache
        # entries (e.g. pre-walk) are abandoned and re-extracted, never served
        # stale. v2 = added the regulatory-capital walk (_walk/_walk_reconciles).
        ckey = f"holdco_cap:v2:{meta['accession']}"
        cap = cache.get(ckey)
        if cap is None:
            try:
                cap = extract_holdco_capital(instance_facts(meta), anchor_cet1=anchor)
                # Cache only a successful parse; a transient fetch/parse exception
                # is never cached (else one SEC hiccup pins the bank to an older
                # filing / n/a until the cache version bumps).
                try:
                    cache.put(ckey, cap)
                except Exception:
                    pass
            except Exception as e:
                print(f"[sec_scraper] holdco capital failed for cik {cik}: {type(e).__name__}: {e}")
                cap = {}
        if _has_capital(cap):
            return {"meta": meta, "capital": cap}
    return None


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


def extract_fair_value(facts: list[Fact]) -> dict:
    """{period_end: {"assets": {...}, "liabilities": {...}}} — the recurring
    ASC 820 fair-value hierarchy from a filing's iXBRL.

    Each side carries l1/l2/l3 (as tagged), total (= sum of tagged levels), grand
    (the filer's tagged grand total, if any), netting (grand − total, the
    derivative/collateral reconciling item), l3_pct (L3 ÷ total) and _reconciles
    (whether the level sum ties the tagged grand within tolerance). A side with no
    clean level total is omitted (n/a) — never summed from components."""
    prio = {c: (side, i)
            for side, cs in _FV_TOTAL_CONCEPTS.items()
            for i, c in enumerate(cs)}
    # side -> period -> slot -> (priority, value); lowest priority wins.
    grab: dict = {}
    for f in facts:
        hit = prio.get(f.concept.split(":")[-1])
        if hit is None or not _fv_clean_total(f.members):
            continue
        side, p = hit
        slot = _fv_level(f.members) or "grand"
        cur = grab.setdefault(side, {}).setdefault(f.period_end, {}).get(slot)
        if cur is None or p < cur[0]:
            grab[side][f.period_end][slot] = (p, f.value)

    out: dict = {}
    for side, byperiod in grab.items():
        for period, slots in byperiod.items():
            levels = {lv: slots[lv][1] for lv in ("L1", "L2", "L3") if lv in slots}
            if not levels:
                continue
            total = sum(levels.values())
            grand = slots["grand"][1] if "grand" in slots else None
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
