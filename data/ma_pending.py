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
cluster, gaps ≤ 180 days) younger than 540 days = a live deal. Cash
deals file no 425 — a documented coverage gap (bank M&A is
overwhelmingly stock consideration).

DETAILS come from the episode's first 425s plus same-window announcement
8-Ks: the 425 legend's "Subject Company:" line names the deal's TARGET
authoritatively (subject == self -> this bank is being acquired), the
press release supplies the stated value or the exchange ratio. The local
ratio patterns tolerate commas inside company phrases ("First Hawaiian,
Inc.") and the "2.095 First Hawaiian shares for each TriCo share" form —
both live-verified on FHB/TriCo; they belong upstream in
data/ma_announcements once that file is free (a concurrent session is
editing it — do NOT duplicate further, merge later).

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
    _accession_text,
    _close_before,
    _shares_outstanding_asof,
    brand_token,
    extract_stated_value,
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
# Ratio forms, comma-tolerant side phrases:
#   "receive 2.095 First Hawaiian shares for each TriCo share"
#   "receive 0.5958 of a share of Columbia stock for each Umpqua share"
_RATIO_FORMS = [
    re.compile(r"receive\s+(\d{1,2}(?:\.\d{1,4})?)\s+"
               r"([A-Z][\w.,&'\- ]{1,60}?)\s+shares?\s+for\s+each\s+"
               r"([A-Z][\w.,&'\- ]{1,60}?)\s+shares?", re.IGNORECASE),
    re.compile(r"receive\s+(\d{1,2}(?:\.\d{1,4})?)\s+(?:of\s+a\s+share|"
               r"shares?)\s+of\s+([A-Z][\w.,&'\- ]{1,60}?)\s+(?:common\s+)?"
               r"stock\s+for\s+each(?:\s+share\s+of)?\s+"
               r"([A-Z][\w.,&'\- ]{1,60}?)\s+(?:common\s+stock|shares?|stock)",
               re.IGNORECASE),
]


def _extract_ratio(text: str):
    """(ratio, acquirer-side phrase, target-side phrase) or None; several
    DISTINCT ratios -> None (collars, never a guess)."""
    found = []
    for pat in _RATIO_FORMS:
        for m in pat.finditer(text):
            found.append((float(m.group(1)), m.group(2).strip(),
                          m.group(3).strip()))
    if not found or len({r for r, _, _ in found}) != 1:
        return None
    return found[0]


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


def find_pending_deals(cik, subject_name: str) -> tuple[list[dict], bool]:
    """
    Live announced deals for a holdco CIK (usually 0 or 1), or ([], ok).

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
    ratio_hit = _extract_ratio(corpus)
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
