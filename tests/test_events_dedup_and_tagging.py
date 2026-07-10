"""
Pins the 2026-06-15 news-quality pass on the events pipeline:

  • cross-source dedup — the SAME syndicated release ingested from one wire is
    NOT re-added from another (collapses cross-wire/aggregator duplicates),
    while distinct headlines and SEC 8-K filings are left untouched.
  • wire bank-tagging — wire adapters match on the TITLE only, so a bank named
    only in the body (as an underwriter / investor / advisor) is NOT mis-tagged.
  • name-matching coverage — one-word brands ("JPMorganChase") and legal-suffix
    forms ("Zions Bancorporation"→"Zions", "Comerica Incorporated"→"Comerica")
    now resolve to their ticker.
  • regulatory coverage — material enforcement events pass the Google News
    first-party gate even though they carry no company PR verb.

No live network: the RSS layer is mocked; the store runs on a private in-memory
SQLite DB.
"""
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

# Stub streamlit before importing data modules (house pattern).
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

import data.db as db  # noqa: E402
import data.events.store as store  # noqa: E402
from data.events.base import Event  # noqa: E402
from data.events.wire_base import (  # noqa: E402
    match_tickers, _normalize_name, RSSItem,
)

NOW = datetime.now(timezone.utc)

# ── Module-wide network guard ────────────────────────────────────────────────
# The real-index tests build the name index via get_universe_tickers(), whose
# _resolves() fires a LIVE FDIC cert_is_active per FDIC-only ticker. That call
# is cached in data.cache with a 1-week TTL, so on a warm box this suite runs
# in <1s — but with a cold/expired fdic_active:* cache, FDIC throttles the
# burst and get_with_retry backoff-sleeps (up to 30s/attempt, 3 attempts) per
# ticker, per index build: the observed ~10-minute runs. cert_is_active's own
# documented FDIC-outage fallback is "assume active" (and the persisted
# universe snapshot only contains ACTIVE:1 banks by construction), so a
# constant True is the honest offline answer for snapshot members — the index
# the tests assert against is identical to a warm-cache run.
_MODULE_PATCHES: list = []


def setUpModule():
    import data.fdic_client as fc
    p = patch.object(fc, "cert_is_active", lambda *a, **k: True)
    p.start()
    _MODULE_PATCHES.append(p)


def tearDownModule():
    while _MODULE_PATCHES:
        _MODULE_PATCHES.pop().stop()


def _require_universe_snapshot(tc: unittest.TestCase):
    """The real-index tests read the persisted universe snapshot. Without one
    (fresh DB), get_universe() would bootstrap the ~6.5-minute LIVE SEC×FDIC
    build inside a unit test — skip loudly instead."""
    from data.bank_universe import _load_lastgood
    try:
        snap = _load_lastgood()[0]
    except Exception:
        snap = None
    if not snap:
        tc.skipTest("no persisted universe snapshot — real-index test would "
                    "trigger a live universe build")


def _fresh_db():
    """Private in-memory SQLite so tests never touch cache.db / Postgres."""
    from sqlalchemy import create_engine
    db._engine = create_engine("sqlite://")
    db.USE_POSTGRES = False
    store._USE_POSTGRES = False
    store._engine = None
    store.init_schema()


def _ev(ticker, source, headline, ext_id, age_min=1, url="https://x.com/a"):
    return Event(ticker=ticker, source=source, event_type="press_release",
                 headline=headline, published_at=NOW - timedelta(minutes=age_min),
                 url=url, external_id=ext_id)


class TestCrossSourceDedup(unittest.TestCase):
    def setUp(self):
        _fresh_db()

    def test_same_release_two_wires_collapses(self):
        # A release ingested from Business Wire is NOT duplicated when Google
        # News re-syndicates the identical headline for the same bank.
        h = "Ameris Bancorp Reports Second Quarter 2026 Results"
        first = store.insert_events_returning_new(
            [_ev("ABCB", "businesswire", h, "bw-guid-1")])
        second = store.insert_events_returning_new(
            [_ev("ABCB", "google_news", h, "ABCB::ameris-q2")])
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0, "cross-wire duplicate must collapse")

    def test_collapse_within_a_single_batch(self):
        # Both copies arriving in one insert call still collapse to one row.
        h = "Zions Bancorporation Prices $500 Million Senior Notes Offering"
        new = store.insert_events_returning_new([
            _ev("ZION", "prnewswire", h, "prn-1"),
            _ev("ZION", "google_news", h, "ZION::notes"),
            _ev("ZION", "yfinance_news", h, "yf-1"),
        ])
        self.assertEqual(len(new), 1)

    def test_punctuation_and_case_differences_still_collapse(self):
        a = "First Horizon Announces Share Repurchase Program of $500 Million"
        b = "First Horizon announces share repurchase program of $500 million!"
        store.insert_events_returning_new([_ev("FHN", "businesswire", a, "bw-2")])
        second = store.insert_events_returning_new([_ev("FHN", "google_news", b, "gn-2")])
        self.assertEqual(len(second), 0)

    def test_distinct_headlines_not_collapsed(self):
        new = store.insert_events_returning_new([
            _ev("PNC", "businesswire", "PNC Financial Increases Quarterly Dividend", "bw-a"),
            _ev("PNC", "google_news", "PNC Financial Appoints New Chief Risk Officer", "gn-a"),
        ])
        self.assertEqual(len(new), 2, "different stories must both survive")

    def test_different_tickers_not_collapsed(self):
        h = "Reports Second Quarter 2026 Results"
        new = store.insert_events_returning_new([
            _ev("ABCB", "businesswire", "Ameris " + h, "bw-x"),
            _ev("SFBS", "businesswire", "ServisFirst " + h, "bw-y"),
        ])
        self.assertEqual(len(new), 2)

    def test_sec_8k_excluded_from_content_dedup(self):
        # 8-K headlines are generic ("8-K · Earnings / Results"); two DISTINCT
        # filings share a headline but must both survive (they dedup on
        # accession, not content).
        new = store.insert_events_returning_new([
            _ev("JPM", "sec_8k", "8-K · Earnings / Results", "0001-25-000001"),
            _ev("JPM", "sec_8k", "8-K · Earnings / Results", "0001-25-000002"),
        ])
        self.assertEqual(len(new), 2)

    def test_8k_and_wire_same_headline_coexist(self):
        # An 8-K is not collapsed against a wire release even if worded alike —
        # the 8-K is the primary filing and stays.
        store.insert_events_returning_new(
            [_ev("JPM", "businesswire", "JPMorgan Chase Declares Dividend", "bw-z")])
        new = store.insert_events_returning_new(
            [_ev("JPM", "sec_8k", "JPMorgan Chase Declares Dividend", "acc-1")])
        self.assertEqual(len(new), 1, "8-K is exempt from content dedup")

    def _sources(self, ticker):
        return sorted(r["source"] for r in store.get_recent_events(ticker))

    def test_wire_upgrades_aggregator_copy_in_place(self):
        # The bug: fmp_news polled the JPMorgan release first, deduping away the
        # businesswire copy — leaving it under a source Home hides. Now the wire
        # copy UPGRADES the stored aggregator row in place: one row, sourced to
        # the wire (Home-visible), with the wire's URL.
        h = "JPMorganChase Names Doug Petno and Troy Rohrbaugh Co-Presidents of the Company"
        first = store.insert_events_returning_new(
            [_ev("JPM", "fmp_news", h, "fmp-1", url="https://fmp/x")])
        second = store.insert_events_returning_new(
            [_ev("JPM", "businesswire", h, "bw-1", url="https://businesswire/y")])
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1, "wire upgrade surfaces as a new/changed row")
        self.assertEqual(self._sources("JPM"), ["businesswire"],
                         "single row, now sourced to the first-party wire")
        row = store.get_recent_events("JPM")[0]
        self.assertEqual(row["url"], "https://businesswire/y")
        self.assertEqual(row["external_id"], "bw-1")

    def test_aggregator_does_not_downgrade_a_wire_copy(self):
        # Reverse order: wire stored first, aggregator re-syndicates later. The
        # aggregator must NOT replace the wire and must NOT add a second row.
        h = "Comerica Incorporated Declares Quarterly Dividend"
        store.insert_events_returning_new([_ev("CMA", "businesswire", h, "bw-1")])
        second = store.insert_events_returning_new([_ev("CMA", "fmp_news", h, "fmp-1")])
        self.assertEqual(len(second), 0)
        self.assertEqual(self._sources("CMA"), ["businesswire"])

    def test_same_batch_wire_wins_over_aggregator(self):
        # Both copies in one batch (aggregator listed first): collapse to one row
        # sourced to the wire regardless of order.
        h = "Zions Bancorporation Prices $500 Million Senior Notes Offering"
        store.insert_events_returning_new([
            _ev("ZION", "fmp_news", h, "fmp-1"),
            _ev("ZION", "businesswire", h, "bw-1"),
        ])
        self.assertEqual(self._sources("ZION"), ["businesswire"])

    def test_aggregator_only_release_is_retained(self):
        # No wire copy ever arrives — the fmp_news row stays (Home's allowlist now
        # includes fmp_news, so it's still surfaced).
        h = "Some Bancorp Announces Strategic Partnership"
        new = store.insert_events_returning_new([_ev("SBNC", "fmp_news", h, "fmp-1")])
        self.assertEqual(len(new), 1)
        self.assertEqual(self._sources("SBNC"), ["fmp_news"])


class TestWireUpgradeUniqueCollision(unittest.TestCase):
    """The upgrade path retargets a stored aggregator row's (source,
    external_id) onto the incoming wire copy's — but when the wire copy ALREADY
    exists as its own row, that UPDATE hits the UNIQUE constraint and rolled
    back the WHOLE adapter batch (audit P3). Routine now that adapters re-scan
    their full lookback (P2 #21): a wire item published just outside the 5-day
    content-dedup window, whose aggregator twin sits inside it, is re-emitted
    every cycle — without the guard the wire source would stop ingesting
    entirely until the twin ages out."""

    def setUp(self):
        _fresh_db()

    def test_existing_wire_row_skips_upgrade_and_batch_survives(self):
        h = "JPMorgan Chase Declares Quarterly Common Stock Dividend"
        wire = Event(ticker="JPM", source="businesswire",
                     event_type="press_release", headline=h,
                     published_at=NOW - timedelta(days=6),   # OUTSIDE 5d window
                     url="https://businesswire/x", external_id="bw-guid-1")
        agg = Event(ticker="JPM", source="fmp_news",
                    event_type="press_release", headline=h,
                    published_at=NOW - timedelta(days=4),    # INSIDE 5d window
                    url="https://fmp/x", external_id="fmp-guid-1")
        # Both copies end up stored: the wire row is invisible to the window
        # query when the aggregator twin arrives.
        self.assertEqual(len(store.insert_events_returning_new([wire])), 1)
        self.assertEqual(len(store.insert_events_returning_new([agg])), 1)
        # Full-lookback re-scan re-emits the wire copy, batched with an
        # unrelated fresh event: must NOT raise, must NOT lose the fresh
        # event, and must leave both stored rows intact.
        fresh = _ev("BAC", "prnewswire", "Bank of America Names New CFO", "prn-9")
        out = store.insert_events_returning_new([wire, fresh])
        self.assertEqual([e.external_id for e in out], ["prn-9"],
                         "the colliding upgrade is skipped; the batch survives")
        from sqlalchemy import text as _sql
        with store._get_engine().connect() as conn:
            n = conn.execute(_sql("SELECT COUNT(*) FROM events")).scalar()
        self.assertEqual(n, 3)


class TestUniverseRecentScoping(unittest.TestCase):
    """get_universe_recent enriches the universe-wide feed at READ time, but only
    when the universe is already built (never a cold build on the read path):
      • canonicalizes a non-common sibling ticker onto its common (VYLD -> JPM),
      • drops out-of-scope tickers (skip-listed broker-dealers / card issuers).
    Drives the REAL share_class / coverage logic against a small injected
    universe rather than mocking it."""

    def setUp(self):
        _fresh_db()
        import data.bank_universe as bu
        self.bu = bu
        self._saved = (bu._UNIVERSE_CACHE, bu._NONCOMMON_CACHE,
                       bu._NONCOMMON_PRIMARY_CACHE)

    def tearDown(self):
        (self.bu._UNIVERSE_CACHE, self.bu._NONCOMMON_CACHE,
         self.bu._NONCOMMON_PRIMARY_CACHE) = self._saved

    def _set_universe(self, uni):
        # Inject a built universe so universe_is_cached() is True and the real
        # accessors compute from it (no network build).
        self.bu._UNIVERSE_CACHE = uni
        self.bu._NONCOMMON_CACHE = None
        self.bu._NONCOMMON_PRIMARY_CACHE = None

    def test_sibling_ticker_canonicalized_to_common(self):
        # A legacy 8-K frozen under VYLD (a JPMorgan ETN under CIK 19617) must
        # DISPLAY as JPM — it can't be re-tagged in place (frozen accession).
        self._set_universe({"JPM": {"cik": 19617}, "VYLD": {"cik": 19617}})
        store.insert_events_returning_new(
            [_ev("VYLD", "sec_8k", "8-K · Officer / Director Change", "acc-v1")])
        rows = store.get_universe_recent(limit=50, sources=["sec_8k"])
        self.assertEqual([r["ticker"] for r in rows], ["JPM"])

    def test_excluded_broker_dealers_dropped_from_feed(self):
        # RJF / FRHC are skip-listed (broker-dealers): their already-stored news
        # must be filtered out of the feed; a real bank (PNC) stays.
        self._set_universe({"PNC": {"cik": 713676}, "RJF": {"cik": 720005},
                            "FRHC": {"cik": 924805}})
        store.insert_events_returning_new([
            _ev("PNC", "sec_8k", "8-K · Earnings / Results", "acc-pnc"),
            _ev("RJF", "sec_8k", "8-K · Other Material Event", "acc-rjf"),
            _ev("FRHC", "businesswire", "Freedom Holding Reports Results", "bw-frhc"),
        ])
        rows = store.get_universe_recent(limit=50)
        self.assertEqual(sorted(r["ticker"] for r in rows), ["PNC"])

    def _ma(self, ticker, source, headline, summary, ext, age_min=1):
        return Event(ticker=ticker, source=source, event_type="m_and_a",
                     headline=headline, summary=summary,
                     published_at=NOW - timedelta(minutes=age_min),
                     url=f"https://x/{ext}", external_id=ext)

    def test_ma_event_dedup_collapses_same_deal(self):
        # One merger, per ticker: the bank's 8-K (summary) + the wire headline —
        # different wording, so text-dedup misses it; event-dedup collapses it.
        self.bu._UNIVERSE_CACHE = None
        store.insert_events_returning_new([
            self._ma("PB", "sec_8k", "8-K · Acquisition / Disposition Completed",
                     "Prosperity Bancshares completed its merger with Stellar "
                     "Bancorp on July 1, 2026, acquiring $5B in assets.", "pb-8k", 1),
            self._ma("PB", "businesswire",
                     "Prosperity Bancshares, Inc. Completes Merger with Stellar "
                     "Bancorp, Inc.", "", "pb-bw", 6),
            self._ma("STEL", "sec_8k", "8-K · Acquisition / Disposition Completed",
                     "Stellar Bancorp merged into Prosperity Bancshares effective "
                     "July 1, 2026.", "stel-8k", 2),
            self._ma("STEL", "businesswire",
                     "Prosperity Bancshares, Inc. Completes Merger with Stellar "
                     "Bancorp, Inc.", "", "stel-bw", 7),
        ])
        rows = store.get_universe_recent(limit=50)
        self.assertEqual(sum(1 for r in rows if r["ticker"] == "PB"), 1)
        self.assertEqual(sum(1 for r in rows if r["ticker"] == "STEL"), 1)

    def test_ma_different_deals_kept(self):
        # Two DIFFERENT deals by one acquirer share only the acquirer → keep both.
        self.bu._UNIVERSE_CACHE = None
        store.insert_events_returning_new([
            self._ma("PB", "businesswire",
                     "Prosperity Bancshares Completes Merger with Stellar Bancorp",
                     "", "d1", 1),
            self._ma("PB", "businesswire",
                     "Prosperity Bancshares Completes Merger with Veritex Holdings",
                     "", "d2", 6),
        ])
        rows = store.get_universe_recent(limit=50)
        self.assertEqual(sum(1 for r in rows if r["ticker"] == "PB"), 2)

    def test_non_ma_not_touched_by_event_dedup(self):
        # Q1 vs Q2 earnings (non-M&A) share generic tokens but must both survive.
        self.bu._UNIVERSE_CACHE = None
        store.insert_events_returning_new([
            _ev("PB", "businesswire",
                "Prosperity Bancshares Reports First Quarter 2026 Results", "q1"),
            _ev("PB", "businesswire",
                "Prosperity Bancshares Reports Second Quarter 2026 Results", "q2"),
        ])
        rows = store.get_universe_recent(limit=50)
        self.assertEqual(sum(1 for r in rows if r["ticker"] == "PB"), 2)

    def test_cross_source_duplicate_collapsed_on_read(self):
        # A bank's IR-site copy + its Business Wire copy of one release (ir_site is
        # exempt from ingest dedup, so both are stored) collapse to ONE feed row.
        self.bu._UNIVERSE_CACHE = None   # dedup runs regardless of universe build
        h = "BayFirst Announces Second Quarter 2026 Conference Call and Webcast"
        store.insert_events_returning_new([
            _ev("BAFN", "ir_site", h, "ir-1"),
            _ev("BAFN", "businesswire", h, "bw-1"),
        ])
        rows = store.get_universe_recent(limit=50)
        self.assertEqual(sum(1 for r in rows if r["ticker"] == "BAFN"), 1)

    def test_distinct_8ks_not_collapsed_on_read(self):
        # Two distinct 8-Ks share the generic item headline — both must survive.
        self.bu._UNIVERSE_CACHE = None
        store.insert_events_returning_new([
            _ev("PNC", "sec_8k", "8-K · Other Material Event", "acc-1"),
            _ev("PNC", "sec_8k", "8-K · Other Material Event", "acc-2"),
        ])
        rows = store.get_universe_recent(limit=50, sources=["sec_8k"])
        self.assertEqual(sum(1 for r in rows if r["ticker"] == "PNC"), 2)

    def test_skip_tickers_drop_even_when_universe_not_built(self):
        # Skip-listed tickers are a STATIC set — they must drop from the feed even
        # when the universe isn't cached (and AFTER the nightly rebuild removes
        # them from the universe, the SF/JXN leak). A normal ticker passes through
        # without triggering a cold universe build.
        self.bu._UNIVERSE_CACHE = None
        store.insert_events_returning_new([
            _ev("PNC", "sec_8k", "8-K · Earnings / Results", "acc-pnc9"),
            _ev("RJF", "sec_8k", "8-K · Other Material Event", "acc-rjf9"),
            _ev("SF", "sec_8k", "8-K · Other Material Event", "acc-sf9"),
            _ev("JXN", "sec_8k", "8-K · Other Material Event", "acc-jxn9"),
        ])
        rows = store.get_universe_recent(limit=50, sources=["sec_8k"])
        self.assertEqual([r["ticker"] for r in rows], ["PNC"],
                         "skip tickers drop without a build; PNC stays")


class TestNoncommonPrimaryMap(unittest.TestCase):
    """noncommon_to_primary maps each preferred/ETN sibling onto the registrant's
    primary common, so a mis-attributed ticker can be canonicalized on display."""

    def test_maps_sibling_to_primary_common(self):
        from data.share_class import noncommon_to_primary
        uni = {
            "JPM":  {"cik": 19617, "exchange": "NYSE"},
            "VYLD": {"cik": 19617, "exchange": "NYSE Arca"},
            "AMJB": {"cik": 19617, "exchange": "NYSE Arca"},
        }
        m = noncommon_to_primary(uni)
        self.assertEqual(m.get("VYLD"), "JPM")
        self.assertEqual(m.get("AMJB"), "JPM")
        self.assertNotIn("JPM", m, "the common must never remap to itself")

    def test_single_ticker_registrant_not_mapped(self):
        from data.share_class import noncommon_to_primary
        self.assertEqual(noncommon_to_primary({"PNC": {"cik": 713676}}), {})


class TestEightKCikCollision(unittest.TestCase):
    """A single registrant (one CIK) lists several tickers — common + preferred
    series + bank-issued ETNs. CIK 19617 = JPM (common), VYLD/AMJB (ETNs). They
    share the registrant's 8-Ks, so the recent-feed adapter must attribute them
    to the COMMON regardless of input order. Regression: JPMorgan 8-Ks tagged
    ">VYLD" because the sibling clobbered JPM in the CIK->ticker map."""

    def test_bank_map_common_wins_cik_collision(self):
        from data.events import sec_8k
        # JPM is in BANK_MAP (cik 19617); VYLD/AMJB are not. Patch the resolver so
        # all three report the shared CIK without a live lookup.
        with patch.object(sec_8k, "get_cik",
                          side_effect=lambda t: 19617 if t.upper() in
                          ("JPM", "VYLD", "AMJB") else None):
            for order in (["AMJB", "JPM", "VYLD"], ["VYLD", "AMJB", "JPM"],
                          ["JPM", "VYLD"], ["VYLD", "JPM"]):
                m = sec_8k._canonical_cik_map(order)
                self.assertEqual(m.get(19617), "JPM", f"order={order}")

    def test_unknown_cik_collision_is_order_deterministic(self):
        from data.events import sec_8k
        # Neither ticker is curated — keep the first seen (stable, never random).
        with patch.object(sec_8k, "get_cik", side_effect=lambda t: 42):
            self.assertEqual(sec_8k._canonical_cik_map(["BBB", "AAA"]).get(42), "BBB")
            self.assertEqual(sec_8k._canonical_cik_map(["AAA", "BBB"]).get(42), "AAA")


class TestSubsidiaryNameIndexing(unittest.TestCase):
    """build_name_index indexes each bank's FDIC subsidiary brand (bank_name)
    alongside its SEC holdco name, so a release under the bank brand ("Provident
    Bank Appoints…") matches the holdco ticker. Mocked universe; aliases patched
    out so it proves the bank_name path, not the curated Provident alias."""

    def setUp(self):
        import data.events.wire_base as wb
        self.wb = wb
        self._saved = (wb._NAME_INDEX, wb._AMBIGUOUS_INDEX)
        wb._NAME_INDEX, wb._AMBIGUOUS_INDEX = [], {}

    def tearDown(self):
        # RESTORE the previously built index (don't clear to []): clearing
        # forced every later match_tickers caller into a full index rebuild —
        # each one a fresh get_universe_tickers() pass (the suite's wall-clock
        # sink on a cold cache). Restoring keeps the same real index those
        # tests would have rebuilt.
        self.wb._NAME_INDEX, self.wb._AMBIGUOUS_INDEX = self._saved

    def _build(self, names, universe):
        import data.bank_universe as bu
        import data.bank_mapping as bm
        with patch.object(self.wb, "BANK_MAP", {}), \
             patch.object(self.wb, "_BRAND_ALIASES", {}), \
             patch.object(bu, "get_universe_tickers", return_value=list(names)), \
             patch.object(bu, "get_universe", return_value=universe), \
             patch.object(bm, "get_name", side_effect=lambda t: names.get(t)), \
             patch.object(bm, "get_cik", side_effect=lambda t: {"PFS": 1, "FNLC": 2}.get(t)):
            self.wb._NAME_INDEX = self.wb.build_name_index()

    def test_subsidiary_brand_matches_holdco_ticker(self):
        self._build({"PFS": "Provident Financial Services"},
                    {"PFS": {"bank_name": "Provident Bank"}})
        self.assertEqual(self.wb.match_tickers("Provident Bank Appoints a New CFO"), ["PFS"])
        self.assertEqual(
            self.wb.match_tickers("Provident Financial Services Declares Dividend"), ["PFS"])

    def test_missing_bank_name_is_safe(self):
        # Snapshot without bank_name (pre refresh-universe) → only holdco indexed.
        self._build({"PFS": "Provident Financial Services"}, {"PFS": {}})
        self.assertEqual(self.wb.match_tickers("Provident Bank Appoints a New CFO"), [])
        self.assertEqual(
            self.wb.match_tickers("Provident Financial Services Declares Dividend"), ["PFS"])


class TestNameMatchingCoverage(unittest.TestCase):
    def setUp(self):
        _require_universe_snapshot(self)

    def test_one_word_jpmorganchase_brand(self):
        self.assertIn("JPM", match_tickers(
            "JPMorganChase Expands Security and Resiliency Initiative to Canada"))

    def test_zions_brand_after_bancorporation_strip(self):
        self.assertEqual(_normalize_name("Zions Bancorporation"), "ZIONS")
        self.assertIn("ZION", match_tickers(
            "Zions enters written agreement with the Federal Reserve"))

    def test_incorporated_suffix_stripped(self):
        # Legal "Incorporated" suffix dropped so the SEC name matches the brand
        # used in headlines.
        self.assertEqual(_normalize_name("Comerica Incorporated"), "COMERICA")

    def test_bancorp_not_overstripped(self):
        # "Bancorp" (vs "Bancorporation") is intentionally preserved so common
        # names aren't shortened to a single generic token.
        self.assertEqual(_normalize_name("First Bancorp"), "FIRST BANCORP")


class TestSingleTokenCollisionGuard(unittest.TestCase):
    """A bare common-word / short-initialism index entry ('FREEDOM'->FRHC,
    'FNB'->FNB) collides with unrelated text, so it tags a bank ONLY when the
    issuer's exchange-qualified ticker confirms it. Pins the 2026-06-23 feed
    mis-tags ('Freedom Boat Club'->FRHC, French 'FNB AGF'->FNB). Injects the
    exact prod index entries to skip the slow universe build."""

    def setUp(self):
        import data.events.wire_base as wb
        self._wb = wb
        self._saved = (wb._NAME_INDEX, wb._AMBIGUOUS_INDEX)
        wb._NAME_INDEX = [("FREEDOM", "FRHC"), ("FNB", "FNB")]
        wb._AMBIGUOUS_INDEX = {}

    def tearDown(self):
        self._wb._NAME_INDEX, self._wb._AMBIGUOUS_INDEX = self._saved

    def test_freedom_boat_club_not_tagged_frhc(self):
        self.assertEqual(match_tickers(
            "Freedom Boat Club Marks 450th Global Location",
            "Freedom Boat Club, a business of Brunswick Corporation (NYSE: BC), "
            "today announced its 450th location."), [])

    def test_french_fnb_etf_not_tagged_fnb(self):
        self.assertEqual(match_tickers(
            "Placements AGF annonce les distributions pour certains FNB AGF",
            "La serie FNB AGF du Fonds de revenu ameliore Etats-Unis Plus AGF."), [])

    def test_legit_fnb_kept_via_exchange_tag(self):
        self.assertIn("FNB", match_tickers(
            "FNB Invests in Future Talent",
            "F.N.B. Corporation (NYSE: FNB) announced its summer internship class."))

    def test_legit_freedom_holding_kept_via_exchange_tag(self):
        self.assertIn("FRHC", match_tickers(
            "Freedom Holding Corp. Reports Record Revenue",
            "Freedom Holding Corp. (NASDAQ: FRHC) today reported fiscal 2026 results."))


class TestMeridianCollisionGuard(unittest.TestCase):
    """MRBK's resolved name is the bare word 'Meridian', which tagged Centene's
    'MERIDIAN HEALTH PLAN OF ILLINOIS' Medicaid PR to the bank (live feed
    mis-tag 2026-07-09). 'MERIDIAN' is now a risky single (in
    _COMMON_NAME_WORDS): a bare match needs a corporate suffix or the exchange
    ticker; the 'Meridian Bank' alias keeps subsidiary-brand recall. Injects
    the exact prod index entries to skip the slow universe build."""

    def setUp(self):
        import data.events.wire_base as wb
        # The injected entries must mirror what build_name_index produces —
        # tie them to the real inputs so this suite can't drift.
        self.assertIn("MERIDIAN", wb._COMMON_NAME_WORDS)
        self.assertIn("Meridian Bank", wb._BRAND_ALIASES.get("MRBK", []))
        self._wb = wb
        self._saved = (wb._NAME_INDEX, wb._AMBIGUOUS_INDEX)
        wb._NAME_INDEX = [("MERIDIAN BANK", "MRBK"), ("MERIDIAN", "MRBK")]
        wb._AMBIGUOUS_INDEX = {}

    def tearDown(self):
        self._wb._NAME_INDEX, self._wb._AMBIGUOUS_INDEX = self._saved

    def test_meridian_health_plan_not_tagged_mrbk(self):
        self.assertEqual(match_tickers(
            "CENTENE SUBSIDIARY MERIDIAN HEALTH PLAN OF ILLINOIS AWARDED "
            "ILLINOIS MEDICAID CONTRACT",
            "Centene Corporation (NYSE: CNC) announced that Meridian Health "
            "Plan of Illinois was awarded a Medicaid contract."), [])

    def test_meridian_corporation_release_kept(self):
        # Corporate suffix confirms the risky single ('Citizens Holding' rule).
        self.assertEqual(match_tickers(
            "Meridian Corporation Reports Second Quarter 2026 Results"), ["MRBK"])

    def test_meridian_bank_brand_release_kept(self):
        # Subsidiary-brand alias — two tokens, not a risky single.
        self.assertEqual(match_tickers(
            "Meridian Bank Announces New Chief Lending Officer"), ["MRBK"])

    def test_bare_meridian_kept_via_exchange_tag(self):
        self.assertEqual(match_tickers(
            "Meridian Declares Quarterly Dividend",
            "Meridian (NASDAQ: MRBK) today announced a dividend."), ["MRBK"])


class TestBusinessScopeSkips(unittest.TestCase):
    def test_main_street_capital_skip_listed(self):
        # Main Street Capital is a BDC wrong-entity-joined to FDIC cert 6592
        # ('The First National Bank of Germantown') — its 8-K/PR loan-portfolio
        # releases polluted the feed (2026-07-09). The read-time _SKIP_TICKERS
        # filter (pinned by TestUniverseRecentScoping) hides stored rows and
        # coverage_excluded() stops future polls.
        from data.bank_universe import _SKIP_TICKERS
        self.assertIn("MAIN", _SKIP_TICKERS)


class TestGoogleNewsCircuitBreaker(unittest.TestCase):
    """When news.google.com 5xx-blocks the datacenter IP, the adapter probes
    once and skips the 435-ticker sweep instead of burning the 240s cap."""

    def _resp(self, code):
        return type("R", (), {"status_code": code})()

    def test_skips_sweep_when_blocked(self):
        from data.events.google_news import GoogleNewsAdapter
        a = GoogleNewsAdapter()
        with patch("requests.get", return_value=self._resp(503)), \
             patch.object(a, "_fetch_ticker",
                          side_effect=AssertionError("must not sweep when blocked")):
            self.assertEqual(a.poll(["JPM", "BAC", "WFC"]), [])

    def test_proceeds_when_not_blocked(self):
        from data.events.google_news import GoogleNewsAdapter
        from unittest.mock import MagicMock
        a = GoogleNewsAdapter()
        fetch = MagicMock(return_value=[])
        with patch("requests.get", return_value=self._resp(200)), \
             patch.object(a, "_fetch_ticker", fetch):
            a.poll(["JPM", "BAC"])
        self.assertTrue(fetch.called, "should sweep when not blocked")


class TestDisambiguateUnit(unittest.TestCase):
    """_disambiguate scoring in isolation (no index build)."""

    def setUp(self):
        from data.events import wire_base as wb
        self.wb = wb

    def _hay(self, s):
        return self.wb._alnum_pad(s)

    def test_ticker_cue_wins(self):
        self.assertEqual(self.wb._disambiguate(
            ["FBP", "FNLC"], self._hay("First BanCorp (NYSE: FBP) reports")), "FBP")

    def test_geo_cue_wins(self):
        self.assertEqual(self.wb._disambiguate(
            ["FBP", "FNLC"], self._hay("First Bancorp, based in Maine, ...")), "FNLC")

    def test_no_cue_returns_none(self):
        self.assertIsNone(self.wb._disambiguate(
            ["FBP", "FNLC"], self._hay("First Bancorp reported results")))

    def test_conflicting_cues_tie_returns_none(self):
        # Both tickers present (e.g. a comparison piece) → can't tell → no tag.
        self.assertIsNone(self.wb._disambiguate(
            ["FBP", "FNLC"], self._hay("FBP vs FNLC: which bank wins")))

    def test_independent_bank_mi_vs_ma(self):
        # "Independent Bank Corp" = IBCP (Michigan) AND INDB (Rockland, MA).
        # (Was routed to INBC — a mapping error; ticker INBC is InBankshares
        # Corp / InBank NM, fixed in the 2026-07-09 wrong-entity sweep.)
        self.assertEqual(self.wb._disambiguate(
            ["IBCP", "INDB"], self._hay("Grand Rapids, Michigan (Nasdaq: IBCP)")), "IBCP")
        self.assertEqual(self.wb._disambiguate(
            ["IBCP", "INDB"], self._hay("Independent Bank, Grand Rapids, Michigan")), "IBCP")
        self.assertEqual(self.wb._disambiguate(
            ["IBCP", "INDB"], self._hay("Rockland, Massachusetts (Nasdaq: INDB)")), "INDB")
        # A bare headline with no ticker/state cue tags NEITHER (no wrong tag).
        self.assertIsNone(self.wb._disambiguate(
            ["IBCP", "INDB"], self._hay("Independent Bank Corporation announces acquisition")))
        # The curated routing itself must point at INDB, never INBC.
        self.assertEqual(self.wb._CURATED_AMBIGUOUS["INDEPENDENT BANK"],
                         ["IBCP", "INDB"])


class TestAmbiguousNameDisambiguation(unittest.TestCase):
    """Real index: 'First Bancorp' (FBP/FNLC, same name + different CIKs) is
    recovered via ticker/geo in the body, without regressing share-class
    siblings (First Niles) or distinguishable collisions (Citizens Holding)."""

    def setUp(self):
        _require_universe_snapshot(self)

    def test_only_same_name_diff_cik_is_ambiguous(self):
        from data.events import wire_base as wb
        wb.build_name_index()
        self.assertIn("FIRST BANCORP", wb._AMBIGUOUS_INDEX)
        self.assertEqual(set(wb._AMBIGUOUS_INDEX["FIRST BANCORP"]), {"FBP", "FNLC"})
        # Citizens (different resolved names) and share-class siblings must NOT
        # be treated as ambiguous.
        self.assertNotIn("CITIZENS", wb._AMBIGUOUS_INDEX)
        self.assertNotIn("FIRST NILES FINANCIAL", wb._AMBIGUOUS_INDEX)

    def test_fbp_recovered_by_ticker(self):
        self.assertEqual(
            match_tickers("First BanCorp to Announce Q2 2026 Results",
                          context="San Juan, Puerto Rico. First BanCorp (NYSE: FBP) today..."),
            ["FBP"])

    def test_fnlc_recovered_by_geo(self):
        self.assertIn("FNLC", match_tickers(
            "The First Bancorp Declares Quarterly Dividend",
            context="Damariscotta, Maine — The First Bancorp announced..."))

    def test_first_bancorp_no_cue_skips(self):
        # Ambiguous and nothing distinguishes them → tag NONE (never a guess).
        self.assertEqual(
            match_tickers("First Bancorp Reports Results",
                          context="First Bancorp reported results today."),
            [])

    def test_citizens_holding_not_regressed(self):
        self.assertEqual(match_tickers("Citizens Holding Company Declares Dividend"),
                         ["CIZN"])

    def test_share_class_sibling_collapsed(self):
        self.assertEqual(match_tickers("First Niles Financial Reports Earnings"),
                         ["FNFI"])


class TestWireTitleOnlyTagging(unittest.TestCase):
    """Wire adapters must tag on the TITLE only — a bank named in the body as an
    investor/underwriter/advisor is not the subject and must not be tagged."""

    def setUp(self):
        # The adapter's match_tickers lazily builds the REAL name index when
        # it isn't already populated (e.g. running this class standalone).
        _require_universe_snapshot(self)

    def _run(self, items, universe):
        from data.events.globenewswire import GlobeNewswireAdapter
        # One mocked feed payload; the adapter calls fetch_rss once per feed URL
        # and dedups on guid, so returning the same list each call is fine.
        with patch("data.events.globenewswire.fetch_rss", return_value=items):
            return GlobeNewswireAdapter().poll(universe)

    def test_body_only_bank_mention_not_tagged(self):
        items = [
            RSSItem(
                title="Arcade Raises $60M to Build Autonomous AI Agents",
                summary="The round was led by JPMorgan Chase and Morgan Stanley.",
                link="https://globenewswire.com/arcade", published=NOW, guid="g1"),
            RSSItem(
                title="JPMorgan Chase Declares Dividend on Preferred Stock",
                summary="Routine quarterly declaration.",
                link="https://globenewswire.com/jpm", published=NOW, guid="g2"),
        ]
        evs = self._run(items, ["JPM", "MS"])
        tagged = {e.ticker for e in evs}
        self.assertNotIn("MS", tagged, "body-only investor mention must not tag MS")
        self.assertIn("JPM", tagged, "the bank's own titled release must be tagged")
        # The Arcade funding story must produce NO bank event at all.
        self.assertTrue(all("Arcade" not in e.headline for e in evs))


class TestPurgeRevalidatesNameMatches(unittest.TestCase):
    """_purge_junk_events re-runs the (now trap-aware) name matcher on stored
    wire/aggregator rows, so a historical mis-tag the matcher would no longer
    make ("First United" on a Century 21 PR) is cleaned out — while the bank's
    own correctly-tagged releases survive."""

    def setUp(self):
        _fresh_db()

    def test_corrected_mistag_purged_legit_kept(self):
        import jobs.poll_events as pe
        import data.events.wire_base as wb
        # Deterministic mini name index (avoids a live universe build).
        with patch.object(wb, "_NAME_INDEX", [("FIRST UNITED", "FUNC")]):
            store.insert_events_returning_new([
                _ev("FUNC", "businesswire",
                    "Century 21 Brand Expands Global Footprint With Opening of "
                    "First United Arab Emirates Office", "bw-junk",
                    url="https://www.businesswire.com/news/junk"),
                _ev("FUNC", "businesswire",
                    "First United Corporation Declares Quarterly Cash Dividend",
                    "bw-legit", url="https://www.businesswire.com/news/legit"),
            ])
            n = pe._purge_junk_events()
        self.assertEqual(n, 1, "only the mis-tagged row should be purged")
        from sqlalchemy import text
        with db._engine.connect() as c:
            kept = [r[0] for r in c.execute(text("SELECT headline FROM events")).all()]
        self.assertEqual(len(kept), 1)
        self.assertIn("Declares Quarterly Cash Dividend", kept[0])


class TestGoogleNewsRegulatoryCoverage(unittest.TestCase):
    def test_regulatory_event_passes_first_party_gate(self):
        from data.events.google_news import GoogleNewsAdapter
        items = [RSSItem(
            title="OCC fines Wells Fargo $250 million over compliance failures - Reuters",
            summary="", link="https://reuters.com/wf", published=NOW, guid="r1")]
        with patch("data.events.google_news.fetch_rss", return_value=items):
            evs = GoogleNewsAdapter()._fetch_ticker("WFC", NOW - timedelta(days=2))
        self.assertEqual(len(evs), 1, "regulatory action should pass the gate")
        self.assertEqual(evs[0].ticker, "WFC")


if __name__ == "__main__":
    unittest.main()
