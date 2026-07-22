"""As-reported composition tables (loan / deposit) reconstructed from a filing's
inline-XBRL FACTS + its dimensional STRUCTURE (MetaLinks labels + definition-
linkbase member hierarchy) — NOT by scraping the rendered R-file.

Many banks have no standalone "composition" table: the category breakdown is
embedded in a combined "categories & past due" / "credit quality" disclosure, so
the composition is reconstructed from the tagged facts and reconcile-gated.

LOANS (member-axis): the loan-balance concept's facts carrying EXACTLY ONE leaf
member on a portfolio-segment / class axis are the per-category balances; the
undimensioned fact is the total. Three independent de-duplication passes resolve
the messy real-world tagging, in order:
  1. synonym-label collapse — a filer often tags the SAME category under two
     member qnames that resolve to the SAME terseLabel (AROW:
     CommercialLoanMember and CommercialPortfolioSegmentMember both -> "Commercial").
     Equal-label, equal-value members are collapsed to one row.
  2. linkbase-descendant drop — drop a member if a finer member it CONTAINS (per
     the definition linkbase) is also tagged (parent+child double-count).
  3. value-aggregate drop — many filers encode the hierarchy NOWHERE machine-
     readable (CFR lists every member flat under one domain). But the filer's own
     values prove it: a member whose value equals the sum of >=2 other (smaller)
     tagged members IS an aggregate of them — drop it, keep the leaves. The
     reconcile-gate makes this safe: a wrong drop breaks the sum-to-total and the
     candidate is rejected, so only the correct leaf set survives.

DEPOSITS (concept-based): deposits are tagged as SEPARATE us-gaap concepts
(NoninterestBearingDepositLiabilities, DepositsSavingsDeposits, TimeDeposits, …),
each undimensioned, NOT as one concept x type-axis. The composition is the set of
product-slot concepts that reconcile to the `Deposits` total; the same value-
aggregate drop removes a parent (InterestBearingDepositLiabilities) when its finer
components are tagged.

RECONCILE-GATE (both): the kept rows must sum to the undimensioned total within
1% or the candidate is rejected; the largest reconciling candidate wins. A
composition that does not reconcile renders n/a, never a guess. See
docs/DATA-SOURCING-ARCHITECTURE.md and the company-reported-faithful-extraction
memory.
"""
from __future__ import annotations

import html
import json
import re
from collections import defaultdict

from data.sec_filing_scraper import _get, latest_filing, instance_facts

# ── LOAN patterns ────────────────────────────────────────────────────────────
# The loan axis carrying the by-category breakdown. "PortfolioSegment" (BCML,
# PNFP, AROW), "ClassOfFinancingReceivable" (CFR's
# FinancingReceivableRecordedInvestmentByClassOfFinancingReceivableAxis),
# "ReceivableType" / "FinancingReceivableByClass".
_LOAN_AXIS = re.compile(
    r"PortfolioSegment|ClassOfFinancingReceivable|FinancingReceivableByClass|ReceivableType",
    re.I)
_LOANISH = re.compile(r"FinancingReceivable|LoansAndLeases|NotesReceivable|LoansReceivable", re.I)
# NB: the loan-BALANCE concept is "…ExcludingAccruedInterestBeforeAllowanceForCreditLoss"
# — it contains "Allowance" but IS what we want, so don't reject on "Allowance".
# Reject the allowance/credit concepts and the vintage (by-origination-year) tables.
# NB: the allowance lookbehind must be case-sensitive to anchor on "Before"/
# "After"; the rest is case-insensitive, so allowance is matched separately.
_BAD_CONCEPT = re.compile(
    r"ChargeOff|Charged|Recover|PastDue|Nonaccrual|Provision|Impair|Modific|"
    r"Delinquen|InterestIncome|Yield|Vintage|Originated|CurrentFiscalYear|"
    r"WriteOff|Writeoff|FeesAndLoanInProcess|CreditLossExpense|"
    # flow / commitment / held-for-sale / average concepts are NOT the period-end
    # loan balance (WFC tags a $4.6B held-for-sale reclassification line and a
    # $839B commitments line that are loan-ish but not the book).
    r"Commitment|HeldForSale|Reclassif|Average|Purchase|Proceeds|Payments|Sale|Sold|"
    r"Percentage", re.I)
# The STOCK allowance concept ("FinancingReceivableAllowanceForCreditLoss…") is
# not a loan balance and must not masquerade as a composition. The loan-balance
# concepts carry "BeforeAllowanceForCreditLoss" (gross) or "AfterAllowanceFor
# CreditLoss" (net) — allow only those two; reject a bare AllowanceForCreditLoss.
_ALLOWANCE_CONCEPT = re.compile(r"(?<!Before)(?<!After)AllowanceForCreditLoss")

# A member that is NOT a composition category — a credit-quality grade, past-due
# bucket, performance status, maturity bucket, geography, collateral cut or fair-
# value level that can appear on the same axis. Dropped before reconciling so it
# can't pollute the category set.
_NON_COMP_MEMBER = re.compile(
    r"PastDue|NotPastDue|Nonaccrual|NonAccrual|Nonperform|Nonperforming|Performing|"
    r"Grade|RiskRating|Watch|Substandard|SpecialMention|Doubtful|\bPass\b|Criticized|"
    r"Classified|FairValueInputs|Collateral|Recourse|Vintage|Current\b|"
    r"30To59|30to59|60To89|60to89|90Days|90OrMore|DaysPastDue|"
    r"IndividuallyEvaluated|CollectivelyEvaluated|Acquired|PurchasedCredit|"
    r"FixedRate|VariableRate|Domestic\b|Foreign\b", re.I)

# ── DEPOSIT patterns ─────────────────────────────────────────────────────────
# Deposits are tagged as distinct undimensioned us-gaap concepts (NOT one concept
# x type-axis), forming a 2-level tree: Deposits = Noninterest-bearing + Interest-
# bearing, and Interest-bearing = checking/NOW + savings + money-market + time.
# So the composition is reconstructed by concept selection, not member dedup.
_DEPOSIT_TOTALS = ["Deposits", "DepositsDomestic"]
_NIB_CONCEPTS = [
    "NoninterestBearingDepositLiabilities", "NoninterestBearingDepositLiabilitiesDomestic",
    "NoninterestBearingDomesticDepositDemand",
    "NoninterestBearingDepositLiabilitiesIncludingRelatedParties", "DemandDepositAccounts"]
# The full interest-bearing total (parent of the products) — the 2-way partner.
_IB_PARENT_CONCEPTS = [
    "InterestBearingDepositLiabilities", "InterestBearingDepositLiabilitiesDomestic",
    "InterestBearingCustomerDepositLiabilities"]
# Interest-bearing PRODUCT slots (each: ordered synonym concepts, first present
# wins). InterestBearingDepositLiabilitiesDomestic sits LAST in the checking slot
# because at some filers (PNFP) it is the interest-bearing transaction bucket, but
# at others (ACNB/ASB) it is the IB parent — a product whose value equals the full
# interest-bearing remainder is recognised as the parent and excluded.
_DEPOSIT_PRODUCTS = [
    ("checking", ["DepositsNegotiableOrderOfWithdrawalNOW",
                  "InterestBearingDomesticDepositNegotiableOrderOfWithdrawalNOW",
                  "InterestBearingDomesticDepositChecking",
                  "InterestBearingDomesticDepositDemand",
                  "InterestBearingDepositLiabilitiesDomestic"]),
    ("savings", ["InterestBearingDomesticDepositSavings", "DepositsSavingsDeposits",
                 "SavingsDeposits"]),
    ("moneymarket", ["InterestBearingDomesticDepositMoneyMarket",
                     "DepositsMoneyMarketDeposits", "MoneyMarketDeposits"]),
    ("savmm", ["DepositsSavingsAndMoneyMarketDeposits",
               "InterestBearingDomesticDepositSavingsAndMoneyMarket"]),
    ("time", ["TimeDeposits", "InterestBearingDomesticDepositTimeDeposits",
              "DepositsTimeDeposits"]),
]

_RECONCILE_TOL = 0.01      # kept rows must sum to total within 1%


def _filing_dir(cik, accession) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/"


def _metalinks(base: str) -> dict:
    return json.loads(_get(base + "MetaLinks.json"))


def _member_labels(meta: dict) -> dict:
    """{tag_localname: filer's terseLabel} from MetaLinks — the bank's own wording
    for each member ('Commercial', 'Commercial Real Estate') AND line-item concept
    (used to label deposit concepts)."""
    inst = meta["instance"][next(iter(meta["instance"]))]
    out = {}
    for key, tag in inst.get("tag", {}).items():
        local = tag.get("localname") or key.split("_")[-1]
        roles = (tag.get("lang", {}).get("en-us", {}) or {}).get("role", {})
        label = roles.get("terseLabel") or roles.get("label") or local
        # strip the "[Member]"/"[Domain]" boilerplate the standard label carries,
        # and unescape XML entities the filer's label text carries ("&amp;" -> "&")
        label = re.sub(r"\s*\[(member|domain)\]\s*$", "", label, flags=re.I).strip()
        out[local] = html.unescape(label)
    return out


def _def_linkbase_url(meta: dict, base: str) -> str | None:
    """The definition-linkbase file URL from the MetaLinks DTS list."""
    inst = meta["instance"][next(iter(meta["instance"]))]
    for fn in inst.get("dts", {}).get("definitionLink", {}).get("local", []):
        return base + fn
    return None


def _member_children(def_xml: bytes) -> dict:
    """{parent_member_localname: set(child_member_localnames)} from the definition
    linkbase's domain-member arcs — the dimensional hierarchy. Used to drop a
    member from a composition when one of its OWN descendants is also tagged."""
    from lxml import etree
    root = etree.fromstring(def_xml)
    XL = "{http://www.w3.org/1999/xlink}"
    children = defaultdict(set)

    def _tag(el):
        # lxml gives comments/PIs a non-string (callable) tag — skip those.
        return el.tag if isinstance(el.tag, str) else ""

    for link in root.iter():
        if not _tag(link).endswith("}definitionLink"):
            continue
        loc_local = {}
        for loc in link.iter():
            if _tag(loc).endswith("}loc"):
                href = loc.get(XL + "href", "")
                loc_local[loc.get(XL + "label")] = href.split("#")[-1].split("_")[-1]
        for arc in link.iter():
            if not _tag(arc).endswith("}definitionArc"):
                continue
            if arc.get(XL + "arcrole") != "http://xbrl.org/int/dim/arcrole/domain-member":
                continue
            frm = loc_local.get(arc.get(XL + "from"))
            to = loc_local.get(arc.get(XL + "to"))
            if frm and to and frm != to:
                children[frm].add(to)
    return children


def _has_present_descendant(member: str, present: set, children: dict,
                            _seen: set | None = None) -> bool:
    """True if any (transitive) child of `member` is itself in `present` — i.e.
    `member` is an aggregate of finer members that are also tagged here."""
    _seen = _seen if _seen is not None else set()
    for c in children.get(member, ()):
        if c in _seen:
            continue
        _seen.add(c)
        if c in present or _has_present_descendant(c, present, children, _seen):
            return True
    return False


def _facts_by_concept(facts) -> dict:
    out = defaultdict(list)
    for f in facts:
        out[f.concept.split(":")[-1]].append(f)
    return out


# ── reconcile / aggregate-detection primitives ──────────────────────────────
def _reconciles(rows: dict, total: float) -> bool:
    """Kept rows (>=2) sum to the disclosed total within tolerance."""
    if len(rows) < 2 or not total:
        return False
    return abs(sum(rows.values()) - total) / abs(total) <= _RECONCILE_TOL


def _subset_sums_to(values: list, target: float, tol: float,
                    min_size: int = 2, budget: int = 40000) -> bool:
    """True if some subset of `values` (>= min_size elements) sums to `target`
    within `tol`. Positive values only; bounded DFS with suffix-sum pruning and a
    step budget so a pathological set can't hang (returns False on budget exhaust
    — i.e. 'not provably an aggregate', the safe default)."""
    vals = sorted((v for v in values if v > 0), reverse=True)
    n = len(vals)
    suffix = [0.0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix[i] = suffix[i + 1] + vals[i]
    steps = [0]

    def dfs(i: int, remaining: float, count: int) -> bool:
        steps[0] += 1
        if steps[0] > budget:
            return False
        if abs(remaining) <= tol and count >= min_size:
            return True
        if i >= n or remaining < -tol or suffix[i] < remaining - tol:
            return False
        if vals[i] <= remaining + tol and dfs(i + 1, remaining - vals[i], count + 1):
            return True
        return dfs(i + 1, remaining, count)

    return dfs(0, target, 0)


def _finest_partition(rows: dict, total: float) -> dict | None:
    """Recover the leaf set of a flat-tagged hierarchy from values alone (CFR
    lists parents AND children flat under one domain, with no linkbase nesting).

    Returns the FINEST subset whose values exactly partition the total — i.e. the
    largest-cardinality subset summing to `total` — but ONLY when it is provably
    the right one:
      • UNIQUE: there is a single max-cardinality subset summing to the total
        (an ambiguous tie means we can't tell which split is real → None);
      • CONSISTENT: every member NOT in the chosen set is itself a sub-sum of >=2
        chosen leaves (it is a genuine aggregate of them, not an unrelated line).
    A coincidental wrong partition almost never satisfies both, so this is safe;
    when it can't, the caller renders n/a. Bounded by a node budget — a set too
    tangled to resolve cleanly returns None rather than hang."""
    # ASCENDING so the take-first DFS accumulates the small leaves first and finds
    # the finest (highest-cardinality) partition immediately — that maximises
    # best_card early, so the cardinality prune then cuts every coarser branch.
    items = sorted(((k, v) for k, v in rows.items() if v > 0), key=lambda kv: kv[1])
    keys = [k for k, _ in items]
    arr = [v for _, v in items]
    n = len(arr)
    if n < 2:
        return None
    suffix = [0.0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix[i] = suffix[i + 1] + arr[i]
    # TIGHT tol: iXBRL values are exact, so a true partition matches the total to
    # well within 0.01%. A loose tol would let near-equal members swap in/out and
    # make the finest partition ambiguous (CFR has Home-Equity 1068 vs Home-Equity-
    # Loan 1036 vs Energy-total 1095 — all within a loose band) → wrongly n/a.
    tol = max(abs(total) * 1e-4, 5e5)
    best_card = [1]
    best_sets: set = set()
    budget = [2000000]

    def dfs(i: int, rem: float, chosen: tuple):
        budget[0] -= 1
        if budget[0] < 0:
            return
        if abs(rem) <= tol and len(chosen) >= 2:
            if len(chosen) > best_card[0]:
                best_card[0] = len(chosen)
                best_sets.clear()
            if len(chosen) == best_card[0] and len(best_sets) <= 4:
                best_sets.add(chosen)
            return
        if i >= n or rem < -tol or suffix[i] < rem - tol:
            return
        # prune: even taking every remaining element can't beat the best card
        if len(chosen) + (n - i) < best_card[0]:
            return
        if arr[i] <= rem + tol:
            dfs(i + 1, rem - arr[i], chosen + (i,))
        dfs(i + 1, rem, chosen)

    dfs(0, total, ())
    if budget[0] < 0 or len(best_sets) != 1:
        return None                      # exhausted / ambiguous → safe n/a
    idxs = next(iter(best_sets))
    chosen = {keys[i]: arr[i] for i in idxs}
    chosen_vals = list(chosen.values())
    for k, v in zip(keys, arr):          # consistency: every dropped member is a
        if k in chosen:                  # sub-sum of the chosen leaves
            continue
        if not _subset_sums_to(chosen_vals, v, max(abs(v) * 1e-4, 5e5), min_size=2):
            return None
    return chosen


def _dedup_synonyms(rows: dict, labels: dict) -> dict:
    """Collapse members that resolve to the SAME display label AND carry an equal
    value — a filer tagging one category under two synonym member qnames (AROW:
    CommercialLoanMember + CommercialPortfolioSegmentMember -> 'Commercial', both
    165.7M). Equal value is required so a genuine parent/child sharing a label is
    NOT merged (the linkbase / value-aggregate pass handles those)."""
    by_label: dict = defaultdict(list)
    for m, v in rows.items():
        by_label[labels.get(m, m)].append((m, v))
    out = {}
    for _label, group in by_label.items():
        if len(group) == 1:
            m, v = group[0]
            out[m] = v
            continue
        vmax = max(v for _m, v in group)
        # collapse only if all members in the label-group are ~equal (true
        # synonyms); otherwise keep them all (different real categories that
        # happen to share a terse label — let reconcile sort it out).
        if all(abs(v - vmax) <= max(abs(vmax) * 0.01, 1e6) for _m, v in group):
            keep_m = max(group, key=lambda mv: mv[1])[0]
            out[keep_m] = vmax
        else:
            for m, v in group:
                out[m] = v
    return out


def _collapse_equal_value(rows: dict) -> dict:
    """Collapse members carrying the EXACT SAME value to one representative — a
    filer tagging the SAME category under two member qnames whose labels DIFFER
    (BANR: SmallBalanceCommercialRealEstateLoansMember and SmallBalanceCREMember,
    both 1,212,357,000; 'Land and Land Development Type' and 'Land and Land
    Improvements', both 433,678,000). `_dedup_synonyms` keys on the DISPLAY label
    and so misses these; equal value is the only reliable synonym signal here.
    Caller is reconcile-gated: collapsing two genuinely-distinct equal-value leaves
    would drop a needed value and the sum would fall short of the total, so the
    candidate is rejected — only a true double-count survives the gate."""
    by_value: dict = defaultdict(list)
    for m, v in rows.items():
        by_value[v].append(m)
    return {ms[0]: v for v, ms in by_value.items()}


def _unique_reconciling_subset(rows: dict, total: float, budget: int = 200000) -> dict | None:
    """The subset of `rows` (>=2 members) that sums to `total` — but ONLY when it
    is the UNIQUE such subset across all cardinalities. Recovers a flat-tagged
    parent+PARTIAL-children set that `_finest_partition` can't: FBK tags
    TotalCommercialLoansMember (9.17B) + ConsumerPortfolioSegmentMember (3.22B) =
    the 12.38B book, PLUS three commercial sub-types (Commercial, Construction,
    Consumer-and-other) that do NOT fully decompose the commercial total — so no
    finest partition exists, but exactly one subset {commercial-total,
    consumer-total} reconciles. Uniqueness is the safety: a coincidental wrong
    subset almost never is the SOLE reconciling one, and any ambiguity (e.g. equal
    members that can swap) returns None. Bounded by a step budget (exhaust -> None,
    the safe default)."""
    items = sorted(((k, v) for k, v in rows.items() if v > 0), key=lambda kv: kv[1])
    keys = [k for k, _ in items]
    arr = [v for _, v in items]
    n = len(arr)
    if n < 2 or not total:
        return None
    suffix = [0.0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix[i] = suffix[i + 1] + arr[i]
    # TIGHT tol (matches _finest_partition): iXBRL values are exact, so a true
    # subset ties the total to well within 0.01%. A loose band would let near-equal
    # members swap in and out and make the reconciling subset spuriously ambiguous.
    tol = max(abs(total) * 1e-4, 5e5)
    found: list = []
    steps = [budget]

    def dfs(i: int, rem: float, chosen: tuple) -> bool:
        steps[0] -= 1
        if steps[0] < 0:
            return True                       # budget exhausted — abort
        if abs(rem) <= tol and len(chosen) >= 2:
            found.append(chosen)
            return len(found) > 1             # a 2nd solution => ambiguous, stop
        if i >= n or rem < -tol or suffix[i] < rem - tol:
            return False
        if arr[i] <= rem + tol and dfs(i + 1, rem - arr[i], chosen + (i,)):
            return True
        return dfs(i + 1, rem, chosen)

    dfs(0, total, ())
    if steps[0] < 0 or len(found) != 1:
        return None                           # exhausted / ambiguous => safe n/a
    return {keys[i]: arr[i] for i in found[0]}


def _resolve_rows(allrows: dict, total: float, labels: dict, children: dict) -> dict | None:
    """Reduce a raw {member: value} breakdown to the reconciling leaf set, or None.
    Staged (each reconcile-gated; first reconciling candidate wins):
    blocklist -> synonym dedup -> equal-value collapse -> linkbase-descendant drop
    -> value-finest-partition -> unique-reconciling-subset."""
    work = {m: v for m, v in allrows.items() if not _NON_COMP_MEMBER.search(m)}
    work = _dedup_synonyms(work, labels)
    if len(work) < 2:
        return None
    # Collapse same-value, different-label synonym duplicates (BANR). Gated: if the
    # collapse over-merges a genuine leaf the sum falls short and the gate rejects.
    collapsed = _collapse_equal_value(work)
    if len(collapsed) >= 2 and _reconciles(collapsed, total):
        return collapsed
    present = set(work)
    r1 = {m: v for m, v in work.items()
          if not _has_present_descendant(m, present, children)}
    if _reconciles(r1, total):
        return r1
    # no (usable) linkbase hierarchy — recover the leaf set from values alone
    part = _finest_partition(work, total)
    if part and _reconciles(part, total):
        return part
    # flat-tagged aggregate + PARTIAL children (FBK): no finest partition, but a
    # single subset reconciles — accept it ONLY when it is provably unique.
    uniq = _unique_reconciling_subset(work, total)
    if uniq and _reconciles(uniq, total):
        return uniq
    return None


# ── LOAN composition ─────────────────────────────────────────────────────────
def extract_loan_composition(facts, labels: dict, children: dict) -> dict | None:
    """{period_end: {"total": v, "rows": [(label, value), …]}} — the by-category
    loan composition from the loan-balance concept whose single-member segment
    facts reconcile to the undimensioned total. None when nothing reconciles."""
    return _extract_member_composition(
        facts, labels, children,
        concept_ok=lambda c: (_LOANISH.search(c) and not _BAD_CONCEPT.search(c)
                              and not _ALLOWANCE_CONCEPT.search(c)),
        axis_ok=lambda a: bool(_LOAN_AXIS.search(a)))


# A rendered composition must cover at least this fraction of the LARGEST clean
# loan-balance total disclosed in the filing. Guards against a giant filer (WFC's
# $986B book doesn't cleanly partition, but a $4.6B sub-line does) shipping a tiny
# slice as if it were the whole composition.
_BOOK_COVERAGE_MIN = 0.5


def _extract_member_composition(facts, labels, children, concept_ok, axis_ok) -> dict | None:
    """Shared member-axis composition extractor (loans). Returns the by-category
    composition for EVERY fiscal period the filing tags one (a 10-K carries the
    current FY plus prior comparatives), keyed newest-first, or None.

    Per period: the largest reconciling candidate across concepts/axes wins (ties
    break to more rows). A period is then KEPT only if its chosen total covers at
    least `_BOOK_COVERAGE_MIN` of the LARGEST clean loan-balance total disclosed
    ANYWHERE in the filing — so a period that tags only a sub-line (e.g. a
    comparative year where the full book isn't tagged undimensioned but a tiny
    modified-loans subtotal is) is dropped rather than shipped as a wrong number.
    A dropped period renders n/a, never a guess."""
    # period -> best (total, n_rows, rows); plus the global largest clean total.
    best_by_period: dict = {}
    book_max = 0.0  # largest clean undimensioned loan-balance total in the filing
    for concept, fs in _facts_by_concept(facts).items():
        if not concept_ok(concept):
            continue
        # period -> {"totals": set, "axes": {axis: {member: value}}}
        per: dict = defaultdict(lambda: {"totals": set(), "axes": defaultdict(dict)})
        for f in fs:
            if not f.members:
                if f.value:
                    per[f.period_end]["totals"].add(f.value)
            elif len(f.members) == 1:
                axis, mem = next(iter(f.members.items()))
                if axis_ok(axis.split(":")[-1]):
                    per[f.period_end]["axes"][axis.split(":")[-1]][mem.split(":")[-1]] = f.value
        for period, d in per.items():
            if not d["totals"]:
                continue
            total = max(d["totals"], key=abs)
            book_max = max(book_max, abs(total))
            for _axis, allrows in d["axes"].items():
                rows = _resolve_rows(allrows, total, labels, children)
                if not rows:
                    continue
                cand = (total, len(rows), rows)
                cur = best_by_period.get(period)
                if cur is None or cand[0] > cur[0] or (cand[0] == cur[0] and cand[1] > cur[1]):
                    best_by_period[period] = cand
    out = {}
    for period in sorted(best_by_period, reverse=True):       # newest period first
        total, _n, rows = best_by_period[period]
        if abs(total) < _BOOK_COVERAGE_MIN * book_max:
            continue                       # sub-slice for this period → drop it
        ordered = sorted(rows.items(), key=lambda kv: -kv[1])
        # (display label, value, MEMBER QNAME). The member is the filer's own
        # XBRL axis member and is STABLE across its 10-K and 10-Qs even when the
        # label wording changes ("Small balance CRE" vs "Small Balance Commercial
        # Real Estate Loans"), so the UI can fold those wording variants safely.
        out[period] = {"total": total,
                       "rows": [(labels.get(m, m), v, m) for m, v in ordered]}
    return out or None


# ── DEPOSIT composition ──────────────────────────────────────────────────────
def _first_present(concepts: list, vals: dict):
    """(concept, value) for the first concept in `concepts` tagged at this period."""
    for c in concepts:
        if c in vals and vals[c]:
            return c, vals[c]
    return None, None


def _deposit_rows_for_total(vals: dict, total: float) -> list | None:
    """[(concept, value), …] reconciling to `total`, finest first: the product
    split (noninterest + checking/NOW + savings + money-market + time) when it
    ties the interest-bearing remainder, else the interest 2-way (noninterest +
    interest-bearing). None when neither reconciles."""
    nib_c, nib_v = _first_present(_NIB_CONCEPTS, vals)
    if nib_v is None:
        return None
    ib_remainder = total - nib_v
    tol = max(abs(total) * _RECONCILE_TOL, 1e6)
    if ib_remainder <= tol:
        return None
    # product split — one concept per slot, excluding any whose value equals the
    # whole interest-bearing remainder (that concept is the IB parent, not a leaf).
    products = []
    for _slot, syns in _DEPOSIT_PRODUCTS:
        c, v = _first_present(syns, vals)
        if v is not None and abs(v - ib_remainder) > tol:
            products.append((c, v))
    if products and abs(sum(v for _c, v in products) - ib_remainder) <= tol:
        return [(nib_c, nib_v)] + products
    # interest 2-way — the noninterest + the full interest-bearing parent
    ibp_c, ibp_v = _first_present(_IB_PARENT_CONCEPTS, vals)
    if ibp_v is not None and abs(ibp_v - ib_remainder) <= tol:
        return [(nib_c, nib_v), (ibp_c, ibp_v)]
    return None


def extract_deposit_composition(facts, labels: dict, children: dict) -> dict | None:
    """{period_end: {"total": v, "rows": [(label, value), …]}} — the deposit
    composition reconstructed from the per-product deposit concepts that reconcile
    to the disclosed total, for EVERY fiscal period the filing tags one (a 10-K
    carries the current FY plus prior comparatives), keyed newest-first. None when
    no period reconciles. Each period is independently reconcile-gated, so a year
    that doesn't tag a clean product split renders n/a, never a guess (filers
    routinely change granularity year to year — e.g. a coarse interest-bearing
    2-way one year and a full product split the next)."""
    undim: dict = defaultdict(dict)          # period -> {concept_local: value}
    for f in facts:
        if f.members or not f.value:
            continue
        undim[f.period_end].setdefault(f.concept.split(":")[-1], f.value)

    best_by_period: dict = {}  # period -> (total, n_rows, rows[(concept, value)])
    for period, vals in undim.items():
        for total_concept in _DEPOSIT_TOTALS:
            total = vals.get(total_concept)
            if not total:
                continue
            rows = _deposit_rows_for_total(vals, total)
            if not rows or not _reconciles(dict(rows), total):
                continue
            cand = (total, len(rows), rows)
            cur = best_by_period.get(period)
            if cur is None or cand[0] > cur[0] or (cand[0] == cur[0] and cand[1] > cur[1]):
                best_by_period[period] = cand
    if not best_by_period:
        return None
    out = {}
    for period in sorted(best_by_period, reverse=True):       # newest period first
        total, _n, rows = best_by_period[period]
        ordered = sorted(rows, key=lambda cv: -cv[1])
        # (display label, value, MEMBER/CONCEPT QNAME) — see the loan extractor.
        out[period] = {"total": total,
                       "rows": [(labels.get(c, c), v, c) for c, v in ordered]}
    return out


# ── public per-CIK entry points ──────────────────────────────────────────────
def _fetch_meta(meta):
    """Fetch ONE filing's iXBRL: (meta, facts, labels, children) or None."""
    if not meta:
        return None
    base = _filing_dir(meta["cik"], meta["accession"])
    facts = instance_facts(meta)       # multi-document aware (USB/WFC/TFC/…)
    if not facts:
        return None
    ml = _metalinks(base)
    labels = _member_labels(ml)
    def_url = _def_linkbase_url(ml, base)
    children = _member_children(_get(def_url)) if def_url else {}
    return meta, facts, labels, children


def _fetch(cik):
    """Fetch a company's latest 10-K iXBRL once: (meta, facts, labels, children)
    or None. Shared by the per-kind helpers and the dual extractor so a single
    filing fetch+parse serves both loan and deposit."""
    if not cik:
        return None
    return _fetch_meta(latest_filing(cik, ("10-K",)))


def _composition_for(cik, extractor) -> dict | None:
    """Run one `extractor(facts, labels, children)`; {"meta", "composition"} or None."""
    got = _fetch(cik)
    if not got:
        return None
    meta, facts, labels, children = got
    comp = extractor(facts, labels, children)
    return {"meta": meta, "composition": comp} if comp else None


def _compositions_extract_cached(meta) -> dict | None:
    """Both compositions for ONE filing, cached per accession (immutable):
    {"loan": comp|None, "deposit": comp|None} or None when the filing has no
    usable iXBRL. A transient fetch failure is never cached."""
    from data import cache
    # v2 (2026-07-14): rows carry the member QName as a third element.
    ckey = f"compositions_filing:v2:{meta['accession']}"
    got = cache.get(ckey)
    if got is not None:
        return got or None
    fetched = _fetch_meta(meta)
    if not fetched:
        return None                      # transient — retryable, never cached
    _m, facts, labels, children = fetched
    out = {"loan": extract_loan_composition(facts, labels, children),
           "deposit": extract_deposit_composition(facts, labels, children)}
    try:
        cache.put(ckey, out)
    except Exception:
        pass
    return out


def compositions_multiquarter_for(cik, n_quarters: int = 8) -> dict | None:
    """Quarter-end loan + deposit composition stitched from recent 10-Qs +
    10-Ks: each filing's composition note covers its own period end (sometimes
    a comparative); reconcile gates are per filing-period exactly as in the
    annual path; newest filing wins a shared period; capped to the newest
    n_quarters period-ends per kind. Returns {"meta", "loan", "deposit"} in
    the compositions_for shape, or None."""
    if not cik:
        return None
    from data.sec_statements import _recent_filing_metas
    metas = _recent_filing_metas(cik, ("10-K", "10-Q"), n_quarters + 2)
    if not metas:
        return None
    merged = {"loan": {}, "deposit": {}}
    latest_meta = None
    for meta in metas:                                # newest-first
        got = _compositions_extract_cached(meta)
        if not got:
            continue
        if latest_meta is None:
            latest_meta = meta
        for kind in ("loan", "deposit"):
            for period, entry in (got.get(kind) or {}).items():
                if period not in merged[kind]:        # newest filing wins
                    merged[kind][period] = entry
    if latest_meta is None or not (merged["loan"] or merged["deposit"]):
        return None
    out = {"meta": latest_meta}
    for kind in ("loan", "deposit"):
        keep = sorted(merged[kind], reverse=True)[:n_quarters]
        out[kind] = ({p: merged[kind][p] for p in keep}) if keep else None
    return out


def compositions_for(cik) -> dict | None:
    """Loan AND deposit composition for a company's latest 10-K from ONE filing
    fetch (the ~7 MB fetch+parse runs once, not twice). Returns
    {"meta", "loan": comp|None, "deposit": comp|None} or None when the filing has
    no usable iXBRL. Each `comp` is {period: {"total", "rows": [(label, value)]}}
    over EVERY fiscal period the filing reconciles (newest-first), or None (n/a).
    This is the entry point for UI wiring (the multi-year composition tables)."""
    got = _fetch(cik)
    if not got:
        return None
    meta, facts, labels, children = got
    return {"meta": meta,
            "loan": extract_loan_composition(facts, labels, children),
            "deposit": extract_deposit_composition(facts, labels, children)}


def loan_composition_for(cik) -> dict | None:
    """As-reported loan composition for a company's latest 10-K, keyed by period
    newest-first (the current FY plus any reconciling comparatives). n/a if nothing
    reconciles to the disclosed total."""
    return _composition_for(cik, extract_loan_composition)


def deposit_composition_for(cik) -> dict | None:
    """As-reported deposit composition for a company's latest 10-K, keyed by period
    newest-first (the current FY plus any reconciling comparatives). n/a if nothing
    reconciles to the disclosed total."""
    return _composition_for(cik, extract_deposit_composition)


if __name__ == "__main__":
    import sys
    from data.bank_mapping import get_cik
    args = sys.argv[1:]
    kind = "deposit" if args and args[0] == "--deposit" else "loan"
    tickers = [a for a in args if not a.startswith("--")] or ["AROW", "BCML", "PNFP", "USB", "CFR", "BSRR"]
    fn = deposit_composition_for if kind == "deposit" else loan_composition_for
    for tk in tickers:
        r = fn(get_cik(tk))
        if not r:
            print(f"{tk}: n/a")
            continue
        p, d = next(iter(r["composition"].items()))
        print(f"{tk} [{p}] {kind} total={d['total']/1e9:.2f}B  ({len(d['rows'])} categories)")
        for label, v, member in d["rows"]:
            print(f"      {label[:38]:38} {v/1e6:>9,.1f}M   {member}")
