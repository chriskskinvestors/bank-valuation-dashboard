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
