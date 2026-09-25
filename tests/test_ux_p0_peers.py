"""UX-review P0 peer-page regressions (2026-09-25).

UX-P0-10  Peer Rank dead-ended for JPM: the Money-Center (>$1T) size tier has
          only 4 banks, below the 6-bank floor, so the page showed a stale
          "Open Home … to load the watchlist" message. Owner rule: a thin size
          tier merges with its adjacent tier (next DOWN in size; the smallest
          tier merges up), and the caption says so.
UX-P0-04  Screen & Compare › Compare defaulted "Banks to tabulate" to the
          first 12 tickers alphabetically. Owner rule: the bank last opened on
          the Company page + its 11 closest banks by total assets (same size
          tier first, filled from the scope); nothing opened / not in scope →
          an empty selection and a prompt, never an arbitrary set.

Run: python -m unittest tests.test_ux_p0_peers -v
"""
import sys
import unittest
from unittest import mock
from unittest.mock import MagicMock

from tests import _streamlit_stub

_streamlit_stub.install()

from analysis.peer_groups import (  # noqa: E402
    MIN_COHORT, get_peer_group_for_bank, metric_percentile_context, peer_cohort,
)
from ui.peer_comparison import default_display_tickers  # noqa: E402

MC = "Money-Center (>$1T)"
LR = "Large Regional ($100B-$1T)"
RG = "Regional ($10-100B)"
CM = "Community (<$10B)"

_MC_BANKS = [("JPM", 4.0e12), ("BAC", 3.3e12), ("C", 2.4e12), ("WFC", 1.7e12)]


def _universe():
    """4 Money-Center + 14 Large Regional ($150B..$800B, step $50B) +
    10 Regional ($20B..$65B) + 3 Community ($1B..$3B). roaa everywhere."""
    banks = [(t, a) for t, a in _MC_BANKS]
    banks += [(f"LR{i}", 150e9 + i * 50e9) for i in range(14)]
    banks += [(f"RG{i}", 20e9 + i * 5e9) for i in range(10)]
    banks += [(f"CM{i}", 1e9 + i * 1e9) for i in range(3)]
    return [{"ticker": t, "total_assets": a, "roaa": 1.0 + i * 0.01}
            for i, (t, a) in enumerate(banks)]


# ── UX-P0-10: thin size tier merges with its neighbour ──────────────────

class TestPeerCohortTierMerge(unittest.TestCase):

    def test_money_center_merges_with_large_regional(self):
        cohort, label, own_n = peer_cohort("JPM", _universe())
        self.assertEqual(len(cohort), 18)
        self.assertEqual(label, f"{MC} + {LR}")
        self.assertEqual(own_n, 4)
        self.assertEqual({m["ticker"] for m in cohort},
                         {t for t, _ in _MC_BANKS} | {f"LR{i}" for i in range(14)})

    def test_percentile_context_meta_carries_merged_label_and_size(self):
        ctx = metric_percentile_context("JPM", _universe(), metric_keys=["roaa"])
        self.assertEqual(ctx["_meta"], {
            "tier": f"{MC} + {LR}", "cohort_size": 18, "mode": "size",
            "base_tier": MC, "base_tier_size": 4})
        self.assertEqual(ctx["roaa"]["n"], 18)
        self.assertEqual(ctx["roaa"]["out_of"], 18)

    def test_leaderboard_cohort_matches_ranked_cohort(self):
        # Peer Rank's leaderboard reads get_peer_group_for_bank — it must list
        # the same 18 banks the scorecard ranked against, not the 4-bank tier.
        self.assertEqual(len(get_peer_group_for_bank("JPM", _universe())), 18)

    def test_tier_at_or_above_minimum_is_unchanged(self):
        cohort, label, own_n = peer_cohort("LR5", _universe())
        self.assertEqual((len(cohort), label, own_n), (14, LR, 14))
        ctx = metric_percentile_context("RG3", _universe(), metric_keys=["roaa"])
        self.assertEqual(ctx["_meta"]["tier"], RG)
        self.assertEqual(ctx["_meta"]["cohort_size"], 10)
        self.assertEqual(ctx["_meta"]["base_tier"], RG)

    def test_exactly_min_cohort_does_not_merge(self):
        banks = [{"ticker": f"R{i}", "total_assets": 20e9 + i} for i in range(MIN_COHORT)]
        banks.append({"ticker": "LR", "total_assets": 200e9})
        cohort, label, _ = peer_cohort("R0", banks)
        self.assertEqual((len(cohort), label), (MIN_COHORT, RG))

    def test_smallest_tier_merges_up(self):
        cohort, label, own_n = peer_cohort("CM0", _universe())
        self.assertEqual((len(cohort), label, own_n), (13, f"{CM} + {RG}", 3))

    def test_no_merge_when_adjacent_tier_empty(self):
        only_mc = [{"ticker": t, "total_assets": a} for t, a in _MC_BANKS]
        cohort, label, own_n = peer_cohort("JPM", only_mc)
        self.assertEqual((len(cohort), label, own_n), (4, MC, 4))

    def test_business_mix_mode_never_merges(self):
        banks = [{"ticker": f"B{i}", "total_assets": 5e9} for i in range(3)]
        banks += [{"ticker": "BIG", "total_assets": 5e9, "cre_to_capital": 400}]
        cohort, label, own_n = peer_cohort("B0", banks, mode="mix")
        self.assertEqual((len(cohort), label, own_n), (3, "Diversified", 3))

    def test_unknown_ticker_is_empty(self):
        self.assertEqual(peer_cohort("ZZZ", _universe()), ([], None, 0))


class TestPeerRankRender(unittest.TestCase):
    """The page copy: merged caption; no stale watchlist dead end."""

    def _render(self, ticker, metrics, mode_label="Asset size"):
        import ui.peer_rank as pr
        st = MagicMock(name="st")

        def _radio(label, options, **k):
            return mode_label if k["key"].startswith("peerrank_mode") else options[0]

        st.radio.side_effect = _radio
        st.selectbox.side_effect = lambda label, options, **k: options[0]
        exports = []
        with mock.patch.object(pr, "st", st), \
             mock.patch.object(pr, "title_bar"), \
             mock.patch.object(pr, "get_name", lambda t: f"{t} Corp"), \
             mock.patch.object(pr, "table_export",
                               side_effect=lambda df, name, **k: exports.append((name, df, k))):
            pr.render_peer_rank(ticker, metrics)
        return st, exports

    def test_money_center_caption_names_both_tiers(self):
        st, exports = self._render("JPM", _universe())
        st.info.assert_not_called()
        caption = st.caption.call_args_list[0].args[0]
        self.assertIn("Ranked against **18** **Money-Center + Large Regional** peers", caption)
        self.assertIn("the Money-Center tier alone has only 4 banks", caption)
        self.assertNotIn("$", caption)  # no LaTeX hazard
        # The leaderboard export lists the same 18 banks.
        board = next(df for name, df, _ in exports if name.startswith("peer_leaderboard_"))
        self.assertEqual(len(board), 18)

    def test_unmerged_caption_escapes_dollar_signs(self):
        # "Large Regional ($100B-$1T)" carries two "$" — unescaped, Streamlit
        # renders the span between them as LaTeX.
        st, _ = self._render("LR5", _universe())
        caption = st.caption.call_args_list[0].args[0]
        self.assertIn(r"**14** tracked **Large Regional (\$100B-\$1T)** peers", caption)

    def test_too_few_peers_message_has_no_watchlist_dead_end(self):
        banks = [{"ticker": f"B{i}", "total_assets": 5e9, "roaa": 1.0 + i / 10}
                 for i in range(3)]
        st, _ = self._render("B0", banks, mode_label="Business mix")
        st.info.assert_called_once_with(
            "Too few business mix peers to rank this bank (n=3). Try the other peer set.")
        self.assertNotIn("watchlist", st.info.call_args.args[0])
        st.caption.assert_not_called()


# ── UX-P0-04: Compare default = last-opened bank + closest peers ─────────

class TestDefaultDisplayTickers(unittest.TestCase):

    def test_money_center_subject_fills_short_tier_from_scope(self):
        # MC tier has only 3 others → 3 MC + the 8 closest by assets from the
        # scope (the largest Large Regionals, $800B down to $450B).
        self.assertEqual(
            default_display_tickers(_universe(), "JPM"),
            ["JPM", "BAC", "C", "WFC"] + [f"LR{i}" for i in range(13, 5, -1)])

    def test_same_tier_preferred_over_closer_cross_tier_bank(self):
        # LR0 ($150B): RG9 ($65B, $85B away) is closer than LR2 ($250B, $100B
        # away), but the tier has 13 others, so all 11 picks stay in-tier.
        self.assertEqual(default_display_tickers(_universe(), "LR0"),
                         ["LR0"] + [f"LR{i}" for i in range(1, 12)])

    def test_picks_are_nearest_by_assets_both_directions(self):
        # LR7 ($500B): in-tier distances $50B (LR6, LR8), $100B (LR5, LR9),
        # $150B (LR4, LR10), $200B (LR3, LR11), $250B (LR2, LR12), $300B (LR1,
        # LR13), $350B (LR0). Equal distances break by ticker string ("LR10" <
        # "LR4"); the 11th pick is LR1 ("LR1" < "LR13"), so LR13 and LR0 drop.
        self.assertEqual(default_display_tickers(_universe(), "LR7"),
                         ["LR7", "LR6", "LR8", "LR5", "LR9", "LR10", "LR4",
                          "LR11", "LR3", "LR12", "LR2", "LR1"])

    def test_subject_absent_or_unset_is_empty(self):
        self.assertEqual(default_display_tickers(_universe(), "ZZZ"), [])
        self.assertEqual(default_display_tickers(_universe(), None), [])

    def test_small_scope_returns_everyone(self):
        scope = _universe()[:5]
        # BAC $3.3T: JPM $0.7T away, C $0.9T, WFC $1.6T (same tier), then LR0.
        self.assertEqual(default_display_tickers(scope, "BAC"),
                         ["BAC", "JPM", "C", "WFC", "LR0"])

    def test_banks_without_assets_are_never_peers(self):
        scope = [{"ticker": "A", "total_assets": 5e9},
                 {"ticker": "NOA", "total_assets": None},
                 {"ticker": "NAN", "total_assets": float("nan")},
                 {"ticker": "B", "total_assets": 6e9}]
        self.assertEqual(default_display_tickers(scope, "A"), ["A", "B"])
        # Subject without assets: closeness is undefined → itself only.
        self.assertEqual(default_display_tickers(scope, "NOA"), ["NOA"])


_PROMPT = ("Pick banks to compare — or open a bank on the Company page "
           "and it will be preselected here with its closest peers.")


class TestCompareDefaultRender(unittest.TestCase):

    def _run(self, cohort, last_bank=None, user_pick=None):
        import ui.peer_comparison as pc
        st = MagicMock(name="st")
        st.session_state = {} if last_bank is None else {"_last_company_bank": last_bank}
        st.columns.side_effect = lambda spec, *a, **k: [
            MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))]
        seen = {}

        def _multiselect(label, options, default=None, **k):
            if k.get("key") == "compare_display":
                seen["default"] = list(default or [])
                return list(default or []) if user_pick is None else user_pick
            return list(default or [])

        st.multiselect.side_effect = _multiselect
        st.selectbox.side_effect = lambda label, options, **k: (
            None if "index" in k and k["index"] is None else options[0])
        tickers = [m["ticker"] for m in cohort]
        with mock.patch.object(pc, "st", st), \
             mock.patch("ui.bank_scope.render_scope_sub",
                        return_value=(cohort, tickers, "All banks")), \
             mock.patch.object(pc, "_render_scorecard") as scorecard, \
             mock.patch.object(pc, "_render_highlights"), \
             mock.patch.object(pc, "lazy_tabs", return_value=None), \
             mock.patch.object(pc, "status_dot", return_value=""), \
             mock.patch.object(pc, "table_export"), \
             mock.patch("ui.chrome.ticker_linkcol", return_value={}), \
             mock.patch.object(pc, "get_name", lambda t: f"{t} Corp"):
            pc.render_peer_comparison(cohort)
        return st, seen, scorecard

    def test_no_bank_opened_prompts_instead_of_arbitrary_set(self):
        st, seen, scorecard = self._run(_universe())
        self.assertEqual(seen["default"], [])
        st.info.assert_called_once_with(_PROMPT)
        scorecard.assert_not_called()

    def test_last_opened_bank_preselected_with_closest_peers(self):
        st, seen, scorecard = self._run(_universe(), last_bank="JPM")
        expected = default_display_tickers(_universe(), "JPM")
        self.assertEqual(seen["default"], expected)
        st.info.assert_not_called()
        self.assertEqual(sorted(scorecard.call_args.args[1]), sorted(expected))

    def test_last_bank_outside_scope_prompts(self):
        scope = [m for m in _universe() if m["ticker"] != "JPM"]
        st, seen, scorecard = self._run(scope, last_bank="JPM")
        self.assertEqual(seen["default"], [])
        st.info.assert_called_once_with(_PROMPT)
        scorecard.assert_not_called()

    def test_cleared_selection_prompts_no_fallback(self):
        # The old `or cohort[:TABLE_CAP]` silently re-filled a cleared picker.
        st, _, scorecard = self._run(_universe(), last_bank="JPM", user_pick=[])
        st.info.assert_called_once_with(_PROMPT)
        scorecard.assert_not_called()

    def test_scope_within_cap_shows_everyone_without_picker(self):
        scope = _universe()[:5]
        st, seen, scorecard = self._run(scope)
        self.assertNotIn("default", seen)
        st.info.assert_not_called()
        self.assertEqual(sorted(scorecard.call_args.args[1]),
                         sorted(m["ticker"] for m in scope))


class TestCompanySubtabRecordsLastBank(unittest.TestCase):

    def test_render_company_subtab_records_ticker(self):
        import ui.company_nav as nav
        state = sys.modules["streamlit"].session_state
        sentinel = MagicMock(name="renderer")
        with mock.patch.dict(state, {}, clear=False), \
             mock.patch.dict(nav._RENDERERS, {"Peer Rank": sentinel}):
            self.assertTrue(nav.render_company_subtab("Peer Rank", "JPM", {}))
            self.assertEqual(state["_last_company_bank"], "JPM")
        sentinel.assert_called_once_with("JPM", {})


if __name__ == "__main__":
    unittest.main()
