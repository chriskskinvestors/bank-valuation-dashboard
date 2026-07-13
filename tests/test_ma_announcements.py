"""
Tests for data/ma_announcements.py — announcement 8-K resolution for the
Detailed M&A History table (docs/SNL-BUILD-PLAN.md §14). All HTTP mocked.
Pins (phrasings taken from the live-verified Banner/Skagit and
Columbia/Umpqua press releases, 2026-07-13):

  • candidate ordering (oldest accession first) and EX-99 preference
  • announcement vs completion classification — a completion PR citing the
    "previously announced definitive agreement" is REJECTED, while
    prospective "upon completion of the transaction" boilerplate in an
    announcement is not
  • both-party name guards (target phrase + acquirer brand token)
  • strict stated-value extraction: single value in raw dollars; distinct
    candidate amounts -> None; termination fees never match
  • pre-2001 completion (EFTS floor) -> (None, True) with zero network
  • EFTS failure -> (None, False); unreadable candidate doc -> ok=False
    unless a later candidate positively matches
"""
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# Full house stub (see tests/test_audit_regressions.py): a minimal stub that
# wins the sys.modules setdefault race would break later suites needing
# st.fragment / streamlit.components.v1 at module load (the stub-rot trap,
# memory 2026-07-02).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
_st.fragment = _st.cache_data
_st_components = types.ModuleType("streamlit.components")
_st_components_v1 = types.ModuleType("streamlit.components.v1")
_st_components_v1.html = lambda *a, **k: None
_st_components.v1 = _st_components_v1
_st.components = _st_components
sys.modules.setdefault("streamlit", _st)
sys.modules.setdefault("streamlit.components", _st_components)
sys.modules.setdefault("streamlit.components.v1", _st_components_v1)

ANNOUNCE_PR = """
<html><body><p>Banner Financial and Skagit Bancorp, Inc., the parent company
of Skagit Bank, today jointly announced the signing of a definitive agreement
under which Banner Bank will acquire Skagit Bank in a transaction valued at
approximately $191.1 million. Upon completion of the transaction, the combined
company will have approximately $11 billion in assets.</p></body></html>
"""

COMPLETION_PR = """
<html><body><p>Banner Financial today announced the completion of its
acquisition of Skagit Bank, pursuant to the previously announced definitive
agreement and plan of merger.</p></body></html>
"""


def _hit(adsh, file_date, doc, file_type="EX-99.1", cik="0000946673",
         items=None):
    return {"_id": f"{adsh}:{doc}",
            "_source": {"adsh": adsh, "file_date": file_date, "ciks": [cik],
                        "file_type": file_type,
                        **({"items": items} if items is not None else {})}}


def _efts_resp(hits):
    r = MagicMock()
    r.json.return_value = {"hits": {"hits": hits}}
    r.raise_for_status = MagicMock()
    return r


def _doc_resp(body):
    r = MagicMock()
    r.text = body
    r.raise_for_status = MagicMock()
    return r


def _wire(efts_hits, docs, efts_fail=False, doc_fail=()):
    """requests.get side_effect: EFTS query then archive doc fetches.
    docs: {doc_filename: html}; doc_fail: filenames that raise."""
    def side_effect(url, params=None, headers=None, timeout=30):
        if "efts.sec.gov" in url:
            if efts_fail:
                raise Exception("efts down")
            return _efts_resp(efts_hits)
        fn = url.rsplit("/", 1)[-1]
        if fn in doc_fail:
            raise Exception("doc down")
        return _doc_resp(docs[fn])
    return side_effect


class TestHelpers(unittest.TestCase):

    def test_query_name_strips_charter_suffixes(self):
        from data.ma_announcements import query_name
        self.assertEqual(query_name("Pacific Premier Bank, National Association"),
                         "Pacific Premier Bank")
        self.assertEqual(query_name("First Foundation Bank N.A."),
                         "First Foundation Bank")
        self.assertEqual(query_name("Skagit Bank"), "Skagit Bank")

    def test_brand_token_skips_generic_words(self):
        from data.ma_announcements import brand_token
        self.assertEqual(brand_token("Umpqua Bank"), "umpqua")
        self.assertEqual(brand_token("First National Bank"), None)
        self.assertEqual(brand_token("South Umpqua Bank"), "south")

    def test_stated_value_single_and_units(self):
        from data.ma_announcements import extract_stated_value
        self.assertEqual(
            extract_stated_value("a transaction valued at approximately "
                                 "$191.1 million in cash"),
            191_100_000)
        self.assertEqual(
            extract_stated_value("aggregate transaction value of $5.2 billion"),
            5_200_000_000)

    def test_stated_value_ambiguous_or_absent_is_none(self):
        from data.ma_announcements import extract_stated_value
        self.assertIsNone(extract_stated_value(
            "valued at approximately $191.1 million ... later restated as "
            "valued at approximately $200 million"))
        self.assertIsNone(extract_stated_value(
            "a termination fee of $25 million and exchange ratio of 0.5958"))
        # The same figure repeated is one value, not ambiguity.
        self.assertEqual(extract_stated_value(
            "valued at approximately $191.1 million ... again valued at "
            "approximately $191.1 million"), 191_100_000)


class TestResolveAnnouncement(unittest.TestCase):

    @patch("data.ma_announcements.time.sleep", lambda *_: None)
    @patch("data.ma_announcements.requests.get")
    def test_happy_path_skips_completion_pr(self, mock_get):
        from data.ma_announcements import resolve_announcement
        # Completion PR is a LATER accession; announcement (older) must win —
        # and even if the completion doc were scanned it must be rejected.
        mock_get.side_effect = _wire(
            [_hit("0001-18-2", "2018-11-01", "complete.htm"),
             _hit("0001-18-1", "2018-07-26", "announce.htm")],
            {"announce.htm": ANNOUNCE_PR, "complete.htm": COMPLETION_PR})
        r, ok = resolve_announcement("Skagit Bank", "Banner Bank", "2018-11-01")
        self.assertTrue(ok)
        self.assertEqual(r["announce_date"], "2018-07-26")
        self.assertEqual(r["value_usd"], 191_100_000)
        self.assertEqual(r["value_basis"], "stated")
        self.assertEqual(r["accession"], "0001-18-1")

    @patch("data.ma_announcements.time.sleep", lambda *_: None)
    @patch("data.ma_announcements.requests.get")
    def test_completion_only_hits_return_none(self, mock_get):
        from data.ma_announcements import resolve_announcement
        mock_get.side_effect = _wire(
            [_hit("0001-18-2", "2018-11-01", "complete.htm")],
            {"complete.htm": COMPLETION_PR})
        r, ok = resolve_announcement("Skagit Bank", "Banner Bank", "2018-11-01")
        self.assertIsNone(r)
        self.assertTrue(ok)

    @patch("data.ma_announcements.time.sleep", lambda *_: None)
    @patch("data.ma_announcements.requests.get")
    def test_acquirer_token_guard(self, mock_get):
        # A PR about a DIFFERENT buyer of the same-named target is rejected.
        from data.ma_announcements import resolve_announcement
        mock_get.side_effect = _wire(
            [_hit("0001-18-1", "2018-07-26", "announce.htm")],
            {"announce.htm": ANNOUNCE_PR})
        r, ok = resolve_announcement("Skagit Bank", "Glacier Bank", "2018-11-01")
        self.assertIsNone(r)
        self.assertTrue(ok)

    def test_pre_efts_floor_no_network(self):
        from data.ma_announcements import resolve_announcement
        with patch("data.ma_announcements.requests.get") as mock_get:
            r, ok = resolve_announcement("Whatcom State Bank", "Banner Bank",
                                         "1999-01-04")
        self.assertIsNone(r)
        self.assertTrue(ok)
        mock_get.assert_not_called()

    @patch("data.ma_announcements.time.sleep", lambda *_: None)
    @patch("data.ma_announcements.requests.get")
    def test_efts_failure_is_not_cacheable(self, mock_get):
        from data.ma_announcements import resolve_announcement
        mock_get.side_effect = _wire([], {}, efts_fail=True)
        r, ok = resolve_announcement("Skagit Bank", "Banner Bank", "2018-11-01")
        self.assertIsNone(r)
        self.assertFalse(ok)

    @patch("data.ma_announcements.time.sleep", lambda *_: None)
    @patch("data.ma_announcements.requests.get")
    def test_unreadable_candidate_makes_miss_uncacheable(self, mock_get):
        from data.ma_announcements import resolve_announcement
        mock_get.side_effect = _wire(
            [_hit("0001-18-1", "2018-07-26", "announce.htm")],
            {}, doc_fail=("announce.htm",))
        r, ok = resolve_announcement("Skagit Bank", "Banner Bank", "2018-11-01")
        self.assertIsNone(r)
        self.assertFalse(ok)

    @patch("data.ma_announcements.time.sleep", lambda *_: None)
    @patch("data.ma_announcements.requests.get")
    def test_non_announce_items_skipped_without_fetch(self, mock_get):
        # A routine earnings 8-K (items 2.02/9.01) mentioning the target must
        # not burn the candidate budget — no document fetch at all for it.
        from data.ma_announcements import resolve_announcement
        mock_get.side_effect = _wire(
            [_hit("0001-18-0", "2018-01-25", "earnings.htm",
                  items=["2.02", "9.01"]),
             _hit("0001-18-1", "2018-07-26", "announce.htm",
                  items=["1.01", "9.01"])],
            {"announce.htm": ANNOUNCE_PR})   # earnings.htm absent on purpose
        r, ok = resolve_announcement("Skagit Bank", "Banner Bank", "2018-11-01")
        self.assertTrue(ok and r)
        self.assertEqual(r["announce_date"], "2018-07-26")

    @patch("data.ma_announcements.time.sleep", lambda *_: None)
    @patch("data.ma_announcements.requests.get")
    def test_legacy_item_codes_stay_eligible(self, mock_get):
        # Pre-2004 8-Ks carry single-digit legacy items ("5") — the modern
        # items gate must not drop them (live regression: the 2001
        # Independent Financial Network cluster lost its announce date).
        from data.ma_announcements import resolve_announcement
        mock_get.side_effect = _wire(
            [_hit("0001-01-1", "2001-06-22", "announce.htm", items=["5"])],
            {"announce.htm": ANNOUNCE_PR})
        r, ok = resolve_announcement("Skagit Bank", "Banner Bank", "2001-12-31")
        self.assertTrue(ok and r)
        self.assertEqual(r["announce_date"], "2001-06-22")

    @patch("data.ma_announcements.time.sleep", lambda *_: None)
    @patch("data.ma_announcements.requests.get")
    def test_startdt_clamped_to_efts_floor(self, mock_get):
        # Completion just after the floor: window start must be clamped —
        # EFTS 500s on pre-coverage startdt (seen live on Security Bank).
        from data.ma_announcements import resolve_announcement
        mock_get.side_effect = _wire([], {})
        r, ok = resolve_announcement("Security Bank", "Umpqua Bank",
                                     "2001-12-31")
        self.assertIsNone(r)
        self.assertTrue(ok)
        params = mock_get.call_args[1].get("params") or mock_get.call_args[0][1]
        self.assertEqual(params["startdt"], "2001-04-01")

    @patch("data.ma_announcements.time.sleep", lambda *_: None)
    @patch("data.ma_announcements.requests.get")
    def test_ex99_preferred_within_accession(self, mock_get):
        from data.ma_announcements import resolve_announcement
        mock_get.side_effect = _wire(
            [_hit("0001-18-1", "2018-07-26", "body.htm", file_type="8-K"),
             _hit("0001-18-1", "2018-07-26", "announce.htm", file_type="EX-99.1")],
            {"announce.htm": ANNOUNCE_PR, "body.htm": COMPLETION_PR})
        r, ok = resolve_announcement("Skagit Bank", "Banner Bank", "2018-11-01")
        self.assertTrue(ok and r)
        self.assertTrue(r["url"].endswith("announce.htm"))


if __name__ == "__main__":
    unittest.main()
