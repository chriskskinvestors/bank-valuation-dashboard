"""
SEC 8-K adapter.

8-Ks are SEC filings disclosing material events. For banks, the most
relevant items are:
  • Item 2.02 — Results of Operations and Financial Condition (earnings)
  • Item 7.01 — Regulation FD Disclosure (press releases)
  • Item 8.01 — Other Events (M&A, dividends, etc.)
  • Item 5.02 — Departure/Election of Directors or Officers
  • Item 1.01 — Material Definitive Agreement
  • Item 2.01 — Completion of Acquisition or Disposition

Every material press release a public bank issues must also be filed
as an 8-K within 4 business days, so this single source captures ~95%
of material company news with structured timestamps + the actual filing
text we can summarize via Claude.
"""

from __future__ import annotations
import re
from datetime import datetime, timezone, timedelta
from typing import Iterable

import requests

from data.bank_mapping import get_cik
from data.events.base import Event, SourceAdapter


SEC_HEADERS = {
    "User-Agent": "BankValuationDashboard chris@kskinvestors.com",
    "Accept": "application/json",
}

ITEM_LABELS = {
    "1.01": "Material Agreement",
    "1.02": "Termination of Material Agreement",
    "2.01": "Acquisition / Disposition Completed",
    "2.02": "Earnings / Results",
    "2.03": "New Financial Obligation",
    "2.05": "Restructuring / Exit Costs",
    "2.06": "Material Impairment",
    "3.01": "Listing / Delisting Notice",
    "3.02": "Unregistered Equity Sale",
    "3.03": "Change to Securityholder Rights",
    "4.01": "Change in Auditor",
    "4.02": "Financial Restatement",
    "5.01": "Change in Control",
    "5.02": "Officer / Director Change",
    "5.03": "Bylaw / Charter Amendment",
    "5.07": "Shareholder Vote Results",
    "7.01": "Reg FD Disclosure",
    "8.01": "Other Material Event",
    "9.01": "Financial Statements / Exhibits",
}

# Most material first — the headline leads with the highest-priority item present
# (so a filing isn't headlined "Financial Statements / Exhibits", which is almost
# always boilerplate attached to a more substantive item).
_ITEM_PRIORITY = ["4.02", "5.01", "2.06", "2.01", "1.01", "5.02", "2.02", "3.01",
                  "2.05", "2.03", "3.02", "5.03", "5.07", "1.02", "3.03", "4.01",
                  "7.01", "8.01", "9.01"]


def _classify_event_type(items: list[str], description: str) -> str:
    """Map 8-K item list to a coarse event_type."""
    if "2.02" in items:
        return "earnings"
    if "1.01" in items or "2.01" in items:
        return "m_and_a"
    if "5.02" in items:
        return "executive_change"
    if "7.01" in items or "8.01" in items:
        return "press_release"
    if "5.07" in items:
        return "shareholder_vote"
    return "regulatory"


def _build_headline(items: list[str], desc: str) -> str:
    """Build a human-readable headline, led by the most material 8-K item."""
    has_desc = bool(desc and desc.strip().lower() not in ("", "8-k"))
    if not items:
        return desc.strip() if has_desc else "8-K filing"
    ranked = sorted(items, key=lambda it: _ITEM_PRIORITY.index(it)
                    if it in _ITEM_PRIORITY else 99)
    lead = ITEM_LABELS.get(ranked[0], f"Item {ranked[0]}")
    head = f"8-K · {lead}"
    if has_desc:
        head += f" — {desc.strip()}"
    return head


class SEC8KAdapter(SourceAdapter):
    """Polls SEC EDGAR for recent 8-K filings per ticker."""

    name = "sec_8k"

    # How far back to look on each poll. Cloud Scheduler will run this
    # frequently enough (e.g. every 30 min) that 7 days is generous overlap.
    LOOKBACK_DAYS = 7

    def poll(self, tickers: list[str], since: datetime | None = None) -> list[Event]:
        out: list[Event] = []
        cutoff = since or (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS))

        for ticker in tickers:
            try:
                events = self._poll_one(ticker, cutoff)
                out.extend(events)
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    continue  # no CIK / no filings — silent
                print(f"[8K] {ticker} HTTP error: {e}")
            except Exception as e:
                print(f"[8K] {ticker} error: {type(e).__name__}: {e}")
        return out

    def _poll_one(self, ticker: str, cutoff: datetime) -> list[Event]:
        cik = get_cik(ticker)
        if not cik:
            return []

        # SEC submissions endpoint gives the latest filings per CIK
        url = f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"
        r = requests.get(url, headers=SEC_HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()

        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])
        items_list = recent.get("items", [])

        events: list[Event] = []
        for i, form in enumerate(forms):
            is_8k = form.startswith("8-K")
            # 10-K / 10-Q (and amendments) update the company's XBRL facts, so
            # they're what the financial views depend on. We emit them as
            # events AND the runner uses them to invalidate the fundamentals
            # cache, so a new periodic filing flows into the dashboard within
            # one poll cycle (~30 min) instead of waiting for the 24h TTL.
            is_periodic = form in ("10-K", "10-K/A", "10-Q", "10-Q/A")
            if not (is_8k or is_periodic):
                continue
            try:
                filed_at = datetime.strptime(dates[i], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except (ValueError, IndexError):
                continue
            if filed_at < cutoff:
                # Sorted newest first, so we can stop scanning once we're past cutoff
                break

            accession = accessions[i] if i < len(accessions) else ""
            primary = primary_docs[i] if i < len(primary_docs) else ""

            if is_periodic:
                acc_nodash = accession.replace("-", "")
                filing_url = (
                    f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                    f"{acc_nodash}/{primary}" if primary else
                    f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                    f"{acc_nodash}/{accession}-index.htm"
                )
                events.append(Event(
                    ticker=ticker.upper(),
                    source=self.name,
                    event_type="filing",
                    headline=f"{form} filed",
                    published_at=filed_at,
                    url=filing_url,
                    summary="",
                    external_id=accession,
                    raw={"cik": cik, "form": form, "filing_date": dates[i]},
                ))
                continue

            item_str = items_list[i] if i < len(items_list) else ""
            items = [it.strip() for it in re.split(r"[,;]", item_str) if it.strip()]

            # Skip pure-boilerplate filings: an 8-K whose ONLY item is 9.01
            # (Financial Statements / Exhibits) is just an exhibit attachment
            # with no substantive event — it would surface as the opaque
            # "8-K · Financial Statements / Exhibits". Material filings always
            # carry a real item alongside 9.01, so nothing substantive is lost.
            if set(items) == {"9.01"}:
                continue

            # Build the URL to the filing index page on EDGAR
            acc_nodash = accession.replace("-", "")
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{acc_nodash}/{primary}" if primary else
                f"https://www.sec.gov/cgi-bin/browse-edgar"
                f"?action=getcompany&CIK={cik}&type=8-K"
            )

            events.append(Event(
                ticker=ticker.upper(),
                source=self.name,
                event_type=_classify_event_type(items, ""),
                headline=_build_headline(items, ""),
                published_at=filed_at,
                url=filing_url,
                summary="",  # populated by summarizer pass if API key available
                external_id=accession,
                raw={
                    "cik": cik,
                    "form": form,
                    "items": items,
                    "primary_document": primary,
                    "filing_date": dates[i],
                },
            ))
        return events


# ──────────────────────────────────────────────────────────────────────────
# Recent-filings feed adapter (FAST poll) — every bank's 8-Ks in ONE call
# ──────────────────────────────────────────────────────────────────────────

# EDGAR's "current filings" Atom feed lists the latest 8-Ks across EVERY filer
# in a single request — and the entry carries the CIK (in the title), the
# accession (in the <id>), the filing time, AND the item codes right in the
# summary ("Item 2.02: ...", "Item 5.02: ..."). So the FAST poll can cover the
# whole universe's material filings in one call, staying sub-minute, instead of
# looping per-CIK. Same source name + accession external_id as SEC8KAdapter, so
# the two dedup. The per-CIK SEC8KAdapter stays the FULL-poll backstop: the feed
# only holds the latest ~100 filings, so it could miss across a long off-hours
# gap — the 30-min/6-h full poll re-pulls per-CIK and can't miss.

_GETCURRENT_8K_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K"
    "&company=&dateb=&owner=include&count=100&output=atom"
)
_TITLE_CIK_RE = re.compile(r"\((\d{4,10})\)\s*\(Filer\)", re.IGNORECASE)
_FEED_ITEM_RE = re.compile(r"Item\s+(\d+\.\d+)")
_FEED_ACC_RE = re.compile(r"accession-number=([\d-]+)")
_FEED_ACC_SUM_RE = re.compile(r"AccNo:\s*([\d-]+)", re.IGNORECASE)


class SEC8KRecentAdapter(SourceAdapter):
    """All-banks 8-K via EDGAR's recent-filings Atom feed (one call). Used by the
    FAST poll profile; dedups with SEC8KAdapter on (source, accession)."""

    name = "sec_8k"
    LOOKBACK_DAYS = 7

    def poll(self, tickers: list[str], since: datetime | None = None) -> list[Event]:
        from data.events.wire_base import fetch_rss
        cutoff = since or (datetime.now(timezone.utc) - timedelta(days=self.LOOKBACK_DAYS))

        # CIK -> ticker for the banks we track (get_cik is a local lookup).
        cik_map: dict[int, str] = {}
        for t in tickers:
            c = get_cik(t)
            if not c:
                continue
            try:
                cik_map[int(c)] = t.upper()
            except (TypeError, ValueError):
                continue
        if not cik_map:
            return []

        try:
            feed = fetch_rss(_GETCURRENT_8K_URL,
                             user_agent="BankValuationDashboard chris@kskinvestors.com")
        except Exception as e:
            print(f"[sec_8k_recent] feed error: {type(e).__name__}: {e}")
            return []

        out: list[Event] = []
        seen: set[str] = set()
        for it in feed:
            m = _TITLE_CIK_RE.search(it.title or "")
            if not m:
                continue
            try:
                cik = int(m.group(1))
            except ValueError:
                continue
            ticker = cik_map.get(cik)
            if not ticker:
                continue

            item_codes = _FEED_ITEM_RE.findall(it.summary or "")
            # Drop pure-boilerplate (exhibits-only) filings, same as SEC8KAdapter.
            if set(item_codes) == {"9.01"}:
                continue

            pub = it.published or datetime.now(timezone.utc)
            if pub < cutoff:
                continue

            am = (_FEED_ACC_RE.search(it.guid or "")
                  or _FEED_ACC_SUM_RE.search(it.summary or ""))
            accession = am.group(1) if am else ""
            if not accession or accession in seen:
                continue
            seen.add(accession)

            out.append(Event(
                ticker=ticker,
                source=self.name,
                event_type=_classify_event_type(item_codes, ""),
                headline=_build_headline(item_codes, ""),
                published_at=pub,
                url=it.link or "",
                summary="",
                external_id=accession,
                raw={"cik": cik, "form": "8-K", "items": item_codes,
                     "source_feed": "getcurrent"},
            ))
        return out
