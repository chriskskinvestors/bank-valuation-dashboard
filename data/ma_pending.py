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
    _COMPLETED_RE,
    _accession_text,
    _close_before,
    _shares_outstanding_asof,
    brand_token,
    extract_exchange_ratio,
    extract_stated_value,
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
# Ratio extraction lives upstream in ma_announcements.extract_exchange_ratio
# (comma-tolerant + the bare "2.095 First Hawaiian shares for each TriCo
# share" form — upstreamed 2026-07-14, closing the merge-later note).


def _universe_match(name: str):
    """(ticker, cert, cik) for a live universe bank whose name shares the
    brand token — unique hit only, else Nones (n/a over a wrong link)."""
    tok = brand_token(name or "")
    if not tok:
        return None, None, None
    try:
        from data.bank_universe import get_universe
        hits = [(t, info) for t, info in get_universe().items()
                if token_in(tok, (info.get("name") or "").lower())
                or tok == t.lower()]
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
    # Self-detection by IDENTITY, not name tokens: the FDIC bank name and
    # the holdco name can differ in brand token ("Tri Counties Bank" vs
    # "TriCo Bancshares") — resolve the subject through the universe and
    # compare CIKs; token equality is only the fallback for unmatched names.
    self_tok = brand_token(subject_name or "")
    subj_tok = brand_token(subject_co)
    _st, _sc, subj_cik = _universe_match(subject_co)
    subject_is_self = (subj_cik == int(cik)) if subj_cik else         bool(subj_tok and self_tok and subj_tok == self_tok)
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
                if ct and ct != self_tok:
                    other = " ".join(cand.split())
                    break
        if not other:
            return [], not fetch_failed
        direction, counterparty = "sale", other
    else:
        direction, counterparty = "acquisition", subject_co

    cp_tick, cp_cert, cp_cik = _universe_match(counterparty)

    # Value: stated first, else computed from the ratio (both parties alive,
    # so shares and price resolve from the live universe mapping).
    value = extract_stated_value(corpus)
    basis = "stated" if value else None
    note = None
    tgt_cik = None
    ratio_hit = extract_exchange_ratio(corpus)
    if ratio_hit:
        ratio, acq_side, tgt_side = ratio_hit
        # The ratio's per-share (target) side owns the deal value.
        t_tick, _t_cert, t_cik = _universe_match(tgt_side)
        a_tick, _a_cert, _a_cik = _universe_match(acq_side)
        tgt_cik = t_cik
        if value is None and t_cik and a_tick:
            shares, sh_end, s_ok = _shares_outstanding_asof(t_cik, announce)
            price, p_date, p_ok = _close_before(a_tick, announce)
            fetch_failed = fetch_failed or not (s_ok and p_ok)
            if shares and price:
                value = int(round(shares * ratio * price))
                basis = "computed"
                note = (f"computed: {ratio} × {a_tick} ${price:.2f} "
                        f"({p_date}) × {shares:,} {t_tick} shares ({sh_end})")

    url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
           f"{episode[0]['accession'].replace('-', '')}/{episode[0]['doc']}"
           if episode[0]["doc"] else None)
    row = {"announce_date": announce, "direction": direction,
           "counterparty_name": counterparty,
           "counterparty_ticker": cp_tick, "counterparty_cert": cp_cert,
           "counterparty_cik": cp_cik,
           "value_usd": value, "value_basis": basis, "value_note": note,
           "target_cik": tgt_cik, "announce_url": url}
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


_RESOLVE_SCAN_CAP = 12          # resolving-candidate documents fetched per
                                # deal — plenty (a filer rarely has more than
                                # a couple of 2.01/1.02/8.01s in the window)


def _resolved_after(filer_cik, other_name: str, announce_date: str,
                    filings=None) -> tuple[bool | None, bool]:
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
    needle = _resolving_needle(other_name)
    if not needle:
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
        if " " in needle:      # full-phrase needle (all-generic name)
            named = needle in " ".join(low.replace(",", " ").split())
        else:                  # distinctive brand token
            named = token_in(needle, low)
        if not named:
            continue
        if any(i in f.get("items", "") for i in _RESOLVING_ITEMS):
            return True, True          # 2.01/1.02 naming it = resolved
        if _COMPLETED_RE.search(text):
            return True, True          # 8.01 in completed tense = resolved
    return False, not fetch_failed


def find_pending_deals(cik, subject_name: str) -> tuple[list[dict], bool]:
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
    seen = {brand_token(r["counterparty_name"] or "") for r in rows_425}
    merged = list(rows_425)
    for c in cash:
        tok = brand_token(c["counterparty_name"] or "")
        if tok and tok in seen:
            continue
        seen.add(tok)
        cp_tick, cp_cert, cp_cik = _universe_match(c["counterparty_name"])
        merged.append({"announce_date": c["announce_date"],
                       "direction": c["direction"],
                       "counterparty_name": c["counterparty_name"],
                       "counterparty_ticker": cp_tick,
                       "counterparty_cert": cp_cert,
                       "counterparty_cik": cp_cik,
                       "value_usd": c["value_usd"],
                       "value_basis": c["value_basis"],
                       "value_note": c["value_note"],
                       "target_cik": c["target_cik"] or cp_cik
                       if c["direction"] == "sale" else c["target_cik"],
                       "announce_url": c["announce_url"]})

    # Open-status gate. Fetch the filer's own submissions ONCE (also the
    # authoritative completion source when we are the acquirer); a sale-side
    # row additionally checks the counterparty's own filings (the buyer
    # files the completion 2.01). resolved=None (no usable needle) drops the
    # row too — unprovable open status is never shown.
    subj_filings, sf_ok = (iter_submission_filings(int(cik)) if cik
                           else ([], True))
    ok = ok1 and ok2 and sf_ok
    out = []
    for r in merged:
        resolved, vok = _resolved_after(cik, r["counterparty_name"],
                                        r["announce_date"], filings=subj_filings)
        if not vok:
            ok = False
            continue
        if resolved or resolved is None:
            continue
        if r["direction"] == "sale" and r.get("counterparty_cik"):
            resolved2, vok2 = _resolved_after(
                r["counterparty_cik"], subject_name, r["announce_date"])
            if not vok2:
                ok = False
                continue
            if resolved2:      # None here = no extra signal; buyer-side
                continue       # check is best-effort on top of our own
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
