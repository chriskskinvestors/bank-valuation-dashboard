"""State corporate-law reference for the Corporate Governance sub-tab.

A CURATED, citation-first reference of the classic state anti-takeover
statutes, keyed by state of incorporation (SEC submissions
stateOfIncorporation code). Deterministic display data, not legal advice —
every asserted provision carries its statutory citation so it can be
checked, and states not yet curated render an honest "not curated" line
instead of a guess (the cardinal rule applies to legal facts too).

Provisions tracked (the three classic statute families + one default):
  business_combination  — moratorium/approval regime for combinations with
                          an interested shareholder
  control_share         — voting-rights restrictions on control-share
                          acquisitions
  fair_price            — price-protection statutes for second-step deals
  cumulative_voting_default — whether cumulative voting applies by default

Entries reviewed 2026-07. `has` False = the state affirmatively lacks that
statute family; a missing field/None = not asserted either way.
"""
from __future__ import annotations

REVIEWED = "2026-07"

# Federal overlay — applies to every bank holding company regardless of
# state of incorporation; often more binding than any state statute.
FEDERAL_BANKING_OVERLAY = [
    ("Change in Bank Control Act",
     "Acquiring 10%+ of a class of voting securities of a banking "
     "organization with registered securities raises a rebuttable control "
     "presumption — prior notice to the Federal Reserve required "
     "(12 U.S.C. §1817(j))."),
    ("Bank Holding Company Act",
     "Federal Reserve approval is required to acquire control of a bank "
     "holding company — 25%+ of any class of voting securities, or a "
     "controlling influence; a bank holding company acquiring over 5% "
     "needs approval under §3(a)(3) (12 U.S.C. §1842)."),
    ("Interstate acquisitions",
     "Riegle-Neal caps interstate acquisitions at 10% of nationwide "
     "deposits (12 U.S.C. §1842(d))."),
]

_Y = True
_N = False

STATE_CORP_LAW: dict[str, dict] = {
    "DE": {
        "name": "Delaware",
        "business_combination": {"has": _Y, "cite": "DGCL §203",
                                 "note": "3-year moratorium at 15% unless board pre-approval or 85% tender"},
        "control_share": {"has": _N},
        "fair_price": {"has": _N},
        "cumulative_voting_default": _N,
        "notes": "Poison pills validated by case law (Moran v. Household); "
                 "classified boards permitted (DGCL §141(d)).",
    },
    "MD": {
        "name": "Maryland",
        "business_combination": {"has": _Y, "cite": "MGCL §3-601 et seq.",
                                 "note": "includes fair-price mechanics"},
        "control_share": {"has": _Y, "cite": "MGCL §3-701 et seq."},
        "fair_price": {"has": _Y, "cite": "within MGCL §3-602"},
        "cumulative_voting_default": _N,
        "notes": "MUTA (MGCL §3-801 et seq.) lets the board opt into a "
                 "classified board and other defenses without a "
                 "shareholder vote.",
    },
    "NY": {
        "name": "New York",
        "business_combination": {"has": _Y, "cite": "NYBCL §912",
                                 "note": "5-year moratorium at 20%"},
        "control_share": {"has": _N},
        "fair_price": {"has": _N, "note": "price protections folded into §912"},
        "cumulative_voting_default": _N,
    },
    "PA": {
        "name": "Pennsylvania",
        "business_combination": {"has": _Y, "cite": "15 Pa.C.S. Subch. 25F"},
        "control_share": {"has": _Y, "cite": "15 Pa.C.S. Subch. 25G"},
        "fair_price": {"has": _Y, "cite": "15 Pa.C.S. Subch. 25E "
                                          "(control-transaction fair value)"},
        "cumulative_voting_default": _N,
        "notes": "Also disgorgement of control-seeker profits "
                 "(Subch. 25H) — among the most protective regimes.",
    },
    "VA": {
        "name": "Virginia",
        "business_combination": {"has": _Y, "cite": "Va. Code §13.1-725 et seq. "
                                                    "(affiliated transactions)"},
        "control_share": {"has": _Y, "cite": "Va. Code §13.1-728.1 et seq."},
        "fair_price": {"has": _N},
        "cumulative_voting_default": _N,
    },
    "OH": {
        "name": "Ohio",
        "business_combination": {"has": _Y, "cite": "ORC Ch. 1704 "
                                                    "(merger moratorium)"},
        "control_share": {"has": _Y, "cite": "ORC §1701.831"},
        "fair_price": {"has": _N},
        "cumulative_voting_default": _N,
        "notes": "Control-bid statute (ORC §1707.041) adds tender-offer "
                 "filing requirements.",
    },
    "IN": {
        "name": "Indiana",
        "business_combination": {"has": _Y, "cite": "Ind. Code 23-1-43"},
        "control_share": {"has": _Y, "cite": "Ind. Code 23-1-42",
                          "note": "upheld in CTS v. Dynamics (U.S. 1987)"},
        "fair_price": {"has": _N},
        "cumulative_voting_default": _N,
    },
    "MI": {
        "name": "Michigan",
        "business_combination": {"has": _N},
        "control_share": {"has": _N, "note": "control-share act repealed 2009"},
        "fair_price": {"has": _Y, "cite": "MBCA Ch. 7A"},
        "cumulative_voting_default": _N,
    },
    "WI": {
        "name": "Wisconsin",
        "business_combination": {"has": _Y, "cite": "Wis. Stat. §§180.1140–1144"},
        "control_share": {"has": _Y, "cite": "Wis. Stat. §180.1150",
                          "note": "supervoting restriction above 20%"},
        "fair_price": {"has": _Y, "cite": "Wis. Stat. §§180.1130–1133"},
        "cumulative_voting_default": _N,
    },
    "MN": {
        "name": "Minnesota",
        "business_combination": {"has": _Y, "cite": "Minn. Stat. §302A.673"},
        "control_share": {"has": _Y, "cite": "Minn. Stat. §302A.671"},
        "fair_price": {"has": _N},
        "cumulative_voting_default": _N,
    },
    "MO": {
        "name": "Missouri",
        "business_combination": {"has": _Y, "cite": "Mo. Rev. Stat. §351.459"},
        "control_share": {"has": _Y, "cite": "Mo. Rev. Stat. §351.407"},
        "fair_price": {"has": _N},
        "cumulative_voting_default": _N,
    },
    "NJ": {
        "name": "New Jersey",
        "business_combination": {"has": _Y, "cite": "N.J.S.A. 14A:10A "
                                                    "(Shareholders Protection Act)"},
        "control_share": {"has": _N},
        "fair_price": {"has": _N},
        "cumulative_voting_default": _N,
    },
    "MA": {
        "name": "Massachusetts",
        "business_combination": {"has": _Y, "cite": "Mass. Gen. Laws ch. 110F"},
        "control_share": {"has": _Y, "cite": "Mass. Gen. Laws ch. 110D"},
        "fair_price": {"has": _N},
        "cumulative_voting_default": _N,
        "notes": "Classified board is the statutory DEFAULT for public "
                 "companies (ch. 156D §8.06(b)) — opt-out requires board "
                 "or shareholder action.",
    },
    "FL": {
        "name": "Florida",
        "business_combination": {"has": _Y, "cite": "Fla. Stat. §607.0901 "
                                                    "(affiliated transactions)"},
        "control_share": {"has": _Y, "cite": "Fla. Stat. §607.0902"},
        "fair_price": {"has": _N, "note": "price protections folded into §607.0901"},
        "cumulative_voting_default": _N,
    },
    "GA": {
        "name": "Georgia",
        "business_combination": {"has": _Y, "cite": "Ga. Code §§14-2-1131–1133",
                                 "note": "OPT-IN — applies only if adopted by bylaw"},
        "control_share": {"has": _N},
        "fair_price": {"has": _Y, "cite": "Ga. Code §§14-2-1110–1113",
                       "note": "OPT-IN — applies only if adopted by bylaw"},
        "cumulative_voting_default": _N,
    },
    "TX": {
        "name": "Texas",
        "business_combination": {"has": _Y, "cite": "TBOC §§21.601–21.610"},
        "control_share": {"has": _N},
        "fair_price": {"has": _N},
        "cumulative_voting_default": _N,
    },
    "CA": {
        "name": "California",
        "business_combination": {"has": _N},
        "control_share": {"has": _N},
        "fair_price": {"has": _N},
        "cumulative_voting_default": _Y,
        "notes": "Cumulative voting is the default (Corp. Code §708); "
                 "listed companies may eliminate it (§301.5).",
    },
    "WA": {
        "name": "Washington",
        "business_combination": {"has": _Y, "cite": "RCW 23B.19 "
                                                    "(significant business transactions)"},
        "control_share": {"has": _N},
        "fair_price": {"has": _N},
        "cumulative_voting_default": _N,
    },
    "TN": {
        "name": "Tennessee",
        "business_combination": {"has": _Y, "cite": "Tenn. Code Title 48, "
                                                    "Ch. 103 (Business Combination Act)"},
        "control_share": {"has": _Y, "cite": "Tenn. Code Title 48, Ch. 103 "
                                             "(Control Share Acquisition Act)"},
        "fair_price": {"has": _N},
        "cumulative_voting_default": _N,
        "notes": "Also the Tennessee Greenmail Act.",
    },
    "OK": {
        "name": "Oklahoma",
        "business_combination": {"has": _Y, "cite": "18 Okla. Stat. §1090.3",
                                 "note": "modeled on DGCL §203"},
        "control_share": {"has": _Y, "cite": "18 Okla. Stat. §§1145–1155"},
        "fair_price": {"has": _N},
        "cumulative_voting_default": _N,
    },
    "IA": {
        "name": "Iowa",
        "business_combination": {"has": _Y, "cite": "Iowa Code §490.1110"},
        "fair_price": {"has": _N},
        "cumulative_voting_default": _N,
    },
}


def get_state_reference(state_code: str | None) -> dict | None:
    """Curated reference for a state-of-incorporation code, or None when the
    state isn't curated (callers render an honest 'not curated' line)."""
    if not state_code:
        return None
    return STATE_CORP_LAW.get(str(state_code).strip().upper())
