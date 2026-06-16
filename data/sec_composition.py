"""As-reported composition tables (loan / deposit / …) reconstructed from a
filing's inline-XBRL FACTS + its dimensional STRUCTURE (MetaLinks labels +
definition-linkbase member hierarchy) — NOT by scraping the rendered R-file.

Many banks have no standalone "composition" table: the category breakdown is
embedded in a combined "categories & past due" / "credit quality" disclosure, so
the composition must be reconstructed from the tagged facts — the loan-balance
concept's facts carrying EXACTLY ONE *leaf* member on the portfolio-segment axis,
which reconcile to the undimensioned total. Leaf-vs-parent is resolved from the
definition linkbase so a parent and its children aren't double-counted. Labels
are the filer's own terseLabel from MetaLinks.

See docs/DATA-SOURCING-ARCHITECTURE.md and the company-reported-faithful-
extraction memory. Every value sources to a filing fact; a composition that does
not reconcile to the disclosed total renders n/a, never a guess.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict

from data.sec_filing_scraper import _get, latest_filing, filing_url, parse_inline_xbrl

# The loan axis carrying the by-category breakdown, and concept-name noise to
# avoid (allowance / past-due / charge-off concepts are not the balance).
_LOAN_AXIS = re.compile(r"PortfolioSegment|FinancingReceivableByClass|ReceivableType", re.I)
_LOANISH = re.compile(r"FinancingReceivable|LoansAndLeases|NotesReceivable|LoansReceivable", re.I)
# NB: the loan-BALANCE concept is "…ExcludingAccruedInterestBeforeAllowanceForCreditLoss"
# — it contains "Allowance" but IS what we want, so don't reject on "Allowance".
# Reject the allowance/credit concepts and the vintage (by-origination-year) tables.
_BAD_CONCEPT = re.compile(
    r"ChargeOff|Charged|Recover|PastDue|Nonaccrual|Provision|Impair|Modific|"
    r"Delinquen|InterestIncome|Yield|Vintage|Originated|CurrentFiscalYear", re.I)

_DOMAIN_MEMBER = "http://xbrl.org/int/dim/arcrole/domain-member"


def _filing_dir(cik, accession) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/"


def _metalinks(base: str) -> dict:
    return json.loads(_get(base + "MetaLinks.json"))


def _member_labels(meta: dict) -> dict:
    """{member_localname: filer's terseLabel} from MetaLinks — the bank's own
    wording for each dimensional member ('Commercial', 'Commercial Real Estate')."""
    inst = meta["instance"][next(iter(meta["instance"]))]
    out = {}
    for key, tag in inst.get("tag", {}).items():
        local = tag.get("localname") or key.split("_")[-1]
        roles = (tag.get("lang", {}).get("en-us", {}) or {}).get("role", {})
        label = roles.get("terseLabel") or roles.get("label") or local
        # strip the "[Member]"/"[Domain]" boilerplate the standard label carries
        label = re.sub(r"\s*\[(member|domain)\]\s*$", "", label, flags=re.I).strip()
        out[local] = label
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
    member from a composition when one of its OWN descendants is also tagged (so a
    parent segment isn't summed alongside the children that compose it)."""
    from lxml import etree
    root = etree.fromstring(def_xml)
    XL = "{http://www.w3.org/1999/xlink}"
    children = defaultdict(set)
    for link in root.iter():
        if not link.tag.endswith("}definitionLink"):
            continue
        loc_local = {}
        for loc in link.iter():
            if loc.tag.endswith("}loc"):
                href = loc.get(XL + "href", "")
                loc_local[loc.get(XL + "label")] = href.split("#")[-1].split("_")[-1]
        for arc in link.iter():
            if not arc.tag.endswith("}definitionArc"):
                continue
            if arc.get(XL + "arcrole") != _DOMAIN_MEMBER:
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


def extract_loan_composition(facts, labels: dict, children: dict) -> dict | None:
    """{period_end: {"total": v, "rows": [(label, value), …]}} — the by-category
    loan composition, from the loan-balance concept whose single-member segment
    facts reconcile to the undimensioned total. A tagged member is dropped when a
    finer member it contains is ALSO tagged (so a parent segment isn't summed with
    its children — per the definition-linkbase hierarchy). None when nothing
    reconciles to the disclosed total."""
    best = None  # (total, n_rows, period, concept, rows)
    for concept, fs in _facts_by_concept(facts).items():
        if not _LOANISH.search(concept) or _BAD_CONCEPT.search(concept):
            continue
        per = defaultdict(lambda: {"total": None, "rows": {}})
        for f in fs:
            if not f.members:
                per[f.period_end]["total"] = f.value
            elif len(f.members) == 1:
                axis, mem = next(iter(f.members.items()))
                if _LOAN_AXIS.search(axis.split(":")[-1]):
                    per[f.period_end]["rows"][mem.split(":")[-1]] = f.value
        for period, d in per.items():
            total, allrows = d["total"], d["rows"]
            if not total:
                continue
            present = set(allrows)
            # keep a member only if none of its descendants is also tagged here
            rows = {m: v for m, v in allrows.items()
                    if not _has_present_descendant(m, present, children)}
            if len(rows) < 2 or abs(sum(rows.values()) - total) / abs(total) > 0.01:
                continue                                  # must reconcile
            cand = (total, len(rows), period, concept, rows)
            if best is None or cand[0] > best[0] or (cand[0] == best[0] and cand[1] > best[1]):
                best = cand
    if not best:
        return None
    total, _n, period, _concept, rows = best
    ordered = sorted(rows.items(), key=lambda kv: -kv[1])
    return {period: {"total": total,
                     "rows": [(labels.get(m, m), v) for m, v in ordered]}}


def loan_composition_for(cik) -> dict | None:
    """As-reported loan composition for a company's latest 10-K, reconstructed from
    its inline XBRL. Returns {"meta", "composition"} or None (n/a)."""
    if not cik:
        return None
    meta = latest_filing(cik, ("10-K",))
    if not meta:
        return None
    base = _filing_dir(meta["cik"], meta["accession"])
    facts = parse_inline_xbrl(_get(filing_url(meta["cik"], meta["accession"], meta["doc"])))
    ml = _metalinks(base)
    labels = _member_labels(ml)
    def_url = _def_linkbase_url(ml, base)
    children = _member_children(_get(def_url)) if def_url else {}
    comp = extract_loan_composition(facts, labels, children)
    return {"meta": meta, "composition": comp} if comp else None


if __name__ == "__main__":
    import sys
    from data.bank_mapping import get_cik
    for tk in sys.argv[1:] or ["AROW", "BCML", "PNFP", "USB", "CFR", "BSRR"]:
        r = loan_composition_for(get_cik(tk))
        if not r:
            print(f"{tk}: n/a")
            continue
        p, d = next(iter(r["composition"].items()))
        print(f"{tk} [{p}] total={d['total']/1e9:.2f}B  ({len(d['rows'])} categories)")
        for label, v in d["rows"]:
            print(f"      {label[:36]:36} {v/1e6:>9,.1f}M")
