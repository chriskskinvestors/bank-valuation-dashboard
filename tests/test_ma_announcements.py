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
  • HTTP 404 on an archive document is a PERMANENT gap -> cacheable
    (None, True), while a 503 stays an uncacheable fetch failure — the
    nightly job must not refetch immutable-archive 404s forever
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
         items=None, display_names=None):
    return {"_id": f"{adsh}:{doc}",
            "_source": {"adsh": adsh, "file_date": file_date, "ciks": [cik],
                        "file_type": file_type,
                        **({"items": items} if items is not None else {}),
                        **({"display_names": display_names}
                           if display_names is not None else {})}}


# Columbia/Umpqua-style all-stock MOE announcement: exchange ratio, ticker
# mentions, NO stated dollar value (live-verified phrasing, 2026-07-13).
MOE_PR = """
<html><body><p>Columbia Banking System, Inc. (NASDAQ: COLB) and Umpqua
Holdings Corporation (NASDAQ: UMPQ), parent company of Columbia State Bank
and Umpqua Bank, today announced a definitive agreement under which the
companies will combine. Umpqua shareholders will receive 0.5958 of a share
of Columbia stock for each Umpqua share they own.</p></body></html>
"""


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


def _http_error(status):
    """requests.HTTPError as raise_for_status() raises it, with the
    response's status_code attached (what is_http_404 inspects)."""
    import requests
    resp = MagicMock()
    resp.status_code = status
    return requests.HTTPError(f"{status} error", response=resp)


def _wire(efts_hits, docs, efts_fail=False, doc_fail=(), indexes=None,
          doc_http=None):
    """requests.get side_effect: EFTS query, filing-index pages (URLs ending
    '/'), then archive doc fetches. docs: {doc_filename: html}; doc_fail:
    filenames that raise; indexes: {accession_nodash: [doc filenames]};
    doc_http: {doc_filename: HTTP status} raising that HTTPError."""
    indexes = indexes or {}

    def side_effect(url, params=None, headers=None, timeout=30):
        if "efts.sec.gov" in url:
            if efts_fail:
                raise Exception("efts down")
            return _efts_resp(efts_hits)
        if url.endswith("/"):
            acc = url.rstrip("/").rsplit("/", 1)[-1]
            links = "".join(f'<a href="/x/{n}">{n}</a>'
                            for n in indexes.get(acc, []))
            return _doc_resp(f"<html>{links}</html>")
        fn = url.rsplit("/", 1)[-1]
        if fn in doc_fail:
            raise Exception("doc down")
        if fn in (doc_http or {}):
            raise _http_error(doc_http[fn])
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


class TestComputedStockValue(unittest.TestCase):

    def test_ratio_extraction_both_forms_and_direction(self):
        from data.ma_announcements import extract_exchange_ratio
        r = extract_exchange_ratio(
            "shareholders will receive 0.5958 of a share of Columbia stock "
            "for each Umpqua share they own")
        self.assertEqual(r[0], 0.5958)
        self.assertIn("Columbia", r[1])   # stock received = acquirer side
        self.assertIn("Umpqua", r[2])     # per-share side = target
        r = extract_exchange_ratio(
            "each share of Umpqua common stock will be converted into the "
            "right to receive 0.5958 shares of Columbia common stock")
        self.assertEqual((round(r[0], 4), "Umpqua" in r[2], "Columbia" in r[1]),
                         (0.5958, True, True))
        # Two DISTINCT ratios (collared deal) -> None, never a guess.
        self.assertIsNone(extract_exchange_ratio(
            "receive 0.5958 of a share of Columbia stock for each Umpqua "
            "share ... receive 0.6100 of a share of Columbia stock for each "
            "Umpqua share"))

    @patch("data.fmp_client.get_history")
    @patch("data.fmp_client._has_key", return_value=True)
    @patch("data.sec_client.fetch_company_facts_ok")
    def test_computed_value_hand_math(self, mock_facts, _hk, mock_hist):
        # 200,000,000 shares x 0.5958 x $40.00 = $4,766,400,000 (hand).
        import pandas as pd
        from data.ma_announcements import compute_stock_value
        mock_facts.return_value = ({"facts": {"dei": {
            "EntityCommonStockSharesOutstanding": {"units": {"shares": [
                {"end": "2021-09-30", "filed": "2021-10-01",
                 "val": 200_000_000}]}}}}}, True)
        mock_hist.return_value = pd.DataFrame(
            {"date": ["2021-10-08", "2021-10-11", "2021-10-12"],
             "close": [41.0, 40.0, 38.0]})
        text = ("Columbia Banking System, Inc. (NASDAQ: COLB) and Umpqua "
                "Holdings Corporation (NASDAQ: UMPQ) ... will receive 0.5958 "
                "of a share of Columbia stock for each Umpqua share they own")
        comp, ok = compute_stock_value(text, "2021-10-12",
                                       {"COLB": 887343, "UMPQ": 1077771})
        self.assertTrue(ok)
        self.assertEqual(comp["value_usd"], 4_766_400_000)
        # Prior close (10-11, $40.00) used - NOT announce-day (10-12, $38).
        self.assertIn("$40.00 (2021-10-11)", comp["value_note"])
        self.assertIn("200,000,000 UMPQ shares", comp["value_note"])
        mock_facts.assert_called_once_with(1077771)   # target CIK, not COLB

    @patch("data.fmp_client.get_history")
    @patch("data.fmp_client._has_key", return_value=True)
    @patch("data.sec_client.fetch_company_facts_ok")
    def test_stale_share_count_is_na(self, mock_facts, _hk, mock_hist):
        from data.ma_announcements import compute_stock_value
        mock_facts.return_value = ({"facts": {"dei": {
            "EntityCommonStockSharesOutstanding": {"units": {"shares": [
                {"end": "2020-06-30", "filed": "2020-08-01",
                 "val": 200_000_000}]}}}}}, True)
        text = ("Columbia Banking System, Inc. (NASDAQ: COLB) and Umpqua "
                "Holdings Corporation (NASDAQ: UMPQ) ... receive 0.5958 of a "
                "share of Columbia stock for each Umpqua share")
        comp, ok = compute_stock_value(text, "2021-10-12", {"UMPQ": 1077771})
        self.assertIsNone(comp)
        self.assertTrue(ok)          # cacheable n/a, not a fetch failure
        mock_hist.assert_not_called()

    @patch("data.fmp_client._has_key", return_value=False)
    @patch("data.sec_client.fetch_company_facts_ok")
    def test_no_fmp_key_is_uncacheable(self, mock_facts, _hk):
        from data.ma_announcements import compute_stock_value
        mock_facts.return_value = ({"facts": {"dei": {
            "EntityCommonStockSharesOutstanding": {"units": {"shares": [
                {"end": "2021-09-30", "filed": "2021-10-01",
                 "val": 200_000_000}]}}}}}, True)
        text = ("Columbia Banking System, Inc. (NASDAQ: COLB) and Umpqua "
                "Holdings Corporation (NASDAQ: UMPQ) ... receive 0.5958 of a "
                "share of Columbia stock for each Umpqua share")
        comp, ok = compute_stock_value(text, "2021-10-12", {"UMPQ": 1077771})
        self.assertIsNone(comp)
        self.assertFalse(ok)

    @patch("data.fmp_client.get_history")
    @patch("data.fmp_client._has_key", return_value=True)
    @patch("data.sec_client.fetch_company_facts_ok")
    def test_target_companyfacts_404_is_cacheable(self, mock_facts, _hk, mock_hist):
        # Permanent companyfacts 404 on the target CIK (non-reporting entity):
        # no shares -> value n/a, but ok=True so the deal caches instead of
        # re-fetching the dead CIK every nightly run.
        from data.ma_announcements import compute_stock_value
        mock_facts.return_value = ({}, True)     # 404 -> empty facts, permanent
        text = ("Columbia Banking System, Inc. (NASDAQ: COLB) and Umpqua "
                "Holdings Corporation (NASDAQ: UMPQ) ... receive 0.5958 of a "
                "share of Columbia stock for each Umpqua share")
        comp, ok = compute_stock_value(text, "2021-10-12", {"UMPQ": 1077771})
        self.assertIsNone(comp)
        self.assertTrue(ok)              # cacheable gap, NOT a retry
        mock_hist.assert_not_called()

    @patch("data.fmp_client.get_history")
    @patch("data.fmp_client._has_key", return_value=True)
    @patch("data.sec_client.fetch_company_facts_ok")
    def test_target_companyfacts_transient_is_uncacheable(self, mock_facts, _hk, mock_hist):
        # Transient companyfacts failure (5xx/timeout) -> ok=False so the deal
        # is NOT frozen; retried next run.
        from data.ma_announcements import compute_stock_value
        mock_facts.return_value = ({}, False)    # transient -> uncacheable
        text = ("Columbia Banking System, Inc. (NASDAQ: COLB) and Umpqua "
                "Holdings Corporation (NASDAQ: UMPQ) ... receive 0.5958 of a "
                "share of Columbia stock for each Umpqua share")
        comp, ok = compute_stock_value(text, "2021-10-12", {"UMPQ": 1077771})
        self.assertIsNone(comp)
        self.assertFalse(ok)
        mock_hist.assert_not_called()

    def test_no_ratio_is_cacheable_na(self):
        from data.ma_announcements import compute_stock_value
        comp, ok = compute_stock_value("all cash transaction", "2021-10-12", {})
        self.assertIsNone(comp)
        self.assertTrue(ok)


class TestResolveAnnouncement(unittest.TestCase):

    def setUp(self):
        # Isolate from the real cache.db: _fetch_doc_text's document-level
        # negative cache (c) calls cache.get/put. get->None keeps every test
        # hitting its mocked network; put is a no-op so no dev-cache rows are
        # written and re-runs stay deterministic. (Dedicated negative-cache
        # behavior is pinned in TestDocNegativeCache.)
        for tgt in ("data.cache.get", "data.cache.put"):
            p = patch(tgt, **({"return_value": None} if "get" in tgt else {}))
            p.start()
            self.addCleanup(p.stop)

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
    def test_404_candidate_is_cacheable_na(self, mock_get):
        # An archive 404 is PERMANENT (2001-vintage accessions missing the
        # document, e.g. 829281/000090951801000405/0001.txt) — the miss must
        # cache, or the nightly job refetches the same 404 forever.
        from data.ma_announcements import resolve_announcement
        mock_get.side_effect = _wire(
            [_hit("0001-01-1", "2001-06-22", "0001.txt", items=["5"])],
            {}, doc_http={"0001.txt": 404})
        r, ok = resolve_announcement("Skagit Bank", "Banner Bank", "2001-12-31")
        self.assertIsNone(r)
        self.assertTrue(ok)

    @patch("data.ma_announcements.time.sleep", lambda *_: None)
    @patch("data.ma_announcements.requests.get")
    def test_503_candidate_stays_uncacheable(self, mock_get):
        # A transient outage must NOT freeze the miss into the cache.
        from data.ma_announcements import resolve_announcement
        mock_get.side_effect = _wire(
            [_hit("0001-18-1", "2018-07-26", "announce.htm")],
            {}, doc_http={"announce.htm": 503})
        r, ok = resolve_announcement("Skagit Bank", "Banner Bank", "2018-11-01")
        self.assertIsNone(r)
        self.assertFalse(ok)

    @patch("data.ma_announcements.time.sleep", lambda *_: None)
    @patch("data.ma_announcements.requests.get")
    def test_404_candidate_does_not_block_later_match(self, mock_get):
        # A 404'd older candidate is skipped; a later readable announcement
        # still resolves with ok=True.
        from data.ma_announcements import resolve_announcement
        mock_get.side_effect = _wire(
            [_hit("0001-18-0", "2018-07-01", "gone.htm"),
             _hit("0001-18-1", "2018-07-26", "announce.htm")],
            {"announce.htm": ANNOUNCE_PR}, doc_http={"gone.htm": 404})
        r, ok = resolve_announcement("Skagit Bank", "Banner Bank", "2018-11-01")
        self.assertTrue(ok and r)
        self.assertEqual(r["announce_date"], "2018-07-26")

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

    @patch("data.fmp_client.get_history")
    @patch("data.fmp_client._has_key", return_value=True)
    @patch("data.sec_client.fetch_company_facts_ok")
    @patch("data.ma_announcements.time.sleep", lambda *_: None)
    @patch("data.ma_announcements.requests.get")
    def test_moe_gets_computed_value(self, mock_get, mock_facts, _hk, mock_hist):
        # Ratio-only MOE: stated value absent -> computed, with the target
        # CIK resolved from the EFTS display names (delisted UMPQ).
        import pandas as pd
        from data.ma_announcements import resolve_announcement
        mock_get.side_effect = _wire(
            [_hit("0001-21-1", "2021-10-12", "moe.htm",
                  cik="0001077771",
                  # REAL delisted shape: no "(UMPQ)" ticker in the display
                  # name — exercises the name-brand-token CIK fallback.
                  display_names=["UMPQUA HOLDINGS CORP  (CIK 0001077771)"])],
            {"moe.htm": MOE_PR})
        mock_facts.return_value = ({"facts": {"dei": {
            "EntityCommonStockSharesOutstanding": {"units": {"shares": [
                {"end": "2021-09-30", "filed": "2021-10-01",
                 "val": 200_000_000}]}}}}}, True)
        mock_hist.return_value = pd.DataFrame(
            {"date": ["2021-10-11"], "close": [40.0]})
        r, ok = resolve_announcement("Columbia State Bank", "Umpqua Bank",
                                     "2023-03-01")
        self.assertTrue(ok)
        self.assertEqual(r["announce_date"], "2021-10-12")
        self.assertEqual(r["value_usd"], 4_766_400_000)
        self.assertEqual(r["value_basis"], "computed")
        # Deal comps pair the value with the RATIO's target side — the
        # priced entity (UMPQ), not the FDIC bank-level counterparty.
        self.assertEqual(r["target_cik"], 1077771)
        mock_facts.assert_called_once_with(1077771)

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


# ── Terminated-deal sweep fixtures (FHN/TD-shaped, live-verified 2026-07-13) ──

TD_ANNOUNCE_PR = """
<html><body><p>First Horizon Corporation (NYSE: FHN) and TD Bank Group
(TSX and NYSE: TD) today announced that they have signed a definitive
agreement for TD to acquire First Horizon in an all-cash transaction valued
at US$13.4 billion.</p></body></html>
"""

TD_TERMINATION_PR = """
<html><body><p>TD Bank Group (TSX and NYSE: TD) and First Horizon
Corporation (NYSE: FHN) today announced a mutual agreement to terminate the
Agreement and Plan of Merger.</p></body></html>
"""

TD_EXTENSION_8K = """
<html><body><p>First Horizon and TD agreed to extend the previously
announced Agreement and Plan of Merger, the definitive agreement under
which TD will acquire First Horizon.</p></body></html>
"""


class TestFindTerminatedDeals(unittest.TestCase):

    FHN = 36966

    def setUp(self):
        # Same real-cache isolation as TestResolveAnnouncement (find_terminated
        # _deals -> _accession_text -> _fetch_doc_text hits the negative cache).
        for tgt in ("data.cache.get", "data.cache.put"):
            p = patch(tgt, **({"return_value": None} if "get" in tgt else {}))
            p.start()
            self.addCleanup(p.stop)

    def _hits(self):
        # Announcement (7.01, live FHN shape), a LATER extension 8-K (8.01),
        # and the termination (1.02). Bodies matched the phrase; the PRs are
        # index-discovered exhibits.
        return [
            _hit("0001-22-1", "2022-02-28", "ann_body.htm", file_type="8-K",
                 cik="0000036966", items=["7.01", "9.01"]),
            _hit("0001-23-1", "2023-02-10", "ext_body.htm", file_type="8-K",
                 cik="0000036966", items=["8.01", "9.01"]),
            _hit("0001-23-2", "2023-05-04", "term_body.htm", file_type="8-K",
                 cik="0000036966", items=["1.02", "8.01", "9.01"]),
        ]

    def _docs(self):
        return {"ann_body.htm": "<p>entry into an Agreement and Plan of "
                                "Merger with The Toronto-Dominion Bank</p>",
                "ann_ex99.htm": TD_ANNOUNCE_PR,
                "ext_body.htm": TD_EXTENSION_8K,
                "term_body.htm": "<p>terminated the Agreement and Plan of "
                                 "Merger with TD</p>",
                "term_ex99.htm": TD_TERMINATION_PR}

    def _indexes(self):
        return {"0001221": ["ann_body.htm", "ann_ex99.htm"],
                "0001231": ["ext_body.htm"],
                "0001232": ["term_body.htm", "term_ex99.htm"]}

    @patch("data.ma_announcements.time.sleep", lambda *_: None)
    @patch("data.ma_announcements.requests.get")
    def test_happy_path_links_original_announcement(self, mock_get):
        # The extension 8-K also cites the merger agreement — EARLIEST-first
        # back-linking must pin the ORIGINAL 2022-02-28 announcement, and the
        # stated US$13.4B value comes from its index-discovered EX-99 PR.
        from data.ma_announcements import find_terminated_deals
        mock_get.side_effect = _wire(self._hits(), self._docs(),
                                     indexes=self._indexes())
        deals, ok = find_terminated_deals(self.FHN, "First Horizon Bank")
        self.assertTrue(ok)
        self.assertEqual(len(deals), 1)
        d = deals[0]
        self.assertEqual(d["termination_date"], "2023-05-04")
        self.assertEqual(d["announce_date"], "2022-02-28")
        self.assertEqual(d["counterparty_name"], "TD Bank Group")
        self.assertEqual(d["value_usd"], 13_400_000_000)
        self.assertEqual(d["value_basis"], "stated")
        self.assertIsNone(d["direction"])  # cash deal — honest n/a

    @patch("data.ma_announcements.time.sleep", lambda *_: None)
    @patch("data.ma_announcements.requests.get")
    def test_unlinkable_termination_dropped(self, mock_get):
        # Termination with NO prior announcement group -> dropped, never a
        # counterparty guess.
        from data.ma_announcements import find_terminated_deals
        hits = [h for h in self._hits() if "1.02" in
                (h["_source"].get("items") or [])]
        mock_get.side_effect = _wire(hits, self._docs(),
                                     indexes=self._indexes())
        deals, ok = find_terminated_deals(self.FHN, "First Horizon Bank")
        self.assertEqual(deals, [])
        self.assertTrue(ok)

    @patch("data.ma_announcements.time.sleep", lambda *_: None)
    @patch("data.ma_announcements.requests.get")
    def test_sweep_fetch_failure_uncacheable(self, mock_get):
        from data.ma_announcements import find_terminated_deals
        mock_get.side_effect = _wire(self._hits(), self._docs(),
                                     indexes=self._indexes(),
                                     doc_fail=("term_body.htm",))
        deals, ok = find_terminated_deals(self.FHN, "First Horizon Bank")
        self.assertEqual(deals, [])
        self.assertFalse(ok)

    @patch("data.ma_announcements.time.sleep", lambda *_: None)
    @patch("data.ma_announcements.requests.get")
    def test_404_term_doc_is_cacheable(self, mock_get):
        # Permanent archive gap on the termination 8-K: no deal surfaces,
        # but the empty result is cacheable — unlike the 503 case above.
        from data.ma_announcements import find_terminated_deals
        mock_get.side_effect = _wire(self._hits(), self._docs(),
                                     indexes=self._indexes(),
                                     doc_http={"term_body.htm": 404})
        deals, ok = find_terminated_deals(self.FHN, "First Horizon Bank")
        self.assertEqual(deals, [])
        self.assertTrue(ok)

    def test_no_cik_returns_empty(self):
        from data.ma_announcements import find_terminated_deals
        self.assertEqual(find_terminated_deals(None, "X"), ([], True))


def _doc_status_resp(status):
    """A response whose raise_for_status() raises that HTTP status."""
    r = MagicMock()
    r.raise_for_status.side_effect = _http_error(status)
    return r


class TestDocNegativeCache(unittest.TestCase):
    """(c) — a permanently-404 EDGAR document is remembered so the nightly job
    never re-fetches it, even when its bank legitimately fails to cache."""

    def _fake_cache(self):
        store = {}
        return (store,
                patch("data.cache.get",
                      side_effect=lambda k, max_age_s=None: store.get(k)),
                patch("data.cache.put",
                      side_effect=lambda k, v: store.__setitem__(k, v)))

    @patch("data.ma_announcements.time.sleep", lambda *_: None)
    @patch("data.ma_announcements.requests.get")
    def test_404_remembered_then_skips_network(self, mock_get):
        from data.ma_announcements import _fetch_doc_text
        mock_get.return_value = _doc_status_resp(404)
        store, gp, pp = self._fake_cache()
        with gp, pp:
            t1, ok1 = _fetch_doc_text(829281, "0000909518-01-000405", "0001.txt")
            self.assertIsNone(t1)
            self.assertTrue(ok1)                 # permanent gap
            self.assertEqual(mock_get.call_count, 1)
            self.assertEqual(len(store), 1)      # 404 remembered
            # Second call for the SAME dead doc: no network, still (None, True).
            t2, ok2 = _fetch_doc_text(829281, "0000909518-01-000405", "0001.txt")
            self.assertIsNone(t2)
            self.assertTrue(ok2)
            self.assertEqual(mock_get.call_count, 1)   # NOT re-fetched

    @patch("data.ma_announcements.time.sleep", lambda *_: None)
    @patch("data.ma_announcements.requests.get")
    def test_503_not_remembered(self, mock_get):
        from data.ma_announcements import _fetch_doc_text
        mock_get.return_value = _doc_status_resp(503)
        store, gp, pp = self._fake_cache()
        with gp, pp:
            t, ok = _fetch_doc_text(1, "0001-18-1", "d.txt")
            self.assertIsNone(t)
            self.assertFalse(ok)                 # transient — retry allowed
            self.assertEqual(store, {})          # NOT negative-cached
            # A retry still hits the network (no negative-cache short-circuit).
            _fetch_doc_text(1, "0001-18-1", "d.txt")
            self.assertEqual(mock_get.call_count, 2)


class TestCleanCompanyName(unittest.TestCase):
    """Pins the 'About X. X' footer run-on fix (live: HOPE's ticker pair
    captured 'About Territorial Bancorp Inc. Territorial Bancorp Inc.' and
    the first repair mangled 'Heartland Financial USA, Inc.' to 'USA, Inc'
    — every case below is a real capture from 2026-07-16 verification)."""

    def test_ground_truth_cases(self):
        from data.ma_announcements import _clean_company_name
        cases = [
            ("About Catalyst Bancorp, Inc. Catalyst Bancorp, Inc.",
             "Catalyst Bancorp, Inc"),
            ("About Territorial Bancorp Inc. Territorial Bancorp Inc.",
             "Territorial Bancorp Inc"),
            ("TBNK About Hope Bancorp, Inc. Hope Bancorp, Inc.",
             "Hope Bancorp, Inc"),
            ("Heartland Financial USA, Inc.", "Heartland Financial USA, Inc"),
            ("U.S. Bancorp", "U.S. Bancorp"),
            ("Lakeside Bancshares, Inc", "Lakeside Bancshares, Inc"),
            ("Lakeside", "Lakeside"),
            ("First Hawaiian, Inc.", "First Hawaiian, Inc"),
            ("American Bank Holding Company", "American Bank Holding Company"),
        ]
        for raw, want in cases:
            self.assertEqual(_clean_company_name(raw), want, raw)


# CLST/Lakeside-style pure-cash announcement (live-verified 2026-04-08): a
# PRIVATE target (no ticker pair for it), the filer's own "(Nasdaq: CLST)"
# boilerplate as the ONLY ticker pair — polluted with the "About X. X"
# footer run-on — and headline full name vs body short name.
CASH_PR = (
    "Catalyst Bancorp, Inc. Announces Agreement to Acquire Lakeside "
    "Bancshares, Inc. Catalyst has entered into a definitive agreement "
    "under which Catalyst will acquire Lakeside Bancshares, Inc. and its "
    "wholly-owned subsidiary Lakeside Bank. The acquisition of Lakeside "
    "in an all-cash transaction is valued at $41.1 million in aggregate. "
    "About Catalyst Bancorp, Inc. Catalyst Bancorp, Inc. (Nasdaq: CLST) "
    "is the parent company of Catalyst Bank.")


class TestFindOpenAnnouncements(unittest.TestCase):
    """The cash-deal pending CANDIDATE leg. These are candidates only — the
    open-status gate lives in ma_pending.find_pending_deals — but the leg
    itself pins: the self-as-counterparty bug (empty subject_name + the
    filer's own boilerplate ticker pair), footer-run-on name cleaning, the
    2.01-group exclusion (a completion 8-K with items {2.01, 8.01} must not
    classify as an announcement — the UMB/Heartland leak), and never-guess
    strictness."""

    def _run(self, pr_text, subject_name="", items=None):
        import datetime as _dt

        class _FakeDate(_dt.date):
            @classmethod
            def today(cls):
                return _dt.date.fromisoformat("2026-07-16")

        hits = [_hit("0001-26-5", "2026-04-08", "clst8k.htm",
                     file_type="8-K", cik="0001849867",
                     items=items or ["1.01", "7.01", "9.01"],
                     display_names=["Catalyst Bancorp, Inc.  (CLST)  "
                                    "(CIK 0001849867)"])]
        with patch("data.ma_announcements.requests.get",
                   return_value=_efts_resp(hits)), \
             patch("data.ma_announcements._accession_text",
                   return_value=(pr_text, True)), \
             patch("data.ma_announcements.time.sleep", lambda *_: None), \
             patch("data.ma_announcements.date", _FakeDate):
            from data.ma_announcements import find_open_announcements
            return find_open_announcements(1849867, subject_name)

    def test_private_target_full_name_never_self(self):
        # Empty subject_name = the live self-as-counterparty trigger.
        rows, ok = self._run(CASH_PR, subject_name="")
        self.assertTrue(ok)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["counterparty_name"], "Lakeside Bancshares, Inc")
        self.assertNotIn("atalyst", r["counterparty_name"])
        self.assertEqual(r["direction"], "acquisition")
        self.assertEqual(r["announce_date"], "2026-04-08")
        self.assertEqual(r["value_usd"], 41_100_000)
        self.assertEqual(r["value_basis"], "stated")

    def test_with_subject_name_same_result(self):
        rows, ok = self._run(CASH_PR, subject_name="Catalyst Bank")
        self.assertTrue(ok)
        self.assertEqual([r["counterparty_name"] for r in rows],
                         ["Lakeside Bancshares, Inc"])

    def test_completion_201_group_never_a_candidate(self):
        # UMB/Heartland class: the completion 8-K carries 8.01 ALONGSIDE its
        # 2.01 and previously classified as an announcement. Zero rows AND
        # zero document fetches.
        with patch("data.ma_announcements._accession_text") as mock_text:
            import datetime as _dt

            class _FakeDate(_dt.date):
                @classmethod
                def today(cls):
                    return _dt.date.fromisoformat("2026-07-16")

            hits = [_hit("0001-26-5", "2026-04-08", "close8k.htm",
                         file_type="8-K", cik="0001849867",
                         items=["2.01", "8.01", "9.01"])]
            with patch("data.ma_announcements.requests.get",
                       return_value=_efts_resp(hits)), \
                 patch("data.ma_announcements.time.sleep", lambda *_: None), \
                 patch("data.ma_announcements.date", _FakeDate):
                from data.ma_announcements import find_open_announcements
                rows, ok = find_open_announcements(1849867, "Catalyst Bank")
        self.assertEqual(rows, [])
        self.assertTrue(ok)
        mock_text.assert_not_called()

    def test_ticker_pair_footer_runon_cleaned(self):
        # HOPE class: the counterparty ticker pair's captured name is the
        # "About X. X" footer run-on — the emitted name must be clean.
        pr = ("Hope Bancorp, Inc. and Territorial Bancorp Inc. today "
              "announced a definitive agreement under which Hope will "
              "acquire Territorial Bancorp Inc. in an all-stock "
              "transaction. Territorial shareholders will receive shares. "
              "About Territorial Bancorp Inc. Territorial Bancorp Inc. "
              "(NASDAQ: TBNK) is the holding company of Territorial "
              "Savings Bank.")
        import datetime as _dt

        class _FakeDate(_dt.date):
            @classmethod
            def today(cls):
                return _dt.date.fromisoformat("2026-07-16")

        hits = [_hit("0001-26-7", "2026-04-08", "hope8k.htm",
                     file_type="8-K", cik="0001128361",
                     items=["1.01", "9.01"],
                     display_names=["Hope Bancorp, Inc.  (HOPE)  "
                                    "(CIK 0001128361)"])]
        with patch("data.ma_announcements.requests.get",
                   return_value=_efts_resp(hits)), \
             patch("data.ma_announcements._accession_text",
                   return_value=(pr, True)), \
             patch("data.ma_announcements.time.sleep", lambda *_: None), \
             patch("data.ma_announcements.date", _FakeDate):
            from data.ma_announcements import find_open_announcements
            rows, ok = find_open_announcements(1128361, "Bank of Hope")
        self.assertTrue(ok)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["counterparty_name"],
                         "Territorial Bancorp Inc")

    def test_no_cik_returns_empty(self):
        from data.ma_announcements import find_open_announcements
        self.assertEqual(find_open_announcements(None, "X"), ([], True))


if __name__ == "__main__":
    unittest.main()
