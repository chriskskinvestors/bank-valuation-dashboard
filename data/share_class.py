"""Share-class classification for the bank universe.

A single SEC registrant (one CIK) can list several securities: its common
stock plus one or more preferred series, baby bonds, and occasionally a
second common class. First Citizens is the worst case — CIK 798941 / FDIC
cert 11063 carries FIVE tickers:

    FCNCA  Class A common   (~$2,069)
    FCNCB  Class B common   (~$1,856, thin OTC)
    FCNCN  depositary pref  (~$25)
    FCNCO  depositary pref  (~$21)
    FCNCP  depositary pref  (~$20)

Our fundamentals pipeline keys off CIK/cert, so EVERY ticker in such a
cluster receives the SAME common-share TBVPS and fair value, joined to its
OWN market price. For a preferred ticker (~$25 par) against the common
TBVPS (~$1,600 for FCNCA) that produces a ~0.01x P/TBV and a ~+99%
"discount to fair value" — a plausible-wrong number the screens must never
show (CLAUDE.md cardinal rule).

So at most ONE ticker per registrant — the common stock — may carry
per-common valuation metrics; the rest are non-common classes and are
dropped from the valuation universe (data.bank_universe.get_universe_tickers).

Signal — why shared-CIK structure, not a ticker blocklist or a single API
field:
  * A hardcoded {FCNCP, FCNCO, FCNCN} blocklist does not generalize — 26
    registrants in the universe have multi-ticker clusters (FITB, HBAN,
    VLY, ONB, …) and new ones appear as banks issue preferreds.
  * FMP `companyName` flags only ~6 of those clusters' preferreds; most
    (FITBP, HBANP, VLYPP, FCNCO, FCNCP, …) carry the plain parent name.
    Price-near-$25-par is also unreliable (MNSB common ~$24 vs MNSBP ~$25).
  * The one signal that holds across all 26 clusters (verified live against
    FMP) is structural: same CIK ⇒ same registrant ⇒ identical common-share
    fundamentals, so only the registrant's primary common listing may keep
    them. The primary common is the curated/base ticker; every other ticker
    in the cluster is non-common.

Resolution order for "which ticker is the primary common":
  1. A persisted `share_class` field on the universe entry — set nightly by
     annotate_share_classes() from the structural pick, FMP-verified. The
     interactive path just reads it.
  2. Structural fallback (offline, no network) when the field is absent:
     _pick_primary() — curated tie-breaker, then BANK_MAP membership, then
     the unique strictly-shortest ticker. Fails SAFE: if the common cannot
     be identified, the WHOLE cluster is treated as non-common (dropped →
     n/a) rather than risk showing a preferred as if it were common.
"""

from collections import defaultdict


# Curated primary-common ticker for clusters that are ambiguous by morphology
# (no unique shortest ticker, not in BANK_MAP). Keyed by CIK. This is an
# allowlist of the VERIFIED common stock — NOT a preferred blocklist — and is
# the same curated-override pattern used by data.bank_mapping.BANK_MAP.
# Each entry verified live against FMP profile name + price (2026-06-15):
_AMBIGUOUS_PRIMARY_COMMON: dict[int, str] = {
    # First Citizens: FCNCA Class A common ($2,069, plain name). FCNCB is a
    # thin OTC Class B common — demoted as a redundant second row for the same
    # registrant. FCNCN/O/P are depositary preferred (~$20–25).
    798941: "FCNCA",
    # Dime Community: DCOM common ($39, plain). DCBG = "9% Notes 2034" baby bond.
    846617: "DCOM",
    # Customers Bancorp: CUBI common ($76, plain). CUBB = "5.375%" preferred.
    1488813: "CUBI",
}


def _clusters(universe: dict[str, dict]) -> dict[int, list[str]]:
    """CIK -> sorted tickers, for CIKs mapped by more than one universe
    ticker. CIK is the SEC registrant key; a single-ticker CIK is an ordinary
    bank and is never touched here."""
    by_cik: dict[int, list[str]] = defaultdict(list)
    for ticker, info in universe.items():
        cik = info.get("cik") if isinstance(info, dict) else None
        if cik:
            by_cik[int(cik)].append(ticker)
    return {c: sorted(ts) for c, ts in by_cik.items() if len(ts) > 1}


def _is_major_exchange(exchange: str | None) -> bool:
    """True for a primary-listing exchange (NYSE, NASDAQ, AMEX…), False for
    OTC/pink/blank. A registrant's live common trades on a major exchange; a
    rebranded or delisted symbol drops to OTC."""
    e = (exchange or "").upper()
    return bool(e) and not ("OTC" in e or "PINK" in e or "GREY" in e)


def _pick_primary(cluster: list[str], cik: int,
                  universe: dict[str, dict] | None = None) -> str | None:
    """The registrant's primary common ticker within a CIK cluster, or None
    when it cannot be identified (caller then drops the whole cluster — fail
    safe). Order: curated tie-breaker, prefer a major-exchange listing, then
    BANK_MAP membership, then the unique strictly-shortest ticker (the base
    listing, e.g. FITB over FITBP)."""
    members = set(cluster)

    curated = _AMBIGUOUS_PRIMARY_COMMON.get(int(cik))
    if curated and curated in members:
        return curated

    candidates = list(cluster)
    # Prefer a major-exchange listing when it narrows the field — a stale or
    # rebranded OTC symbol (e.g. BK after BNY Mellon's ticker change to BNY) or
    # a thin OTC second common class (FCNCB) must not outrank the live common.
    if universe:
        major = [t for t in candidates
                 if _is_major_exchange(universe.get(t, {}).get("exchange"))]
        if major and len(major) < len(candidates):
            candidates = major

    # BANK_MAP only ever lists a registrant's common stock (hand-verified).
    from data.bank_mapping import BANK_MAP
    bank_map_members = [t for t in candidates if t in BANK_MAP]
    if len(bank_map_members) == 1:
        return bank_map_members[0]

    # Base listing = unique strictly-shortest ticker (FITB, VLY, ONB, ASB…).
    shortest_len = min(len(t) for t in candidates)
    shortest = [t for t in candidates if len(t) == shortest_len]
    if len(shortest) == 1:
        return shortest[0]

    return None  # ambiguous (e.g. two equal-length commons, none curated)


# Markers that, in an FMP `companyName`, positively identify a non-common
# security (preferred series, depositary shares, baby bonds). Used only to
# VERIFY the structural pick at build time — never as the sole classifier,
# because FMP omits the marker for most preferreds.
_NONCOMMON_NAME_MARKERS = (
    "preferred", "depositary", "depository", " pfd", "pfd ",
    " notes", "fixed-", "fixed to", "fixed rate", "% s", "capital trust",
)


def _name_flags_noncommon(name: str) -> bool:
    """True when an FMP security name positively looks non-common."""
    import re
    n = (name or "").lower()
    if any(mk in n for mk in _NONCOMMON_NAME_MARKERS):
        return True
    # A coupon like "5.375%" / "9 %" only appears on preferreds / notes.
    return bool(re.search(r"\d\s*%", n))


def noncommon_tickers(universe: dict[str, dict]) -> set[str]:
    """Universe tickers that must NOT carry per-common valuation metrics.

    Prefers a persisted `share_class` field (set nightly, FMP-backed); falls
    back to the structural rule so the exclusion works offline and on the
    first deploy before any snapshot rebuild.
    """
    noncommon: set[str] = set()
    for cik, cluster in _clusters(universe).items():
        labels = {t: str((universe[t].get("share_class") or "")).lower()
                  for t in cluster}
        if any(labels.values()):
            # Nightly FMP-backed classification present — trust it. Keep only
            # the single primary common; demote any extra labelled-common
            # siblings (e.g. FCNCB) so a registrant never double-lists.
            commons = [t for t in cluster if labels[t] == "common"]
            primary = _pick_primary(commons, cik, universe) if commons else None
            noncommon |= members_except(cluster, primary)
        else:
            primary = _pick_primary(cluster, cik, universe)
            noncommon |= members_except(cluster, primary)
    return noncommon


def members_except(cluster: list[str], keep: str | None) -> set[str]:
    """All cluster members except `keep` (everything when keep is None)."""
    return {t for t in cluster if t != keep}


def annotate_share_classes(universe: dict[str, dict],
                           name_lookup=None) -> dict[str, dict]:
    """Set `share_class` ('common' | 'preferred') on every universe entry,
    in place. Single-ticker registrants are 'common'. Within a multi-ticker
    cluster the structural primary is 'common' and the rest 'preferred'.

    `name_lookup(ticker) -> str | None` (FMP company name) is optional; when
    provided it VERIFIES the structural pick and logs a warning on any
    contradiction (the primary looking non-common, or no sibling looking
    non-common) so a future mis-pick is caught rather than silently shipped.
    Verification never overrides the structural decision.
    """
    clustered = _clusters(universe)
    clustered_tickers = {t for ts in clustered.values() for t in ts}

    for ticker, info in universe.items():
        if isinstance(info, dict) and ticker not in clustered_tickers:
            info["share_class"] = "common"

    for cik, cluster in clustered.items():
        primary = _pick_primary(cluster, cik, universe)
        for t in cluster:
            universe[t]["share_class"] = "common" if t == primary else "preferred"

        if name_lookup is None:
            continue
        # FMP cross-check (logs only).
        if primary is None:
            print(f"[share_class] WARN cik {cik}: cluster {cluster} has no "
                  f"identifiable common — entire cluster dropped from screens")
            continue
        primary_name = name_lookup(primary)
        if primary_name and _name_flags_noncommon(primary_name):
            print(f"[share_class] WARN cik {cik}: structural common {primary} "
                  f"has a non-common FMP name {primary_name!r} — verify pick")
        siblings = [t for t in cluster if t != primary]
        flagged = [t for t in siblings
                   if (lambda nm: nm and _name_flags_noncommon(nm))(name_lookup(t))]
        if siblings and not flagged:
            print(f"[share_class] note cik {cik}: none of {siblings} carry an "
                  f"FMP non-common name marker (classified by CIK structure)")

    return universe
