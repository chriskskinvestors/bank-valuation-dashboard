"""
PENDING (announced, not yet completed) M&A deals for one holdco —
docs/SNL-BUILD-PLAN.md §14. Closes the announced-deal gap: a deal signed
yesterday has no FDIC completion event and no termination 8-K, so
ma_history alone cannot see it (live case: First Hawaiian / TriCo
Bancshares, agreement 2026-07-12, announced 2026-07-13).

DETECTION is form-driven, not text-driven: Rule 425 filings (prospectus
communications for a live stock business combination) appear in the
filer's own submissions history the day a stock deal is announced and
keep coming while it is pending. The latest 425 "episode" (trailing
cluster, gaps ≤ 180 days) younger than 540 days = a live deal.

DETAILS come from the episode's first 425s plus same-window announcement
8-Ks: the 425 legend's "Subject Company:" line names the deal's TARGET
authoritatively (subject == self -> this bank is being acquired), the
press release supplies the stated value or the exchange ratio (shared
extractor in data/ma_announcements — comma-tolerant, FHB-form aware).

CASH deals file no 425 — those come from ma_announcements.
find_open_announcements (recent announcement-classified 8-Ks with no
completion/termination anchor; live case: Catalyst/Lakeside all-cash,
announced 2026-04-08, $41.1M stated). When both legs surface the same
deal (mixed stock-and-cash), the 425 row wins (richer party data).

OPEN-STATUS VERIFICATION (mandatory — the reason the 2026-07-15 revert
happened): neither leg's detection signal proves a deal is STILL open —
425 episodes and announcement 8-Ks both persist after the deal closes, so
without a positive close check a completed deal shows as pending forever
(CLST/Lakeside closed 2026-07-14, PB/Stellar 2026-07-01, FULT/Blue Foundry
2026-04-01 all leaked through the first cut). Every merged candidate is
therefore confirmed against the filer's LATER 8-K Item 2.01 (Completion)
or 1.02 (Termination) naming the counterparty; anything resolved — or
unverifiable because EDGAR failed — is dropped (and the result made
uncacheable) rather than shown.

Counterparties are matched to the live universe by brand token (a pending
deal's counterparty is by definition still alive), giving cert + CIK —
which is what makes pending rows the RICHEST comps rows (holdco TBV and
FDIC financials both available).

Emitted rows carry ok=False on any fetch failure (caller must not
cache). Dedup against completed/terminated rows happens in ma_history.
"""

from __future__ import annotations

import re
import time
from datetime import date, timedelta

from data.ma_announcements import (
    _ACQUIRE_OBJ_RE,
    _ANNOUNCE_RE,
    _COMPLETED_RE,
    _accession_text,
    _clean_company_name,
    _close_before,
    _digits_in_name,
    _shares_outstanding_asof,
    _wire_releases,
    _wire_releases_since,
    _wire_story_text,
    brand_token,
    build_terms,
    row_token,
    _GENERIC as _GENERIC_WORDS,
    extract_exchange_ratio,
    extract_stated_value,
    fill_implied_price,
    find_open_announcements,
    token_in,
)
from data.ma_summary import iter_submission_filings

_PAUSE_S = 0.15
_EPISODE_GAP_DAYS = 180         # 425s further apart than this = older deal
_PENDING_MAX_AGE_DAYS = 540     # older unresolved episodes are stale, not
                                # "pending" — the completed/terminated legs
                                # own whatever became of them
_ANNOUNCE_8K_ITEMS = {"1.01", "7.01", "8.01"}

_SUBJECT_RE = re.compile(
    r"Subject\s+Compan(?:y|ies)\s*:?\s*(.{3,80}?)\s*(?:Commission\s+File|"
    r"\(Commission|Registration\s+No)", re.IGNORECASE)
# "Filed by: Tri-County Financial Group Pursuant to Rule 425" — the filer's
# OWN name in its own words. The FDIC charter name ("First State Bank") can
# be all-generic and the universe match by brand token ambiguous ("Tri"
# hits two names), so neither identified self for Tri-County and its 425s
# rendered as TYFG acquiring HBT.
_FILED_BY_RE = re.compile(
    r"Filed\s+by\s*:?\s*(.{3,80}?)\s*(?:Pursuant\s+to|Subject\s+Compan|"
    r"Commission\s+File)", re.IGNORECASE)
# Ratio extraction lives upstream in ma_announcements.extract_exchange_ratio
# (comma-tolerant + the bare "2.095 First Hawaiian shares for each TriCo
# share" form — upstreamed 2026-07-14, closing the merge-later note).


_FDIC_NAME_CACHE: dict[str, tuple] = {}
_CORP_SUFFIX_RE = re.compile(
    r",?\s+(?:inc\.?|incorporated|corp\.?|corporation|co\.?|company|ltd\.?|llc)\s*$",
    re.IGNORECASE)
# FDIC abbreviates holding-company names ("CENTURY FINL SERVICES CORP" for
# Century Financial Services Corporation, live 2026-10-06; BCORP per the
# OTC-discovery notes). The NAMEHCR retry uses these forms.
_FDIC_ABBREV = {"financial": "finl", "bancorp": "bcorp", "bancorporation": "bcorp",
                "national": "natl", "savings": "svgs", "trust": "tr",
                "bankshares": "bkshs", "holdings": "hldgs"}


def _fdic_abbreviated(phrase: str) -> str | None:
    words = phrase.split()
    out = [_FDIC_ABBREV.get(w.lower(), w) for w in words]
    return " ".join(out) if out != words else None


_FDIC_EXPAND = {v: k for k, v in _FDIC_ABBREV.items()}
_FDIC_EXPAND.update({"corp": "corporation", "co": "company", "assn": "association",
                     "natl": "national", "bk": "bank", "svgs": "savings"})
_NAME_SUFFIX_WORDS = {"inc", "incorporated", "corp", "corporation", "co", "company",
                      "ltd", "llc", "the"}


def _fdic_norm(name: str) -> tuple[str, ...]:
    """Comparable word tuple: lowered, FDIC abbreviations expanded, corporate
    suffix words dropped."""
    words = re.findall(r"[a-z0-9&]+", (name or "").lower())
    words = [_FDIC_EXPAND.get(w, w) for w in words]
    return tuple(w for w in words if w not in _NAME_SUFFIX_WORDS)


def fdic_cert_for_name(name: str) -> tuple[int | None, str | None, bool]:
    """(cert, FDIC name, ok) for the ONE active FDIC-insured institution —
    or the one charter under a holding company — carrying this name
    (corporate suffix stripped, phrase match on NAME then NAMEHCR, brand
    token must appear). A target outside the universe (private or OTC:
    Century Financial Services, First Illinois, First Carolina Bank on the
    2026-10-06 board) otherwise has no cert, and every valuation cell —
    target assets, P/TBV, P/Assets, core-deposit premium — is n/a. Several
    hits (a multi-charter holdco, a generic name) -> None: one charter's
    TBV would be a plausible-wrong denominator. ok=False = FDIC unreachable
    (never cache that)."""
    phrase = _CORP_SUFFIX_RE.sub("", (name or "").strip()).strip(" ,.")
    if len(phrase) < 4 or len(phrase.split()) < 2 and not brand_token(phrase):
        return None, None, True
    key = phrase.lower()
    if key in _FDIC_NAME_CACHE:
        return _FDIC_NAME_CACHE[key]
    from data.fdic_client import FDIC_INSTITUTIONS_URL
    from data.http import get_with_retry
    hits: list[dict] = []
    tries = [("NAME", phrase), ("NAMEHCR", phrase)]
    abbr = _fdic_abbreviated(phrase)
    if abbr:
        tries.append(("NAMEHCR", abbr))
    for field, needle in tries:
        resp = get_with_retry(FDIC_INSTITUTIONS_URL, params={
            "filters": f'{field}:"{needle}" AND ACTIVE:1',
            "fields": "CERT,NAME,NAMEHCR,ASSET", "limit": 5}, timeout=30)
        if resp is None:
            return None, None, False
        try:
            rows = [d["data"] for d in resp.json().get("data", [])]
        except Exception:
            return None, None, False
        # EXACT normalized equality on the searched field: the phrase
        # search is a prefix match, and "First Carolina" (the captured short
        # name of First Carolina Bancshares, First Bancorp 2026-07-14) hit
        # the unrelated $3.4B First Carolina Bank — a 0.43x P/TBV on the
        # board. A short name never links; the full name does.
        want = _fdic_norm(phrase)
        hits = [r for r in rows if _fdic_norm(r.get(field) or "") == want]
        if hits:
            break
    out = ((int(hits[0]["CERT"]), hits[0].get("NAME"), True) if len(hits) == 1
           else (None, None, True))
    _FDIC_NAME_CACHE[key] = out
    return out


def _universe_match(name: str):
    """(ticker, cert, cik) for a live universe bank whose NAME shares the
    brand token — unique hit only, else Nones (n/a over a wrong link).
    Never by ticker: a filing's defined term is not a ticker ("BOH" in
    South Plains' 2025-12-01 8-K is BOH Holdings, parent of Bank of Houston;
    the ticker-equality match linked it to Bank of Hawaii, and the
    open-status needle then missed the 2026-04-01 2.01, so a closed deal
    sat on the board as pending)."""
    tok = brand_token(name or "")
    if not tok:
        return None, None, None
    try:
        from data.bank_universe import get_universe
        hits = [(t, info) for t, info in get_universe().items()
                if token_in(tok, (info.get("name") or "").lower())
                and brand_token(info.get("name") or "") == tok]
    except Exception:
        return None, None, None
    if len(hits) != 1:
        return None, None, None
    t, info = hits[0]
    try:
        cert = int(info.get("fdic_cert") or 0) or None
    except (TypeError, ValueError):
        cert = None
    try:
        cik = int(info.get("cik") or 0) or None
    except (TypeError, ValueError):
        cik = None
    return t, cert, cik


def _find_pending_425(cik, subject_name: str) -> tuple[list[dict], bool]:
    """
    Live 425-episode (stock) deals for a holdco CIK (usually 0 or 1).

    Rows: {announce_date, direction 'acquisition' | 'sale',
           counterparty_name, counterparty_ticker | None,
           counterparty_cert | None, value_usd | None,
           value_basis 'stated' | 'computed' | None, value_note | None,
           target_cik | None, announce_url}
    ok=False on any fetch failure — the caller must not cache.
    """
    if not cik:
        return [], True
    filings, ok = iter_submission_filings(int(cik))
    if not ok:
        return [], False

    f425 = sorted((f for f in filings if f["form"] == "425" and f["date"]),
                  key=lambda f: f["date"])
    if not f425:
        return [], True

    # Trailing episode of 425s = the latest (possibly live) deal.
    episode = [f425[-1]]
    for f in reversed(f425[:-1]):
        gap = (date.fromisoformat(episode[0]["date"])
               - date.fromisoformat(f["date"])).days
        if gap > _EPISODE_GAP_DAYS:
            break
        episode.insert(0, f)
    announce = episode[0]["date"]
    if (date.today() - date.fromisoformat(announce)).days > _PENDING_MAX_AGE_DAYS:
        return [], True    # stale — completed/terminated legs own the outcome

    # Detail corpus: the episode's first 425s + same-window announcement 8-Ks
    # (their EX-99 press releases carry the value/ratio).
    docs = episode[:2]
    lo = (date.fromisoformat(announce) - timedelta(days=2)).isoformat()
    hi = (date.fromisoformat(announce) + timedelta(days=2)).isoformat()
    docs += [f for f in filings
             if f["form"] == "8-K" and lo <= f["date"] <= hi
             and ({i.strip() for i in (f["items"] or "").split(",")}
                  & _ANNOUNCE_8K_ITEMS)][:2]
    texts = []
    fetch_failed = False
    for f in docs:
        time.sleep(_PAUSE_S)
        text, t_ok = _accession_text(int(cik), f["accession"], f["doc"])
        fetch_failed = fetch_failed or not t_ok
        if text:
            texts.append(text)
    if not texts:
        return [], not fetch_failed
    corpus = " ".join(texts)

    # Counterparty via the 425 legend's Subject Company line.
    sm = _SUBJECT_RE.search(corpus)
    if not sm:
        return [], not fetch_failed    # no legend — never guess the parties
    subject_co = " ".join(sm.group(1).split()).strip(" .,:;")
    # A legend whose capture is not a company name ("Merger Investor
    # Presentation 251 John Marshall Bancorp, Inc", "Cincinnati, Ohio - July
    # 21, 2026. First Financial Bancorp" — both live on the first universe
    # board, the latter a self-deal) is unreadable: no row, never a guess.
    if _digits_in_name(subject_co) or len(subject_co.split()) > 8:
        return [], not fetch_failed
    # Self-detection by IDENTITY, not name tokens: the FDIC bank name and
    # the holdco name can differ in brand token ("Tri Counties Bank" vs
    # "TriCo Bancshares") — resolve the subject through the universe and
    # compare CIKs; token equality is only the fallback for unmatched names.
    self_tok = brand_token(subject_name or "")
    fb = _FILED_BY_RE.search(corpus)
    filed_by_tok = brand_token(" ".join(fb.group(1).split())) if fb else None
    self_toks = {t for t in (self_tok, filed_by_tok) if t}
    subj_tok = brand_token(subject_co)
    _st, _sc, subj_cik = _universe_match(subject_co)
    subject_is_self = ((subj_cik == int(cik)) if subj_cik
                       else bool(subj_tok and subj_tok in self_toks))
    if subject_is_self:
        # We are the 425 subject — this bank is the one being ACQUIRED. The
        # counterparty is the other party of the legend's "transaction
        # between A and B" sentence; without a clean parse, leave the row
        # out rather than guess (the acquirer's own filings carry the deal).
        bm = re.search(r"transaction\s+between\s+(.{3,60}?)\s+"
                       r"(?:\([^)]{1,30}\)\s*)?and\s+(.{3,60}?)[,.]", corpus,
                       re.IGNORECASE)
        other = None
        if bm:
            for cand in (bm.group(1), bm.group(2)):
                ct = brand_token(cand)
                if ct and ct not in self_toks:
                    other = " ".join(cand.split())
                    break
        if not other:
            return [], not fetch_failed
        direction, counterparty = "sale", other
    else:
        direction, counterparty = "acquisition", subject_co
        # A target filing its own 425s may legend the REGISTRANT (acquirer)
        # as the Subject Company. The PR's acquire-verb object settles it:
        # when that object resolves to self, we are the one being acquired
        # (live: Tri-County's 425s legended HBT, so the board showed TYFG
        # "acquiring" HBT at 0.28x — a cash deal, so the ratio-side check
        # below had nothing to arbitrate).
        from data.ma_announcements import _ACQUIRE_OBJ_RE, _clean_company_name
        # Headlines are title-cased ("Agreement to Acquire ..."): match
        # case-insensitively (the cash leg's regex is case-sensitive by design).
        for m in re.compile(_ACQUIRE_OBJ_RE.pattern, re.IGNORECASE).finditer(corpus):
            obj = _clean_company_name(m.group(1))
            o_cik = _universe_match(obj)[2]
            o_tok = brand_token(obj)
            if (o_cik and int(o_cik) == int(cik)) or (o_tok and o_tok in self_toks):
                direction = "sale"
                break

    cp_tick, cp_cert, cp_cik = _universe_match(counterparty)

    # Value: stated first, else computed from the ratio (both parties alive,
    # so shares and price resolve from the live universe mapping).
    value = extract_stated_value(corpus)
    basis = "stated" if value else None
    note = None
    tgt_cik = None
    a_tick = t_tick = None
    ratio_hit = extract_exchange_ratio(corpus)
    if ratio_hit:
        ratio, acq_side, tgt_side = ratio_hit
        # The ratio's per-share (target) side owns the deal value.
        t_tick, _t_cert, t_cik = _universe_match(tgt_side)
        a_tick, _a_cert, _a_cik = _universe_match(acq_side)
        tgt_cik = t_cik
        # The ratio sentence arbitrates DIRECTION over the legend: a target
        # filing its own 425s may legend the registrant (acquirer) as the
        # "Subject Company" (live: Tri-County's 425s named HBT, so the board
        # showed TYFG "acquiring" HBT at 0.28x). If the per-share side is
        # self, we are being acquired.
        # Universe-ambiguous per-share side ("Tri-County" vs other Tri-
        # banks) still arbitrates by our own brand tokens.
        tgt_is_self = ((t_cik and int(t_cik) == int(cik))
                       or (not t_cik and brand_token(tgt_side) in self_toks))
        if tgt_is_self and direction == "acquisition":
            direction = "sale"
            counterparty = " ".join(acq_side.split())
            cp_tick, cp_cert, cp_cik = _universe_match(counterparty)
        elif _a_cik and int(_a_cik) == int(cik) and direction == "sale":
            direction = "acquisition"
            counterparty = " ".join(tgt_side.split())
            cp_tick, cp_cert, cp_cik = _universe_match(counterparty)
        if value is None and t_cik and a_tick:
            shares, sh_end, s_ok = _shares_outstanding_asof(t_cik, announce)
            price, p_date, p_ok = _close_before(a_tick, announce)
            fetch_failed = fetch_failed or not (s_ok and p_ok)
            if shares and price:
                value = int(round(shares * ratio * price))
                basis = "computed"
                note = (f"computed: {ratio} × {a_tick} ${price:.2f} "
                        f"({p_date}) × {shares:,} {t_tick} shares ({sh_end})")

    # Structured terms (ratio / cash / mix / implied price at announce /
    # premium / expected close / termination fee) off the same corpus — the
    # ratio sides resolved through the live universe above, the
    # counterparty ticker as the per-share side's fallback.
    terms, t_ok = build_terms(
        corpus, announce, acq_tick=a_tick,
        tgt_tick=t_tick or (cp_tick if direction == "acquisition" else None),
        close_lookup=_close_before)
    fetch_failed = fetch_failed or not t_ok

    url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
           f"{episode[0]['accession'].replace('-', '')}/{episode[0]['doc']}"
           if episode[0]["doc"] else None)
    row = {"announce_date": announce, "direction": direction,
           "counterparty_name": counterparty,
           "counterparty_ticker": cp_tick, "counterparty_cert": cp_cert,
           "counterparty_cik": cp_cik,
           "value_usd": value, "value_basis": basis, "value_note": note,
           "target_cik": tgt_cik, "announce_url": url, "terms": terms}
    return [row], not fetch_failed


_RESOLVING_ITEMS = ("2.01", "1.02")   # Completion / Termination of a deal
_ANCHOR_SLACK_DAYS = 45               # our announce anchor can be a LATER
                                      # deal-related 8-K (mis-anchor); a
                                      # resolving filing shortly BEFORE it
                                      # still proves the deal is done


def _resolving_needle(other_name: str) -> str | None:
    """What to look for in a resolving 8-K's text: the counterparty's brand
    token, else (all-generic names like 'American Bank Holding Company') the
    full lowered multi-word phrase. None = no usable needle — the caller
    must treat the deal as UNVERIFIABLE and drop it."""
    tok = brand_token(other_name or "")
    if tok:
        return tok
    phrase = " ".join((other_name or "").lower().replace(",", " ").split())
    phrase = re.sub(r"[.]", "", phrase)
    return phrase if len(phrase.split()) >= 2 else None


def _resolving_needles(other_name: str, extra_names=()) -> list[str]:
    """Every needle that may name the counterparty in a resolving filing:
    the brand token (or generic-name phrase), the collapsed two-word form
    (an 8-K wrote "MidWest One"; Nicolet's 2026-02-20 completion says
    "MidWestOne", which the word-boundary token never matched — live, a
    closed deal sat as pending), and the extra names' tokens (the universe
    name behind a known ticker)."""
    out = []
    n = _resolving_needle(other_name)
    if n:
        out.append(n)
    words = re.findall(r"[a-z0-9]+", (other_name or "").lower())
    if len(words) >= 2 and words[0] not in _GENERIC_WORDS:
        joined = words[0] + words[1]
        if joined not in out and len(joined) >= 6:
            out.append(joined)
    for name in extra_names or ():
        t = brand_token(name or "")
        if t and t not in out:
            out.append(t)
    return out


def _needle_in(needle: str, low: str) -> bool:
    if " " in needle:          # full-phrase needle (all-generic name)
        return needle in " ".join(low.replace(",", " ").split())
    return token_in(needle, low)


_RESOLVE_SCAN_CAP = 12          # resolving-candidate documents fetched per
                                # deal — plenty (a filer rarely has more than
                                # a couple of 2.01/1.02/8.01s in the window)


def _resolved_after(filer_cik, other_name: str, announce_date: str,
                    filings=None, extra_names=()) -> tuple[bool | None, bool]:
    """(resolved, ok): did ``filer_cik`` file an 8-K on/after
    ``announce_date`` − slack that RESOLVES the deal with ``other_name``?
    Resolution = an Item 2.01 (Completion) or 1.02 (Termination) naming the
    counterparty, OR an Item 8.01 naming it in completed tense — item
    discipline varies by filer (live: Hope filed the Territorial completion
    under 8.01 only, no 2.01 anywhere). This is the positive
    close/terminate check that keeps a completed deal off the pending list —
    the announcement / Rule-425 filings that FOUND the deal persist after it
    closes, so their presence proves nothing about open status. The slack
    window covers mis-anchored candidates (a post-close 8-K latched as the
    announcement).

    resolved=None: no usable needle (caller must DROP the row — unprovable
    open status is not shown). ok=False on a fetch failure (caller must
    neither emit the row nor cache)."""
    if not filer_cik:
        return None, True
    needles = _resolving_needles(other_name, extra_names)
    if not needles:
        return None, True
    if filings is None:
        filings, ok = iter_submission_filings(int(filer_cik))
        if not ok:
            return False, False
    floor = (date.fromisoformat(announce_date)
             - timedelta(days=_ANCHOR_SLACK_DAYS)).isoformat()
    cands = [f for f in filings
             if f.get("form") == "8-K" and f.get("date", "") >= floor
             and (any(i in f.get("items", "") for i in _RESOLVING_ITEMS)
                  or "8.01" in f.get("items", ""))]
    fetch_failed = False
    for f in sorted(cands, key=lambda x: x["date"],
                    reverse=True)[:_RESOLVE_SCAN_CAP]:
        time.sleep(_PAUSE_S)
        text, t_ok = _accession_text(int(filer_cik), f["accession"], f["doc"])
        fetch_failed = fetch_failed or not t_ok
        if not text:
            continue
        low = text.lower()
        if not any(_needle_in(n, low) for n in needles):
            continue
        if any(i in f.get("items", "") for i in _RESOLVING_ITEMS):
            return True, True          # 2.01/1.02 naming it = resolved
        if _COMPLETED_RE.search(text):
            return True, True          # 8.01 in completed tense = resolved
    return False, not fetch_failed


def _wire_resolved(ticker: str | None, needles: list[str],
                   announce_date: str) -> bool:
    """Second net for an EDGAR-sourced row: a LATER release by the filer
    on the wire whose title carries completion/termination wording and
    names the counterparty. U.S. Bancorp completed BTIG on 2026-06-01 with
    a press release and no 2.01 (immaterial), so the EDGAR gate alone kept
    it pending for four months. Unavailable feed = no signal (False)."""
    if not ticker or not needles:
        return False
    prs = _wire_releases_since(ticker, announce_date)
    for q in prs or []:
        if (q.get("published_at") or "")[:10] <= announce_date:
            continue
        title = q.get("title") or ""
        if not _WIRE_RESOLVED_TITLE_RE.search(title):
            continue
        if _release_resolves(title, q.get("text") or "", needles):
            return True
    return False


def _release_resolves(title: str, text: str, needles: list[str]) -> bool:
    """A completion/termination-titled release is ABOUT the counterparty
    when its title names it, or the completion wording sits within 150
    chars of the name in the body. "Announces Closing of Subordinated Notes
    Offering" with the pending deal in its boilerplate is not a close."""
    low_t = title.lower()
    if any(_needle_in(n, low_t) for n in needles):
        return True
    low = (text or "").lower()
    for n in needles:
        for m in re.finditer(re.escape(n), low):
            around = low[max(0, m.start() - 150):m.end() + 150]
            if _WIRE_RESOLVED_TITLE_RE.search(around):
                return True
    return False


_VOTE_RE = re.compile(r"\bapprov", re.IGNORECASE)
_REG_APPROVAL_RE = re.compile(
    r"(?:received|obtained|receipt\s+of|granted|approved\s+by)\s+(?:all\s+)?"
    r"(?:(?:of\s+)?the\s+)?(?:required|necessary|requisite|remaining|final|"
    r"regulatory)?\s*(?:bank\s+)?regulatory\s+approvals?|regulatory\s+"
    r"approvals?\s+(?:have|has)\s+been\s+(?:received|obtained|granted)",
    re.IGNORECASE)
_MILESTONE_SCAN_CAP = 8


def _milestones(filer_cik, other_name: str, announce_date: str,
                filings, side: str) -> tuple[dict, bool]:
    """Disclosed deal milestones from ``filer_cik``'s 8-Ks AFTER the
    announcement: an Item 5.07 naming the counterparty (brand token) with
    approval language = that side's shareholder vote (filing date); an Item
    8.01/7.01 naming it with "received ... regulatory approvals" = the
    regulatory-approval date. Strict — nothing inferred from silence; a
    filing that doesn't name the counterparty proves nothing. Returns
    ({"votes": [{side, date, url}], "regulatory_approval": {date, url} |
    None}, ok); ok=False on a fetch failure (caller must not cache)."""
    out = {"votes": [], "regulatory_approval": None}
    if not filer_cik:
        return out, True
    needle = _resolving_needle(other_name)
    if not needle:
        return out, True
    if filings is None:
        filings, f_ok = iter_submission_filings(int(filer_cik))
        if not f_ok:
            return out, False
    cands = [f for f in filings
             if f.get("form") == "8-K" and f.get("date", "") > announce_date
             and any(i in f.get("items", "") for i in ("5.07", "8.01", "7.01"))]
    fetch_failed = False
    for f in sorted(cands, key=lambda x: x["date"])[:_MILESTONE_SCAN_CAP]:
        time.sleep(_PAUSE_S)
        text, t_ok = _accession_text(int(filer_cik), f["accession"], f["doc"])
        fetch_failed = fetch_failed or not t_ok
        if not text:
            continue
        low = text.lower()
        named = (needle in " ".join(low.replace(",", " ").split())
                 if " " in needle else token_in(needle, low))
        if not named:
            continue
        if _TRANSCRIPT_RE.search(low[:2000]):
            continue              # a call transcript is not a milestone source
        url = (f"https://www.sec.gov/Archives/edgar/data/{int(filer_cik)}/"
               f"{f['accession'].replace('-', '')}/{f['doc']}")
        if "5.07" in f.get("items", "") and _VOTE_RE.search(text) \
                and not out["votes"]:
            out["votes"].append({"side": side, "date": f["date"], "url": url})
        if not out["regulatory_approval"] and _approval_about(low, needle):
            out["regulatory_approval"] = {"date": f["date"], "url": url}
    return out, not fetch_failed


_TRANSCRIPT_RE = re.compile(r"transcript\s+of\s+(?:the\s+)?(?:conference\s+)?call|conference\s+call\s+transcript")
_FORWARD_RE = re.compile(
    r"(?:expect\w*|anticipat\w*|subject|prior|pending|until|upon|condition\w*|"
    r"will|would|must|required?|need\w*)\s+(?:to\s+)?(?:the\s+)?(?:receipt\s+of\s+)?$")


def _approval_about(low: str, needle: str) -> bool:
    """A "received ... regulatory approvals" sentence that names the
    counterparty within 250 chars and is not forward-looking ("expect to
    receive", "subject to receipt of"). Peoples' 2026-10-05 Capital call
    transcript discussed the Citizens approvals; it showed on the Capital
    row as that deal's approval."""
    for m in _REG_APPROVAL_RE.finditer(low):
        before = low[max(0, m.start() - 60):m.start()]
        if _FORWARD_RE.search(before.strip() + " "):
            continue
        window = low[max(0, m.start() - 250):m.end() + 250]
        flat = " ".join(window.replace(",", " ").split())
        if (needle in flat) if " " in needle else token_in(needle, window):
            return True
    return False


def _merge_milestones(a: dict, b: dict) -> dict:
    votes = list(a.get("votes") or []) + list(b.get("votes") or [])
    reg = a.get("regulatory_approval") or b.get("regulatory_approval")
    return {"votes": votes, "regulatory_approval": reg}


# ── Wire pending leg (acquirers EDGAR cannot reach) ───────────────────────
_WIRE_DEAL_TITLE_RE = re.compile(
    r"(?i)\b(?:to\s+acquire|agreement\s+to\s+acquire|acquisition\s+of|definitive\s+"
    r"(?:merger\s+)?agreement|merger\s+agreement|to\s+merge|strategic\s+merger|"
    r"business\s+combination|to\s+combine|announce\w*\s+merger)\b")
_WIRE_DONE_TITLE_RE = re.compile(
    r"(?i)\b(?:complet(?:es|ed|ion)|closes?|closing\s+of|terminat(?:es|ed|ion)|"
    r"receives?\s+(?:all\s+)?(?:regulatory|shareholder|stockholder)|final\s+exchange)")
# RESOLUTION on the wire is completion / termination wording only — an
# approvals release is a milestone, not a close ("Peoples Bancorp Inc. and
# Capital Bancorp, Inc. Receive Regulatory Approvals", 2026-10-05, dropped
# the live deal from the board).
_WIRE_RESOLVED_TITLE_RE = re.compile(
    r"(?i)\b(?:complet(?:es|ed|ion)|closes|closing\s+of|terminat(?:es|ed|ion))")
_WIRE_PENDING_SCAN = 4


def find_pending_wire(ticker: str, subject_name: str) -> tuple[list[dict], bool]:
    """Live announced deals from the ACQUIRER's own wire releases — the only
    detection route for FDIC-registered / OTC acquirers (TowneBank announced
    blueharbor bank on the wire 2026-10-06 and files nothing on EDGAR).
    Deal-titled releases in the last 540 days, subject-guarded, story
    fetched and gated like the EDGAR legs; counterparty = the acquire-verb
    object (title first). Open status: no LATER release by the same filer
    with completion/termination wording that names the counterparty (the
    FDIC completion dedupe in ma_history is the second net). Same row schema
    as _find_pending_425 plus source='wire'."""
    prs = _wire_releases(ticker)
    if prs is None:
        return [], False
    from data.events.fmp_news import _is_subject
    floor = (date.today() - timedelta(days=_PENDING_MAX_AGE_DAYS)).isoformat()
    self_tok = brand_token(subject_name or "")
    obj_re = re.compile(_ACQUIRE_OBJ_RE.pattern, re.IGNORECASE)
    deals = []
    for p in prs:
        d = (p.get("published_at") or "")[:10]
        title = p.get("title") or ""
        if d < floor or not _WIRE_DEAL_TITLE_RE.search(title) \
                or _WIRE_DONE_TITLE_RE.search(title):
            continue
        if not _is_subject(ticker, f"{title} {p.get('text') or ''}"):
            continue
        deals.append((d, p))
    rows, fetch_failed, seen = [], False, set()
    for d, p in sorted(deals, key=lambda x: x[0], reverse=True)[:_WIRE_PENDING_SCAN]:
        time.sleep(_PAUSE_S)
        text = _wire_story_text(p.get("url") or "")
        if not text:
            fetch_failed = True
            continue
        if _COMPLETED_RE.search(text) or not _ANNOUNCE_RE.search(text):
            continue
        title = p.get("title") or ""
        best: dict[str, str] = {}
        for m in obj_re.finditer(title + ". " + text):
            cand = _clean_company_name(m.group(1))
            t = brand_token(cand)
            # A candidate that CONTAINS our own brand is us, whatever its
            # first token: the acquirer's release is indexed under the
            # target too, and "acquisition of Munster-based Finward" put
            # FNWD on the board acquiring itself (live 2026-07-21).
            if (not t or t == self_tok or token_in(self_tok, cand.lower())
                    or _digits_in_name(cand) or len(cand.split()) > 8):
                continue
            if len(cand) > len(best.get(t, "")):
                best[t] = cand
        # The title's object settles a multi-name body.
        title_toks = {brand_token(_clean_company_name(m.group(1)))
                      for m in obj_re.finditer(title + ". ")}
        pick = [t for t in best if t in title_toks] or list(best)
        if len(pick) != 1 or pick[0] in seen:
            continue
        ct = pick[0]
        counterparty = best[ct]
        later = [q for q in prs
                 if (q.get("published_at") or "")[:10] > d
                 and _WIRE_RESOLVED_TITLE_RE.search(q.get("title") or "")
                 and _release_resolves(q.get("title") or "", q.get("text") or "", [ct])]
        if later:
            continue              # closed, terminated or otherwise resolved
        seen.add(ct)
        cp_tick, cp_cert, cp_cik = _universe_match(counterparty)
        terms, t_ok = build_terms(text, d, acq_tick=ticker, tgt_tick=cp_tick)
        fetch_failed = fetch_failed or not t_ok
        value = extract_stated_value(text)
        rows.append({"announce_date": d, "direction": "acquisition",
                     "counterparty_name": counterparty,
                     "counterparty_ticker": cp_tick, "counterparty_cert": cp_cert,
                     "counterparty_cik": cp_cik,
                     "value_usd": value, "value_basis": "stated" if value else None,
                     "value_note": None, "target_cik": cp_cik,
                     "announce_url": p.get("url"), "terms": terms,
                     "source": "wire"})
    return rows, not fetch_failed


def _dedupe_tok(name: str | None) -> str | None:
    """Leg-dedupe key: the brand token, else (all-generic name — "First
    Savings Financial Group, Inc", First Merchants 2026) the first two
    words lowered, so the 425 row and the 8-K row of one deal collapse."""
    return row_token(name)


def find_pending_deals(cik, subject_name: str,
                       ticker: str | None = None) -> tuple[list[dict], bool]:
    """
    Live, STILL-OPEN announced deals for a holdco CIK: 425-episode (stock)
    rows plus cash-deal rows from find_open_announcements, deduped by
    counterparty brand token (425 wins), then each confirmed still open via
    _resolved_after — a candidate with a later Item 2.01/1.02 for the
    counterparty is dropped (it has closed or terminated), and an
    unverifiable candidate (EDGAR failed) is dropped AND makes ok=False so
    the caller does not cache. Same row schema as _find_pending_425.
    """
    rows_425, ok1 = _find_pending_425(cik, subject_name)
    cash, ok2 = find_open_announcements(cik, subject_name)
    seen = {_dedupe_tok(r["counterparty_name"]) for r in rows_425}
    merged = list(rows_425)
    for c in cash:
        tok = _dedupe_tok(c["counterparty_name"])
        if tok and tok in seen:
            continue
        seen.add(tok)
        # The release's own ticker pair ("Capital Bancorp, Inc. ... (NASDAQ:
        # CBNK)") beats a brand-token name match, which is ambiguous for a
        # generic brand like "Capital".
        cp_tick = cp_cert = cp_cik = None
        pair_tick = c.get("counterparty_ticker")
        if pair_tick:
            try:
                from data.bank_universe import get_universe
                info = get_universe().get(pair_tick) or {}
            except Exception:
                info = {}
            if info:
                cp_tick = pair_tick
                cp_cert = int(info.get("fdic_cert") or 0) or None
                cp_cik = int(info.get("cik") or 0) or None
        if not cp_tick:
            cp_tick, cp_cert, cp_cik = _universe_match(c["counterparty_name"])
        # A universe-matched counterparty shows under its universe name and
        # ticker (the acquire-verb capture was "Capital" for Capital
        # Bancorp, live 2026-09-30).
        cp_name = c["counterparty_name"]
        if cp_tick:
            from data.bank_mapping import get_name
            cp_name = get_name(cp_tick) or cp_name
            if isinstance(c.get("terms"), dict) and not c["terms"].get("tgt_ticker"):
                c["terms"]["tgt_ticker"] = cp_tick
        merged.append({"announce_date": c["announce_date"],
                       "direction": c["direction"],
                       "counterparty_name": cp_name,
                       "counterparty_ticker": cp_tick,
                       "counterparty_cert": cp_cert,
                       "counterparty_cik": cp_cik,
                       "value_usd": c["value_usd"],
                       "value_basis": c["value_basis"],
                       "value_note": c["value_note"],
                       "target_cik": c["target_cik"] or cp_cik
                       if c["direction"] == "sale" else c["target_cik"],
                       "announce_url": c["announce_url"],
                       "terms": c.get("terms")})
    ok3 = True
    if ticker:
        wire, ok3 = find_pending_wire(ticker, subject_name)
        for w in wire:
            tok = brand_token(w["counterparty_name"] or "")
            if tok and tok in seen:
                continue
            seen.add(tok)
            merged.append(w)

    # Open-status gate. Fetch the filer's own submissions ONCE (also the
    # authoritative completion source when we are the acquirer); a sale-side
    # row additionally checks the counterparty's own filings (the buyer
    # files the completion 2.01). resolved=None (no usable needle) drops the
    # row too — unprovable open status is never shown.
    subj_filings, sf_ok = (iter_submission_filings(int(cik)) if cik
                           else ([], True))
    ok = ok1 and ok2 and ok3 and sf_ok
    out = []
    for r in merged:
        # Complete the row: a counterparty outside the universe still has
        # an FDIC cert (valuation cells), and terms built without the
        # filer's ticker still get their implied price.
        if r["direction"] == "acquisition" and not r.get("counterparty_cert"):
            c_cert, _c_name, c_ok = fdic_cert_for_name(r["counterparty_name"])
            ok = ok and c_ok
            if c_cert:
                r["counterparty_cert"] = c_cert
        terms = r.get("terms")
        if (isinstance(terms, dict) and terms.get("implied_price") is None
                and (terms.get("exchange_ratio") or terms.get("cash_per_share"))):
            own = ticker
            cp_t = r.get("counterparty_ticker")
            acq_t = own if r["direction"] == "acquisition" else cp_t
            tgt_t = cp_t if r["direction"] == "acquisition" else own
            if not fill_implied_price(terms, r["announce_date"], acq_tick=acq_t,
                                      tgt_tick=tgt_t, close_lookup=_close_before):
                ok = False
        if r.get("source") == "wire" and not cik:
            # No EDGAR to consult: the wire completion check above and the
            # FDIC completion dedupe in ma_history are this row's gates.
            r["milestones"] = {"votes": [], "regulatory_approval": None}
            out.append(r)
            continue
        extra = []
        if r.get("counterparty_ticker"):
            try:
                from data.bank_mapping import get_name
                extra = [get_name(r["counterparty_ticker"]) or ""]
            except Exception:
                extra = []
        resolved, vok = _resolved_after(cik, r["counterparty_name"],
                                        r["announce_date"], filings=subj_filings,
                                        extra_names=extra)
        if not vok:
            ok = False
            continue
        if resolved or resolved is None:
            continue
        if _wire_resolved(ticker, _resolving_needles(r["counterparty_name"], extra),
                          r["announce_date"]):
            continue
        if r["direction"] == "sale" and r.get("counterparty_cik"):
            resolved2, vok2 = _resolved_after(
                r["counterparty_cik"], subject_name, r["announce_date"])
            if not vok2:
                ok = False
                continue
            if resolved2:      # None here = no extra signal; buyer-side
                continue       # check is best-effort on top of our own
        # Disclosed milestones (shareholder vote 5.07s, regulatory-approval
        # 8.01s) from BOTH filers' later 8-Ks — n/a until a filing says so.
        ms, m_ok = _milestones(cik, r["counterparty_name"], r["announce_date"],
                               subj_filings, side="acquirer"
                               if r["direction"] == "acquisition" else "target")
        ok = ok and m_ok
        if r.get("counterparty_cik"):
            ms2, m_ok2 = _milestones(
                r["counterparty_cik"], subject_name, r["announce_date"], None,
                side="target" if r["direction"] == "acquisition" else "acquirer")
            ok = ok and m_ok2
            ms = _merge_milestones(ms, ms2)
        r["milestones"] = ms
        out.append(r)
    return out, ok


if __name__ == "__main__":
    # LIVE smoke — First Hawaiian / TriCo Bancshares (announced 2026-07-13,
    # agreement dated 2026-07-12): all-stock, 2.095 FHB per TriCo share,
    # "$63.12 per [TriCo] share" at FHB's 2026-07-10 close — so the computed
    # value must be ≈ $63.12 × TriCo's cover shares (~$2.0-2.2B).
    rows, ok = find_pending_deals(36377, "First Hawaiian Bank")
    print("FHB pending:", rows, "ok =", ok)
    assert ok and len(rows) == 1, rows
    r = rows[0]
    assert r["announce_date"] == "2026-07-13", r
    assert r["direction"] == "acquisition", r
    assert "TriCo" in r["counterparty_name"], r
    assert r["counterparty_ticker"] == "TCBK", r
    assert r["value_basis"] == "computed" and "2.095" in (r["value_note"] or ""), r
    assert 1_800_000_000 < r["value_usd"] < 2_400_000_000, r
    print("SMOKE OK")
