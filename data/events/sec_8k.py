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
    "1.01": "Material Definitive Agreement",
    "2.01": "Completion of Acquisition",
    "2.02": "Earnings / Results of Operations",
    "3.02": "Unregistered Sale of Equity",
    "5.02": "Officer/Director Change",
    "5.03": "Bylaw / Charter Amendment",
    "5.07": "Vote of Security Holders",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements / Exhibits",
}


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
    """Build a human-readable headline from the item list + filing description."""
    labels = [ITEM_LABELS.get(it, f"Item {it}") for it in items]
    if desc and desc.strip().lower() not in ("", "8-k"):
        return f"{', '.join(labels)} — {desc.strip()}"
    return ", ".join(labels) if labels else "8-K filing"


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
            if not form.startswith("8-K"):
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
