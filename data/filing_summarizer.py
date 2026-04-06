"""
Filing summarizer — fetches SEC filing/press release content and
generates AI summaries using the Anthropic Claude API.
"""

import re
import requests
from html.parser import HTMLParser

import streamlit as st

SEC_HEADERS = {"User-Agent": "KSK Investors admin@kskinvestors.com"}


class _TextExtractor(HTMLParser):
    """Simple HTML-to-text parser."""

    def __init__(self):
        super().__init__()
        self.text: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self.text.append(data)


def _html_to_text(html: str) -> str:
    """Convert HTML to clean text."""
    parser = _TextExtractor()
    parser.feed(html)
    text = " ".join(parser.text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _strip_sec_boilerplate(text: str) -> str:
    """
    Remove SEC filing boilerplate (cover page, legal headers) to get
    to the actual substance of the filing.
    """
    # Common markers that signal the start of real content
    content_markers = [
        # Press releases / earnings
        "reports fourth quarter", "reports third quarter",
        "reports second quarter", "reports first quarter",
        "announces fourth quarter", "announces third quarter",
        "announces second quarter", "announces first quarter",
        "reports full year", "reports annual",
        "financial results for",
        "today announced", "today reported",
        # 10-K / 10-Q content starts
        "part i", "item 1.", "item 1 ",
        "business overview", "overview",
        "management's discussion",
        # 8-K content
        "item 2.02", "item 1.01", "item 5.02",
        "press release", "news release",
        # General
        "highlights", "selected financial data",
    ]

    text_lower = text.lower()
    best_pos = len(text)

    for marker in content_markers:
        pos = text_lower.find(marker)
        if pos != -1 and pos < best_pos:
            best_pos = pos

    # If we found a content marker, back up to the start of its sentence
    if best_pos < len(text):
        # Go back to find sentence/paragraph start
        search_start = max(0, best_pos - 200)
        chunk = text[search_start:best_pos]
        # Find last paragraph break or period before the marker
        for sep in ["\n", ". ", "— "]:
            last_break = chunk.rfind(sep)
            if last_break != -1:
                best_pos = search_start + last_break + len(sep)
                break

    # Skip at minimum the first 100 chars (always boilerplate)
    if best_pos > 100:
        return text[best_pos:].strip()

    return text


def fetch_filing_text(url: str, max_chars: int = 15000) -> str:
    """
    Fetch a filing document from SEC EDGAR and return clean text
    with boilerplate stripped.
    Truncates to max_chars to stay within API limits.
    """
    try:
        resp = requests.get(url, headers=SEC_HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        return f"[Error fetching filing: {e}]"

    content_type = resp.headers.get("Content-Type", "")

    if "html" in content_type or url.endswith(".htm") or url.endswith(".html"):
        text = _html_to_text(resp.text)
    else:
        text = resp.text

    # Strip SEC boilerplate to get to actual content
    text = _strip_sec_boilerplate(text)

    # Truncate intelligently — try to end at a sentence boundary
    if len(text) > max_chars:
        truncated = text[:max_chars]
        last_period = truncated.rfind(".")
        if last_period > max_chars * 0.7:
            text = truncated[: last_period + 1]
        else:
            text = truncated + "..."

    return text


def find_press_release_url(cik: int, accession: str) -> str | None:
    """
    Given an 8-K filing, find the EX-99.1 press release exhibit URL.
    """
    acc_clean = accession.replace("-", "")
    idx_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/"

    try:
        resp = requests.get(idx_url, headers=SEC_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception:
        return None

    # Look for EX-99.1 (the press release is almost always here)
    patterns = [
        r'href="([^"]*ex-?99[\-_]?1[^"]*\.htm[^"]*)"',
        r'href="([^"]*ex-?99[^"]*\.htm[^"]*)"',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, resp.text, re.IGNORECASE)
        if matches:
            url = matches[0]
            if not url.startswith("http"):
                url = f"https://www.sec.gov{url}"
            return url

    return None


@st.cache_data(ttl=3600, show_spinner=False)
def summarize_filing(filing_text: str, form_type: str, ticker: str) -> str:
    """
    Generate an AI summary of a filing using Claude.

    Requires ANTHROPIC_API_KEY in Streamlit secrets or environment.
    """
    api_key = None

    # Try Streamlit secrets first
    try:
        api_key = st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:
        pass

    # Fall back to environment
    if not api_key:
        import os
        api_key = os.environ.get("ANTHROPIC_API_KEY")

    if not api_key:
        return _extractive_summary(filing_text, form_type)

    # Call Claude API
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)

        if form_type in ("8-K", "8-K/A"):
            prompt = (
                f"Summarize this {form_type} press release / earnings announcement for {ticker} "
                f"in 3-4 concise sentences. Focus on: key financial results (EPS, revenue, NIM, "
                f"loan/deposit growth), management outlook, and any notable items (dividends, "
                f"buybacks, M&A, credit quality changes). Be specific with numbers.\n\n"
                f"Filing text:\n{filing_text}"
            )
        elif form_type in ("10-K", "10-K/A"):
            prompt = (
                f"Summarize this {form_type} annual report for {ticker} in 3-4 concise sentences. "
                f"Focus on: full-year financial performance, key trends in loans/deposits/NIM, "
                f"credit quality, capital position, and forward outlook. Be specific.\n\n"
                f"Filing text:\n{filing_text}"
            )
        elif form_type in ("10-Q", "10-Q/A"):
            prompt = (
                f"Summarize this {form_type} quarterly report for {ticker} in 3-4 concise sentences. "
                f"Focus on: quarterly financial results, changes from prior quarter/year, "
                f"NIM trends, credit metrics, and notable developments. Be specific.\n\n"
                f"Filing text:\n{filing_text}"
            )
        else:
            prompt = (
                f"Summarize this SEC {form_type} filing for {ticker} in 2-3 concise sentences. "
                f"What is the key information or action disclosed?\n\n"
                f"Filing text:\n{filing_text}"
            )

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text

    except Exception as e:
        return _extractive_summary(filing_text, form_type)


def _extractive_summary(text: str, form_type: str) -> str:
    """
    Fallback: extract key sentences when no AI API is available.
    Finds sentences with the most financial substance.
    """
    # High-value keywords (specific financial metrics)
    high_keywords = [
        "earnings per share", "eps of", "diluted eps",
        "net income of", "net income was", "net income increased",
        "net interest margin", "nim of", "nim was", "nim increased",
        "total deposits", "total loans", "total assets of",
        "book value per", "tangible book value",
        "dividend of", "declared a dividend",
        "return on average", "roaa", "roae", "roatce",
        "efficiency ratio", "nonperforming",
    ]

    # Medium-value keywords
    med_keywords = [
        "increased", "decreased", "grew", "declined",
        "up from", "compared to", "year-over-year",
        "basis points", "percent", "million", "billion",
        "per share", "per common share",
        "reported", "announced", "quarterly",
    ]

    # Junk patterns to skip
    skip_patterns = [
        "securities and exchange", "commission file",
        "registrant", "check the appropriate", "pursuant to",
        "incorporated by reference", "exhibit",
        "form 8-k", "form 10-k", "form 10-q",
        "zip code", "telephone number", "irs employer",
        "state of incorporation", "exact name",
    ]

    # Split into sentences
    sentences = re.split(r"(?<=[.!?])\s+", text[:10000])
    scored = []
    for i, s in enumerate(sentences):
        s_clean = s.strip()
        if len(s_clean) < 30 or len(s_clean) > 500:
            continue

        s_lower = s_clean.lower()

        # Skip boilerplate
        if any(skip in s_lower for skip in skip_patterns):
            continue

        score = 0
        score += sum(3 for kw in high_keywords if kw in s_lower)
        score += sum(1 for kw in med_keywords if kw in s_lower)

        # Boost sentences with dollar amounts or percentages
        if re.search(r"\$[\d,.]+", s_clean):
            score += 2
        if re.search(r"\d+\.?\d*\s*%", s_clean):
            score += 1

        # Slight boost for earlier content sentences (after boilerplate stripped)
        if i < 5:
            score += 1

        if score > 0:
            scored.append((score, s_clean))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Take top 4 most informative sentences
    top = scored[:4]
    if not top:
        # Last resort: just take first 300 chars of content
        clean = text.strip()
        return clean[:400] + "..." if len(clean) > 400 else clean

    # Re-order by appearance in original text
    top_texts = [t[1] for t in top]
    ordered = sorted(top_texts, key=lambda s: text.find(s))

    return " ".join(ordered)
