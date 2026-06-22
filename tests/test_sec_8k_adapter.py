"""
Pins two SEC-leg quality tweaks (2026-06-16):

  1. pure-boilerplate skip — an 8-K whose ONLY item is 9.01 (Financial
     Statements / Exhibits) is an exhibit attachment with no substantive event
     and must NOT be emitted; filings carrying a real item alongside 9.01 stay.
  2. summarizer prioritization — _is_high_signal_8k flags the material-but-opaque
     item types (M&A, officer, restatement, regulatory) that jump the summarizer
     queue when budget is tight; routine earnings (2.02) do not.

SEC HTTP + CIK lookup are mocked; no network.
"""
import json
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

# Stub streamlit before importing data modules (house pattern).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

import data.events.sec_8k as sec_8k  # noqa: E402
import data.events.wire_base as wire_base  # noqa: E402
from data.events.sec_8k import SEC8KAdapter, SEC8KRecentAdapter  # noqa: E402
from data.events.wire_base import RSSItem  # noqa: E402
import data.filing_summarizer as filing_summarizer  # noqa: E402
from jobs.poll_events import (  # noqa: E402
    _is_high_signal_8k, _clean_summary, _resolve_8k_doc_url,
)

PAST = datetime(2020, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 6, 18, 12, 0, tzinfo=timezone.utc)


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def _submissions(items_per_filing):
    n = len(items_per_filing)
    return {"filings": {"recent": {
        "form": ["8-K"] * n,
        "filingDate": [f"2026-06-{15 - i:02d}" for i in range(n)],
        "accessionNumber": [f"0001-26-00000{i+1}" for i in range(n)],
        "primaryDocument": [f"doc{i}.htm" for i in range(n)],
        "items": items_per_filing,
    }}}


class TestBoilerplateSkip(unittest.TestCase):
    def _poll(self, items_per_filing):
        payload = _submissions(items_per_filing)
        with patch.object(sec_8k, "get_cik", return_value=320193), \
             patch.object(sec_8k.requests, "get", return_value=_FakeResp(payload)):
            return SEC8KAdapter()._poll_one("TEST", PAST)

    def test_pure_9_01_filing_skipped(self):
        evs = self._poll(["9.01"])
        self.assertEqual(evs, [], "an exhibits-only 8-K must not be emitted")

    def test_substantive_filings_kept(self):
        evs = self._poll(["9.01", "2.02,9.01", "8.01", "9.01"])
        # Two pure-9.01 dropped; earnings (2.02) and other-material (8.01) kept.
        self.assertEqual(len(evs), 2)
        leads = {e.headline for e in evs}
        self.assertTrue(any("Earnings" in h for h in leads))
        self.assertTrue(any("Other Material Event" in h for h in leads))
        self.assertFalse(any("Financial Statements / Exhibits" in h for h in leads))

    def test_real_item_with_9_01_not_dropped(self):
        # 9.01 is almost always attached to a real item — that filing stays.
        evs = self._poll(["5.02,9.01"])
        self.assertEqual(len(evs), 1)
        self.assertIn("Officer / Director Change", evs[0].headline)


def _entry(cik10, items_line, acc, title="Test Bank Corp", pub=NOW):
    return RSSItem(
        title=f"8-K - {title} ({cik10}) (Filer)",
        summary=f"Filed: 2026-06-18 AccNo: {acc} Size: 100 KB {items_line}",
        link=f"https://www.sec.gov/Archives/edgar/data/{int(cik10)}/x-index.htm",
        published=pub,
        guid=f"urn:tag:sec.gov,2008:accession-number={acc}",
    )


class TestRecentFeedAdapter(unittest.TestCase):
    """SEC8KRecentAdapter — all-banks 8-K from EDGAR's recent-filings feed."""

    def _poll(self, entries, tickers=("TBNK",), cik=123456):
        with patch.object(sec_8k, "get_cik",
                          side_effect=lambda t: cik if t.upper() == "TBNK" else None), \
             patch.object(wire_base, "fetch_rss", return_value=entries):
            return SEC8KRecentAdapter().poll(list(tickers), since=PAST)

    def test_matches_only_universe_banks(self):
        evs = self._poll([
            _entry("0000123456", "Item 2.02: Results of Operations", "0000123456-26-000001"),
            # different CIK — not a tracked bank — must be ignored
            _entry("0000999999", "Item 8.01: Other Events", "0000999999-26-000002", title="Random Co"),
        ])
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].ticker, "TBNK")
        self.assertEqual(evs[0].external_id, "0000123456-26-000001")
        self.assertIn("Earnings", evs[0].headline)
        self.assertEqual(evs[0].raw["items"], ["2.02"])

    def test_pure_9_01_skipped(self):
        evs = self._poll([
            _entry("0000123456", "Item 9.01: Financial Statements and Exhibits", "0000123456-26-000003"),
        ])
        self.assertEqual(evs, [], "exhibits-only feed entry must be dropped")

    def test_dedup_on_accession(self):
        evs = self._poll([
            _entry("0000123456", "Item 5.02: Departure of Officers", "0000123456-26-000004"),
            _entry("0000123456", "Item 5.02: Departure of Officers", "0000123456-26-000004"),
        ])
        self.assertEqual(len(evs), 1)

    def test_same_source_and_id_as_per_cik_adapter(self):
        # Both adapters use source 'sec_8k' + accession id, so they dedup in store.
        evs = self._poll([_entry("0000123456", "Item 1.01: Material Agreement",
                                 "0000123456-26-000005")])
        self.assertEqual(evs[0].source, "sec_8k")


class TestCleanSummary(unittest.TestCase):
    """_clean_summary strips the markdown-title / label noise Haiku leaks and
    drops content-free refusals (the exact junk seen in the live feed)."""

    def test_strips_leading_markdown_header(self):
        self.assertEqual(
            _clean_summary("# CFFI 8-K Summary\n\nC&F Financial Corp disclosed an officer change."),
            "C&F Financial Corp disclosed an officer change.")

    def test_header_only_becomes_empty(self):
        self.assertEqual(_clean_summary("# Summary for Bank Analyst"), "")

    def test_strips_summary_label(self):
        self.assertEqual(_clean_summary("Summary: PNC raised its dividend to $1.60."),
                         "PNC raised its dividend to $1.60.")

    def test_none_sentinel_dropped(self):
        self.assertEqual(_clean_summary("NONE"), "")

    def test_refusal_phrasings_dropped(self):
        # Every refusal variant seen in the live feed must be dropped.
        for r in [
            "Unable to summarize substantive content — the provided text contains only SEC filing metadata.",
            "Unable to provide specific summary — the filing document itself is not included in the text.",
            "Unable to provide a substantive summary—the 8-K filing document is not included in the provided text.",
            "I cannot provide a substantive summary because the SEC filing text itself is not included.",
            "The provided text contains only SEC filing metadata, not the actual 8-K content.",
        ]:
            with self.subTest(r=r):
                self.assertEqual(_clean_summary(r), "")

    def test_legit_summary_with_unable_survives(self):
        # A real summary that happens to say a COMPANY was "unable to" must NOT
        # be mistaken for a refusal.
        s = "The bank disclosed it was unable to meet the minimum capital ratio in Q2."
        self.assertEqual(_clean_summary(s), s)

    def test_clean_summary_passes_through(self):
        s = "Truist named a new CEO effective July 1, 2026."
        self.assertEqual(_clean_summary(s), s)


class TestResolve8KDocUrl(unittest.TestCase):
    """An index-page URL (recent-feed adapter) must resolve to the EX-99.1 doc so
    the summarizer gets real body text instead of a document list."""

    IDX = "https://www.sec.gov/Archives/edgar/data/713676/000071367626000050/0000713676-26-000050-index.htm"

    def test_index_resolves_to_exhibit_when_present(self):
        ex = "https://www.sec.gov/Archives/edgar/data/713676/000071367626000050/ex-991.htm"
        with patch.object(filing_summarizer, "find_press_release_url", return_value=ex):
            self.assertEqual(_resolve_8k_doc_url(self.IDX), ex)

    def test_index_unchanged_when_no_exhibit(self):
        # Officer-change/vote 8-Ks have no EX-99.1 → keep the index (summary then
        # drops, the item headline stands).
        with patch.object(filing_summarizer, "find_press_release_url", return_value=None):
            self.assertEqual(_resolve_8k_doc_url(self.IDX), self.IDX)

    def test_primary_doc_url_also_resolves(self):
        # Per-CIK adapter stores the primary-doc URL (the cover page that only
        # *references* Exhibit 99.1) — it must ALSO resolve to the EX-99.1, with
        # the dashed accession reconstructed from the 18-digit directory.
        doc = "https://www.sec.gov/Archives/edgar/data/713676/000071367626000050/pnc8k.htm"
        ex = "https://www.sec.gov/Archives/edgar/data/713676/000071367626000050/release.htm"
        with patch.object(filing_summarizer, "find_press_release_url",
                          return_value=ex) as m:
            self.assertEqual(_resolve_8k_doc_url(doc), ex)
            m.assert_called_once_with(713676, "0000713676-26-000050")

    def test_primary_doc_url_unchanged_when_no_exhibit(self):
        # Bare officer-change/vote 8-K: no EX-99.1 → keep the primary doc (its
        # cover narrative still summarizes, or drops to the item headline).
        doc = "https://www.sec.gov/Archives/edgar/data/713676/000071367626000050/pnc8k.htm"
        with patch.object(filing_summarizer, "find_press_release_url", return_value=None):
            self.assertEqual(_resolve_8k_doc_url(doc), doc)

    def test_non_archive_url_passes_through(self):
        # browse-edgar fallback / non-EDGAR URLs must not be touched.
        u = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=713676&type=8-K"
        with patch.object(filing_summarizer, "find_press_release_url",
                          side_effect=AssertionError("should not be called")):
            self.assertEqual(_resolve_8k_doc_url(u), u)


class TestPressReleaseFromIndexHtml(unittest.TestCase):
    """The EX-99.1 designation lives in the index table's Type column, NOT the
    filename — modern filers name the exhibit arbitrarily (regression: PNC's
    'a2026_0622xrlsxpncxfirst.htm' was missed by the old filename regex, so
    8-Ks never summarized)."""

    # Real PNC 8-K index table shape: primary doc wrapped in the iXBRL viewer,
    # then an EX-99.1 row whose filename contains neither "ex" nor "99".
    HTML = """
      <table class="tableFile" summary="Document Format Files">
        <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th></tr>
        <tr><td>1</td><td>8-K</td>
            <td><a href="/ix?doc=/Archives/edgar/data/713676/000162828026044499/pnc-20260622.htm">pnc-20260622.htm</a></td>
            <td>8-K</td></tr>
        <tr class="evenRow"><td>2</td><td>EX-99.1</td>
            <td><a href="/Archives/edgar/data/713676/000162828026044499/a2026_0622xrlsxpncxfirst.htm">a2026_0622xrlsxpncxfirst.htm</a></td>
            <td>EX-99.1</td></tr>
      </table>"""

    def test_matches_ex991_by_type_not_filename(self):
        url = filing_summarizer._press_release_from_index_html(self.HTML)
        self.assertEqual(
            url,
            "https://www.sec.gov/Archives/edgar/data/713676/000162828026044499/a2026_0622xrlsxpncxfirst.htm",
        )

    def test_unwraps_ixbrl_viewer_prefix(self):
        # The primary 8-K doc is wrapped in '/ix?doc=' — if it were ever the hit,
        # the viewer wrapper must be stripped so we fetch the raw document.
        out = filing_summarizer._abs_edgar_url(
            "/ix?doc=/Archives/edgar/data/713676/000162828026044499/pnc-20260622.htm")
        self.assertEqual(
            out,
            "https://www.sec.gov/Archives/edgar/data/713676/000162828026044499/pnc-20260622.htm",
        )

    def test_no_ex99_row_returns_none(self):
        # Bare officer-change/vote 8-K (only the primary doc) → no exhibit.
        html = """
          <table class="tableFile">
            <tr><td>1</td><td>8-K</td>
                <td><a href="/Archives/edgar/data/1/2/x.htm">x.htm</a></td>
                <td>8-K</td></tr>
          </table>"""
        self.assertIsNone(filing_summarizer._press_release_from_index_html(html))

    def test_falls_back_to_any_ex99(self):
        # EX-99 (no .1) still counts as the press release exhibit.
        html = """
          <table class="tableFile">
            <tr><td>1</td><td>EX-99</td>
                <td><a href="/Archives/edgar/data/1/2/release.htm">release.htm</a></td>
                <td>EX-99</td></tr>
          </table>"""
        self.assertEqual(
            filing_summarizer._press_release_from_index_html(html),
            "https://www.sec.gov/Archives/edgar/data/1/2/release.htm",
        )


class TestSummarizerPriority(unittest.TestCase):
    def test_high_signal_items_flagged(self):
        for item in ("1.01", "2.01", "8.01", "5.02", "4.02", "2.06", "5.01"):
            with self.subTest(item=item):
                self.assertTrue(_is_high_signal_8k(json.dumps({"items": [item, "9.01"]})))

    def test_routine_items_not_flagged(self):
        self.assertFalse(_is_high_signal_8k(json.dumps({"items": ["2.02", "9.01"]})))
        self.assertFalse(_is_high_signal_8k(json.dumps({"items": ["7.01"]})))

    def test_garbled_raw_json_is_safe(self):
        self.assertFalse(_is_high_signal_8k(None))
        self.assertFalse(_is_high_signal_8k("not json"))
        self.assertFalse(_is_high_signal_8k(json.dumps({})))


if __name__ == "__main__":
    unittest.main()
