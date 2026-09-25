"""UX-P0-07: Compensation — gate the FMP feed against the proxy's own XBRL.

FMP's executive-compensation feed was verified WRONG for JPM against the
DEF 14A filed 2026-04-06 (accession 0000019617-26-000096), Summary
Compensation Table:
  James Dimon  FY2025 40,632,724 · FY2024 37,683,462 · FY2023 35,960,259
  Erdoes FY2025 29,528,934 · Barnum FY2025 18,212,926
FMP:  Dimon 43.00M / 39.00M / 36.00M; Erdoes 31.00M; Barnum 19.50M.
Owner rule: an FMP fiscal year is shown only when some FMP total that year
is within 1% of a Pay-versus-Performance PEO SCT total (proxy inline XBRL)
for the same year; otherwise n/a + the reason. FY2023 passes (0.11%).
"""
import html
import unittest
from unittest import mock

import ui.compensation as C
from ui.compensation import verified_years

DIMON, ERDOES, BARNUM = ("James Dimon, Chairman and CEO",
                         "Mary Callahan Erdoes, CEO AWM",
                         "Jeremy Barnum, CFO")


def _fmp(name, year, total, salary=1_500_000):
    return {"name_position": name, "year": year, "salary": salary,
            "bonus": 0.0, "stock_award": 0.0, "option_award": 0.0,
            "incentive": total - salary, "other": 0.0, "total": total,
            "filing_date": "2026-04-06",
            "link": "https://www.sec.gov/Archives/edgar/data/19617/"}


# FMP as served for JPM (verified 2026-09-25). The FY2022 row is SYNTHETIC —
# it exists only to exercise a year the PvP stub does not cover.
JPM_FMP = [
    _fmp(DIMON, 2025, 43_000_000), _fmp(ERDOES, 2025, 31_000_000),
    _fmp(BARNUM, 2025, 19_500_000),
    _fmp(DIMON, 2024, 39_000_000), _fmp(DIMON, 2023, 36_000_000),
    _fmp(DIMON, 2022, 34_500_000),
]


def _pvp_row(fy_end, peo_total):
    return {"fy_end": fy_end, "peo_total": peo_total, "peo_paid": [],
            "non_peo_avg_total": None, "non_peo_avg_paid": None, "tsr": None,
            "peer_tsr": None, "net_income": None, "co_selected": None}


# JPM PvP, the proxy's own XBRL (PEO SCT totals from the 2026 DEF 14A).
JPM_PVP = {"years": [_pvp_row("2025-12-31", [40_632_724]),
                     _pvp_row("2024-12-31", [37_683_462]),
                     _pvp_row("2023-12-31", [35_960_259])],
           "multi_peo": False, "filed": "2026-04-06", "source_url": None}


class TestVerifiedYears(unittest.TestCase):

    def test_jpm_fmp_vs_proxy_xbrl(self):
        self.assertEqual(verified_years(JPM_FMP, JPM_PVP),
                         {2025: "mismatch",      # 43.00M vs 40.63M = 5.8%
                          2024: "mismatch",      # 39.00M vs 37.68M = 3.5%
                          2023: "ok",            # 36.00M vs 35.96M = 0.11%
                          2022: "unverifiable"})  # no PvP row

    def test_no_pvp_is_unverifiable_never_ok(self):
        for pvp in (None, {}, {"years": []}):
            self.assertEqual(set(verified_years(JPM_FMP, pvp).values()),
                             {"unverifiable"})

    def test_empty_peo_list_is_unverifiable(self):
        pvp = {"years": [_pvp_row("2025-12-31", [])]}
        self.assertEqual(verified_years([_fmp(DIMON, 2025, 1e7)], pvp),
                         {2025: "unverifiable"})

    def test_tolerance_is_one_percent_relative_to_xbrl(self):
        pvp = {"years": [_pvp_row("2025-12-31", [10_000_000])]}
        self.assertEqual(verified_years([_fmp(DIMON, 2025, 10_099_000)], pvp),
                         {2025: "ok"})          # 0.99%
        self.assertEqual(verified_years([_fmp(DIMON, 2025, 10_101_000)], pvp),
                         {2025: "mismatch"})    # 1.01%
        self.assertEqual(verified_years([_fmp(DIMON, 2025, 9_899_000)], pvp),
                         {2025: "mismatch"})    # -1.01%

    def test_any_row_matching_any_peo_verifies_transition_year(self):
        # Two PEOs tagged (CEO transition); the incoming CEO's FMP row matches
        # the second value, the outgoing one is absent from FMP.
        pvp = {"years": [_pvp_row("2024-12-31", [3_000_000, 8_000_000])]}
        rows = [_fmp("New CEO", 2024, 8_010_000), _fmp("CFO", 2024, 2_000_000)]
        self.assertEqual(verified_years(rows, pvp), {2024: "ok"})

    def test_none_total_rows_do_not_verify(self):
        pvp = {"years": [_pvp_row("2025-12-31", [10_000_000])]}
        row = _fmp(DIMON, 2025, 10_000_000)
        row["total"] = None
        self.assertEqual(verified_years([row], pvp), {2025: "mismatch"})


class _Page:
    """Records st.markdown / st.caption from the page module's own `st` and
    ui.states' (empty_state) — patched per module, never sys.modules, so the
    stub-swap hazard can't bind a stale stub."""

    def __init__(self):
        self.out: list[str] = []
        self.st = mock.MagicMock()
        self.st.markdown.side_effect = lambda s, **k: self.out.append(html.unescape(s))
        self.st.caption.side_effect = lambda s, **k: self.out.append(html.unescape(s))

    def render(self, fmp_rows, pvp):
        import ui.states as S
        with mock.patch.object(C, "st", self.st), \
                mock.patch.object(S, "st", self.st), \
                mock.patch.object(C, "title_bar", lambda *a, **k: None), \
                mock.patch.object(C, "get_name", lambda t: "JPMorgan Chase"), \
                mock.patch.object(C, "get_executive_compensation",
                                  lambda t: fmp_rows), \
                mock.patch("data.bank_mapping.get_cik", lambda t: 19617), \
                mock.patch("data.sec_pvp.get_pay_versus_performance",
                           lambda cik: pvp):
            C.render_compensation("JPM")
        return "\n".join(self.out)


class TestCompensationRender(unittest.TestCase):

    def test_jpm_unverified_fmp_figures_never_render(self):
        text = _Page().render(JPM_FMP, JPM_PVP)
        for wrong in ("$43.00M", "$39.00M", "$31.00M", "$19.50M", "$34.50M",
                      "$41.50M"):
            self.assertNotIn(wrong, text)
        self.assertIn("The compensation feed (FMP) disagrees with the proxy's "
                      "own XBRL for FY2025 — not shown.", text)
        self.assertIn("The compensation feed (FMP) disagrees with the proxy's "
                      "own XBRL for FY2024 — not shown.", text)
        self.assertIn("FY2022 cannot be verified against the proxy's XBRL — "
                      "not shown.", text)
        self.assertIn("$36.00M", text)   # FY2023 verified → shown in the trend
        self.assertIn("n/a", text)
        self.assertIn("$40.63M", text)   # Pay versus Performance unchanged

    def test_verified_latest_year_renders_summary_with_honest_caption(self):
        pvp = {"years": [_pvp_row("2025-12-31", [43_100_000])]}  # 0.23%
        text = _Page().render([_fmp(DIMON, 2025, 43_000_000)], pvp)
        self.assertIn("$43.00M", text)
        self.assertIn("$41.50M", text)   # component cells render when verified
        self.assertIn("from the FMP compensation feed", text)
        self.assertIn("verified against the CEO total in the proxy's own XBRL "
                      "(Pay versus Performance)", text)
        self.assertNotIn("not shown", text)

    def test_no_pvp_hides_every_fmp_year(self):
        text = _Page().render(JPM_FMP, None)
        for v in ("$43.00M", "$39.00M", "$36.00M", "$34.50M"):
            self.assertNotIn(v, text)
        self.assertIn("FY2025 cannot be verified against the proxy's XBRL — "
                      "not shown.", text)


if __name__ == "__main__":
    unittest.main()
