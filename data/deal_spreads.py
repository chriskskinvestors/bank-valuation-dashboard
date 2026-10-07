"""
Merger-arb spread history for the Transactions › Recent Deals spread
tracker (owner 2026-10-07: "an interactive chart component underneath the
current content to track how the spreads change"; owner choices: gross and
annualized spread plus acquirer & target price, all / one / a few deals,
overlay + drill-down, backfilled from announcement).

Every pending deal on the board with a LISTED target, an acquirer price
history and a computable per-share offer gets a daily series from its
announcement date to today, recomputed from that day's closing prices and
the deal's fixed terms through the SAME merger_arb the board's live columns
use (one formula, never two):

    implied offer_d = per the stated consideration (ratio × acquirer close_d
                      [+ cash], blended at the stated proration for an
                      election, fixed for all-cash)
    gross_d         = implied offer_d ÷ target close_d − 1
    annualized_d    = gross_d × 365 ÷ days from d to the STATED expected close

A deal whose offer cannot be priced honestly (election without a stated mix,
aggregate-only cash leg, private target) gets no series and is listed with
the reason — never an approximation. A single day whose gross spread lands
outside ±75% is a bad print, not a spread: that point is dropped and
counted.

Jobs build (jobs/refresh_deal_comps, both the nightly walk and the pending
fast pass), renders read: the UI only ever reads SPREADS_KEY.
"""
from __future__ import annotations

from datetime import date, datetime

SPREADS_KEY = "deal_spread_history:v1"
_BAD_PRINT = 0.75            # |gross| beyond this on one day = a bad close
_SPIKE = 0.08                # a one-day move this large that reverses = bad print
_HISTORY_PERIOD = "2Y"       # pending deals are < 540 days old


def deal_key(d: dict) -> str:
    return (f"{d.get('buyer_ticker') or '?'}:"
            f"{d.get('target_ticker') or d.get('target_name') or '?'}:"
            f"{d.get('announce_date') or '?'}")


def _closes(hist) -> dict[str, float]:
    """{ISO date: close} from a get_history DataFrame or a list of records."""
    out: dict[str, float] = {}
    if hist is None:
        return out
    recs = hist.to_dict("records") if hasattr(hist, "to_dict") else list(hist)
    for r in recs:
        dt, c = r.get("date"), r.get("close")
        if dt is None or c is None:
            continue
        iso = dt.date().isoformat() if hasattr(dt, "date") else str(dt)[:10]
        try:
            c = float(c)
        except (TypeError, ValueError):
            continue
        if c > 0:
            out[iso] = c
    return out


def spread_series(terms: dict, announce_date: str, acq_hist, tgt_hist,
                  acq_ticker: str | None = None) -> tuple[list[dict], int]:
    """([{date, acq, tgt, offer, gross, annualized, days}], dropped) for every
    trading day after ``announce_date`` with both closes. Pure."""
    from data.deal_comps import merger_arb
    acq, tgt = _closes(acq_hist), _closes(tgt_hist)
    out, dropped = [], 0
    # An all-cash offer does not move with the acquirer (who may not even be
    # listed — First Seacoast's buyer is a mutual holding company).
    cash_only = (terms or {}).get("consideration") == "cash"
    for iso in sorted(set(tgt) if cash_only else set(acq) & set(tgt)):
        # From the first trading day AFTER the announcement: a deal announced
        # after the close leaves that day's target close at the pre-deal
        # price (Colony/First Reliance and First Financial/Finward read ~30%
        # on announcement day in the first preview, 2026-10-07).
        if iso <= announce_date:
            continue
        arb = merger_arb(terms, acq.get(iso), tgt[iso], date.fromisoformat(iso),
                         acq_ticker=acq_ticker)
        g = arb["gross_spread"]
        if g is None:
            continue
        if abs(g) > _BAD_PRINT:
            dropped += 1
            continue
        out.append({"date": iso, "acq": acq.get(iso), "tgt": tgt[iso],
                    "offer": round(arb["implied_offer"], 4),
                    "gross": round(g, 6),
                    "annualized": (round(arb["annualized_spread"], 6)
                                   if arb["annualized_spread"] is not None else None),
                    "days": arb["days_to_close"]})
    # A one-day spike that reverses the next day is a bad print on a thinly
    # traded target (two ~30% one-day spikes on OTC targets in the first
    # preview, 2026-10-07), not a spread: drop it and count it.
    # Compared against the last KEPT day, so the day after a spike is not
    # itself mistaken for a dip.
    kept: list[dict] = out[:1]
    for i in range(1, len(out)):
        if i < len(out) - 1:
            a = out[i]["gross"] - kept[-1]["gross"]
            b = out[i]["gross"] - out[i + 1]["gross"]
            if abs(a) > _SPIKE and abs(b) > _SPIKE and a * b > 0:
                dropped += 1
                continue
        kept.append(out[i])
    return kept, dropped


def _skip_reason(d: dict) -> str | None:
    terms = d.get("terms") or {}
    if not d.get("target_ticker"):
        return "target not listed — no price history"
    if not d.get("buyer_ticker") and not ((d.get("terms") or {}).get("consideration") == "cash"
                                          and (d.get("terms") or {}).get("cash_per_share")):
        return "acquirer not listed"
    mix = terms.get("consideration")
    if mix == "election" and (terms.get("stock_pct") is None
                              or terms.get("cash_pct") is None):
        return "cash/stock election with no stated proration — offer not computable"
    if mix == "mixed" and not terms.get("cash_per_share"):
        return "cash leg stated only in aggregate — per-share offer not computable"
    if not (terms.get("exchange_ratio") or (mix == "cash" and terms.get("cash_per_share"))):
        return "no exchange ratio or per-share cash in the filings"
    return None


def build_spread_histories(deals: list[dict], history=None) -> dict:
    """{"built_at", "deals": {key: {...meta, series, dropped}}, "skipped":
    [{key, label, reason}]} for the snapshot's pending deals. ``history`` is
    the price-history function (ticker -> DataFrame); default FMP EOD."""
    if history is None:
        from data.fmp_client import get_history
        history = lambda t: get_history(t, _HISTORY_PERIOD)  # noqa: E731
    out: dict = {"built_at": datetime.now().isoformat(), "deals": {}, "skipped": []}
    cache: dict[str, object] = {}

    def _hist(t):
        if t not in cache:
            try:
                cache[t] = history(t)
            except Exception as e:                       # one ticker ≠ the build
                print(f"[deal_spreads] history {t}: {type(e).__name__}: {e}")
                cache[t] = None
        return cache[t]

    for d in deals:
        if d.get("status") != "pending":
            continue
        key = deal_key(d)
        label = f"{d.get('target_ticker') or d.get('target_name')} ← {d.get('buyer_ticker')}"
        reason = _skip_reason(d)
        if reason:
            out["skipped"].append({"key": key, "label": label,
                                   "target_name": d.get("target_name"), "reason": reason})
            continue
        terms = d.get("terms") or {}
        series, dropped = spread_series(terms, d.get("announce_date") or "",
                                        _hist(d["buyer_ticker"]) if d.get("buyer_ticker") else None,
                                        _hist(d["target_ticker"]),
                                        acq_ticker=d["buyer_ticker"])
        if not series:
            out["skipped"].append({"key": key, "label": label,
                                   "target_name": d.get("target_name"),
                                   "reason": "no overlapping daily closes since announcement"})
            continue
        ms = d.get("milestones") or {}
        out["deals"][key] = {
            "label": label,
            "target_ticker": d["target_ticker"], "target_name": d.get("target_name"),
            "buyer_ticker": d["buyer_ticker"], "buyer_name": d.get("buyer_name"),
            "announce_date": d.get("announce_date"),
            "expected_close_date": terms.get("expected_close_date"),
            "consideration": terms.get("consideration"),
            "exchange_ratio": terms.get("exchange_ratio"),
            "cash_per_share": terms.get("cash_per_share"),
            "milestones": ([{"date": v["date"], "label": f"{v['side'].capitalize()} vote"}
                            for v in (ms.get("votes") or []) if v.get("date")]
                           + ([{"date": ms["regulatory_approval"]["date"],
                                "label": "Regulatory approval"}]
                              if (ms.get("regulatory_approval") or {}).get("date") else [])),
            "series": series, "dropped": dropped,
        }
    return out


def refresh_spread_histories(snapshot: dict | None, history=None) -> dict | None:
    """Build from the served snapshot and cache. None when there is no
    snapshot; a build with zero series is still written (it carries the
    skipped reasons the tracker shows)."""
    if not snapshot or not isinstance(snapshot.get("deals"), list):
        return None
    from data import cache
    built = build_spread_histories(snapshot["deals"], history=history)
    cache.put(SPREADS_KEY, built)
    return built


def get_spread_histories() -> dict | None:
    """The cached tracker data (UI read path — never builds)."""
    from data import cache
    v = cache.get(SPREADS_KEY, max_age_s=None)
    return v if isinstance(v, dict) and isinstance(v.get("deals"), dict) else None
