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
               "DebtSecuritiesAvailableForSaleAmortizedCost",
               "AvailableForSaleSecuritiesAmortizedCost"),
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
            "LoansAndLeasesReceivableAllowance",
            "AllowanceForLoanAndLeaseLossesRealEstate"),
    "loans_gross": ("FinancingReceivableExcludingAccruedInterestBeforeAllowanceForCreditLoss",
                    "FinancingReceivableBeforeAllowanceForCreditLoss",
                    "NotesReceivableGross",
                    "LoansAndLeasesReceivableGrossCarryingAmount"),
    "loans_net": ("FinancingReceivableExcludingAccruedInterestAfterAllowanceForCreditLoss",
                  "FinancingReceivableAfterAllowanceForCreditLoss",
                  "LoansAndLeasesReceivableNetReportedAmount",
                  "NotesReceivableNet"),
    "nonaccrual": ("FinancingReceivableExcludingAccruedInterestNonaccrual",
                   "FinancingReceivableRecordedInvestmentNonaccrualStatus"),
    "writeoff": ("FinancingReceivableExcludingAccruedInterestAllowanceForCreditLossWriteoff",
                 "FinancingReceivableAllowanceForCreditLossesWriteoff",
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


def extract_credit_quality(facts: list[Fact]) -> dict:
    """{period_end: {...}} — the as-reported CECL allowance & asset-quality summary
    from a filing's iXBRL: allowance for credit losses (ACL), gross/net loans,
    nonaccrual loans, net charge-offs (writeoff − recovery, or the directly tagged
    net), provision, and the derived ratios (ACL ÷ loans, nonaccrual ÷ loans, ACL
    coverage of nonaccruals, NCO ÷ loans).

    Reconcile-gated: a period renders only with a tagged ACL AND gross loans; when
    net loans are also tagged, net + ACL must tie gross within 1% or the trio is
    rejected (n/a). NCO uses the tagged net writeoff-after-recovery, else
    writeoff − recovery when both are present, else None."""
    # Use ONLY the filing's current (latest) loan-footnote date — never fall back
    # to a prior-year comparative, which would surface stale figures as current.
    periods: set = set()
    anchors = set(_CQ_CONCEPTS["acl"]) | set(_CQ_CONCEPTS["loans_gross"])
    for f in facts:
        if f.concept.split(":")[-1] in anchors and not f.members:
            periods.add(f.period_end)
    out: dict = {}
    if periods:
        period = max(periods)
        acl = _sec_pick(facts, _CQ_CONCEPTS["acl"], period)
        gross = _sec_pick(facts, _CQ_CONCEPTS["loans_gross"], period)
        net = _sec_pick(facts, _CQ_CONCEPTS["loans_net"], period)
        # Render only with ACL AND gross loans at the current date, and (when a net
        # loan figure is tagged) net + ACL tying gross — else n/a.
        if (acl is not None and gross not in (None, 0)
                and not (net is not None and abs((net + acl) - gross) > max(abs(gross) * 0.01, 5e6))):
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
        ckey = f"credit_quality:v1:{meta['accession']}"
        cq = cache.get(ckey)
        if cq is None:
            try:
                cq = extract_credit_quality(instance_facts(meta))
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
    nonint_inc = ann(("NoninterestIncome",))
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
        return (cur, True) if cur is not None else (None, True)

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


def extract_segments(facts: list[Fact]) -> dict:
    """{fiscal_year_end: {"segments": [...], ...}} — the as-reported business-segment
    net income (with revenue and assets when tagged) reconstructed from a filing's
    iXBRL, presented GAAP-style: each reportable segment's directly tagged figures
    plus an explicit 'Corporate / other & reconciling items' residual (consolidated
    − Σ reportable) so the segments tie the consolidated total. The net-income
    measure (NetIncomeLoss or ProfitLoss) is chosen to match the consolidated tag.
    n/a unless ≥2 reportable segments and a consolidated total are tagged for the
    latest fiscal year."""
    fy_end = None
    for f in facts:
        if (f.concept.split(":")[-1] in ("NetIncomeLoss", "ProfitLoss") and not f.members
                and f.period_start and 330 <= _days(f.period_start, f.period_end) <= 400):
            if fy_end is None or f.period_end > fy_end:
                fy_end = f.period_end
    if fy_end is None:
        return {}

    def _annual(f):
        return f.period_start and f.period_end == fy_end and 330 <= _days(f.period_start, fy_end) <= 400

    # Choose the NI concept that the consolidated total uses, then read segments
    # with the SAME concept so the residual is consistent.
    for ni_concept in ("NetIncomeLoss", "ProfitLoss"):
        consol = None
        for f in facts:
            if f.concept.split(":")[-1] == ni_concept and not f.members and _annual(f):
                consol = f.value
                break
        if consol is None:
            continue
        seg_ni: dict = {}
        for f in facts:
            if f.concept.split(":")[-1] == ni_concept and _annual(f):
                seg = _seg_of(f.members)
                if seg:
                    seg_ni.setdefault(seg, f.value)
        if len(seg_ni) < 2:
            continue
        # Optional per-segment revenue and assets (same segment marker).
        seg_rev: dict = {}
        seg_assets: dict = {}
        for f in facts:
            seg = _seg_of(f.members)
            if not seg or seg not in seg_ni:
                continue
            c = f.concept.split(":")[-1]
            if c in ("Revenues", "RevenuesNetOfInterestExpense") and _annual(f):
                seg_rev.setdefault(seg, f.value)
            elif c == "Assets" and f.period_start is None and f.period_end == fy_end:
                seg_assets.setdefault(seg, f.value)
            elif c == "AssetsAverageOutstanding" and _annual(f):
                seg_assets.setdefault(seg, f.value)
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
        # an "All Other" = consolidated) → reject (try the other measure, else n/a),
        # never a misleading breakdown.
        if consol and abs(residual) > abs(consol):
            continue
        return {fy_end: {
            "segments": segments, "consolidated_net_income": consol,
            "reconciling_residual": residual, "ni_measure": ni_concept,
        }}
    return {}


def segments_for(cik) -> dict | None:
    """Cached business-segment summary for a company from its own latest 10-K.
    Returns {"meta": {...}, "segments": {...}} or None."""
    if not cik:
        return None
    from data import cache
    meta = latest_filing(cik, ("10-K",))
    if not meta:
        return None
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
            print(f"[sec_scraper] segments failed for cik {cik}: {type(e).__name__}: {e}")
            seg = {}
    if seg:
        return {"meta": meta, "segments": seg}
    return None
