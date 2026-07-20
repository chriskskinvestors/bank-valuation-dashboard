"""
Pins the 2026-07-20 Home-feed freshness cut.

Owner report: "how is MNSB still not getting picked up?" — MNSB's earnings 8-K
and GlobeNewswire release were both live in News & Research at 6 minutes old
(that view reads the event store directly), while Home showed nothing newer
than 2h. Home does not read the store; it reads a persisted snapshot, and that
snapshot's TTL was 30 minutes. Earnings-season ingestion polls every 60s
(poll-events-earnings-1min), so a release could be ingested and remain
invisible on the page the owner actually watches for half an hour.

Pinned here so the two cadences can't drift apart again silently.
"""
import sys
import types
import unittest

_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
_st.fragment = _st.cache_data
_st.session_state = {}
sys.modules.setdefault("streamlit", _st)


class TestHomeFeedFreshness(unittest.TestCase):
    def test_snapshot_ttl_is_earnings_season_tight(self):
        from ui.home import _NEWS_FEED_SNAP_TTL
        # The fast poll runs every 60s. A Home snapshot older than ~5 min makes
        # that speed invisible on the landing page.
        self.assertLessEqual(
            _NEWS_FEED_SNAP_TTL, 300,
            "Home feed TTL must stay <=5min or fast polling never surfaces")

    def test_ttl_not_so_short_it_thrashes_the_render_path(self):
        from ui.home import _NEWS_FEED_SNAP_TTL
        # served_snapshot rebuilds SYNCHRONOUSLY on the render thread when the
        # snapshot is stale. Too short and every Home load pays for it.
        self.assertGreaterEqual(
            _NEWS_FEED_SNAP_TTL, 120,
            "TTL this short puts a rebuild on nearly every Home render")

    def test_first_party_sources_include_the_wires_that_carry_releases(self):
        # MNSB arrived via globenewswire + sec_8k. Home deliberately excludes
        # google_news (aggregator noise), but must carry every first-party wire
        # or a real release becomes invisible here while showing elsewhere.
        import inspect
        from ui import home
        src = inspect.getsource(home._af_feed_items_live)
        for wire in ("sec_8k", "businesswire", "prnewswire",
                     "globenewswire", "ir_site", "fmp_news"):
            self.assertIn(wire, src, f"Home feed dropped first-party {wire}")
        self.assertNotIn('"google_news"', src,
                         "google_news must stay OUT of the Home feed")

    def test_snapshot_key_version_tracks_filter_changes(self):
        # The key is bumped whenever filter logic changes, so a stored snapshot
        # can't keep serving pre-filter rows. Guards the documented convention.
        from ui.home import _NEWS_FEED_SNAP_KEY
        self.assertTrue(_NEWS_FEED_SNAP_KEY.startswith("home_af_feed_snap_v"))
        ver = int(_NEWS_FEED_SNAP_KEY.rsplit("_v", 1)[1])
        self.assertGreaterEqual(ver, 4, "bump the key when filters change")


if __name__ == "__main__":
    unittest.main()
