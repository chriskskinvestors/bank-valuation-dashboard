"""(2026-08-20) A filer's NI-to-common must not be a MULTIPLE of its own
net income for the same period — that is a units/scale tagging error.

Found while measuring the effect of dropping the fair-P/TBV cap: KFFB
(Kentucky First Federal, CIK 1297341) tags
NetIncomeLossAvailableToCommonStockholdersBasic 1000x too large in several
periods — $181,000,000 for FY2025 where NetIncomeLoss says $181,000, and the
same 1000x error at 2026-03-31 — while other quarters are correct. The TTM
sum came to $757,648,000 for a thrift that earned $1.4M, driving holdco
ROATCE to 1555% and (uncapped) a $1,247 fair value on a $4.77 stock. The
2.5x multiple cap had been hiding it behind a plausible-looking number.

The gate is SAME-PERIOD and order-of-magnitude on purpose. A first cut
compared the two TTM sums and over-fired on 13 healthy banks: ABCB tags NIAC
only annually (its "TTM" is calendar-2025 while NI's is the trailing four
quarters through Q2-2026) and HOMB's series expose different durations — a
few percent of WINDOW-COMPOSITION difference, not corruption. Gating those
would have silently moved ABCB's ROATCE 14.0% → 12.5%. A legitimate excess
also exists (preferred repurchased below carrying value adds to income
available to common under ASC 260) — but it is a percentage, never a
multiple.

With no preferred outstanding NIAC == NI by identity (honest substitute);
with preferred present the split is unknowable → None, and
compute_roatce_holdco renders n/a (cardinal rule).

Run: python -m unittest tests.test_niac_scale_gate
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

from data import sec_client  # noqa: E402

FILED = date.today().isoformat()


def _q(end_days_ago: int, val: float, span: int = 92):
    end = date.today() - timedelta(days=end_days_ago)
    return {"end": end.isoformat(), "start": (end - timedelta(days=span)).isoformat(),
            "val": val, "form": "10-Q", "filed": FILED, "fp": "Q1"}


def _facts(ni_vals, niac_vals, preferred=False, extra_niac_ytd=None,
           stale_corrupt=False):
    """Four quarters of NI and NIAC; optional preferred evidence, an extra
    NIAC-only YTD duration (the ABCB/HOMB window-composition shape), and an
    optional OLD 1000x-corrupt period (the GSBC/NBHC/FNWD shape)."""
    ends = (50, 142, 234, 326)
    ug = {
        "NetIncomeLoss": {"units": {"USD": [_q(d, v) for d, v in zip(ends, ni_vals)]}},
        "NetIncomeLossAvailableToCommonStockholdersBasic":
            {"units": {"USD": [_q(d, v) for d, v in zip(ends, niac_vals)]}},
        "StockholdersEquity": {"units": {"USD": [
            {"end": (date.today() - timedelta(days=50)).isoformat(),
             "val": 49_658_000, "form": "10-Q", "filed": FILED}]}},
    }
    if stale_corrupt:
        # A period well outside the TTM window: 1000x wrong, but it cannot
        # affect today's figure, so it must not change today's figure.
        old_end = date.today() - timedelta(days=2200)
        old = {"end": old_end.isoformat(),
               "start": (old_end - timedelta(days=92)).isoformat(),
               "form": "10-Q", "filed": FILED, "fp": "Q3"}
        ug["NetIncomeLoss"]["units"]["USD"].append({**old, "val": 19_732})
        ug["NetIncomeLossAvailableToCommonStockholdersBasic"]["units"]["USD"].append(
            {**old, "val": 19_732_000})
    if extra_niac_ytd is not None:
        end = date.today() - timedelta(days=50)
        ug["NetIncomeLossAvailableToCommonStockholdersBasic"]["units"]["USD"].append(
            {"end": end.isoformat(),
             "start": (end - timedelta(days=180)).isoformat(),
             "val": extra_niac_ytd, "form": "10-Q", "filed": FILED, "fp": "Q2"})
    if preferred:
        ug["PreferredStockValue"] = {"units": {"USD": [
            {"end": (date.today() - timedelta(days=50)).isoformat(),
             "val": 10_000_000, "form": "10-Q", "filed": FILED}]}}
    return {"facts": {"us-gaap": ug, "dei": {}}}


def _fund(facts):
    with patch.object(sec_client, "fetch_company_facts", return_value=facts):
        return sec_client.get_latest_fundamentals(1)


class TestNiacScaleGate(unittest.TestCase):
    def test_kffb_shape_no_preferred_substitutes_total_ni(self):
        """NIAC 1000x inflated on some quarters → identity violated → the
        total (which equals NIAC by identity here) serves."""
        f = _fund(_facts(ni_vals=[344_000, 304_000, 400_000, 357_000],
                         niac_vals=[344_000, 304_000, 400_000_000, 357_000]))
        self.assertEqual(f["net_income_to_common_ttm"], f["net_income"])
        self.assertLess(f["net_income_to_common_ttm"], 2_000_000)

    def test_preferred_present_renders_na(self):
        """With preferred outstanding the common split is unknowable from a
        corrupt series — n/a, never a guess."""
        f = _fund(_facts(ni_vals=[344_000, 304_000, 400_000, 357_000],
                         niac_vals=[344_000, 304_000, 400_000_000, 357_000],
                         preferred=True))
        self.assertIsNone(f["net_income_to_common_ttm"])

    def test_window_composition_difference_does_not_fire(self):
        """The ABCB/HOMB shape that broke the first cut: every SAME-PERIOD
        pair agrees, but the TTM sums differ a few percent because the two
        concepts expose different durations. Must pass through untouched."""
        f = _fund(_facts(ni_vals=[344_000, 304_000, 400_000, 357_000],
                         niac_vals=[344_000, 304_000, 400_000, 357_000],
                         extra_niac_ytd=1_500_000))
        self.assertEqual(f["net_income_to_common_ttm"], 1_405_000)

    def test_asc260_percentage_excess_does_not_fire(self):
        """Preferred repurchased below carrying value legitimately ADDS to
        income available to common — a percentage, never a multiple."""
        f = _fund(_facts(ni_vals=[1_000_000] * 4,
                         niac_vals=[1_080_000] * 4))
        self.assertEqual(f["net_income_to_common_ttm"], 4_320_000)

    def test_historical_corruption_does_not_touch_current_value(self):
        """GSBC (2019), NBHC (2011) and FNWD (2017) all carry the same 1000x
        error in OLD periods while their current figures are clean —
        substituting there would degrade a good number."""
        f = _fund(_facts(ni_vals=[344_000, 304_000, 400_000, 357_000],
                         niac_vals=[344_000, 304_000, 400_000, 357_000],
                         stale_corrupt=True))
        self.assertEqual(f["net_income_to_common_ttm"], 1_405_000)

    def test_healthy_filer_untouched(self):
        """The normal case: NIAC slightly BELOW NI (preferred dividends) —
        must pass through exactly (JPM/BAC/C/WFC shape)."""
        f = _fund(_facts(ni_vals=[1_000_000, 1_000_000, 1_000_000, 1_000_000],
                         niac_vals=[950_000, 950_000, 950_000, 950_000]))
        self.assertEqual(f["net_income_to_common_ttm"], 3_800_000)
        self.assertEqual(f["net_income"], 4_000_000)

    def test_equal_values_pass(self):
        """No preferred: NIAC == NI exactly is the identity, not a violation."""
        f = _fund(_facts(ni_vals=[500_000] * 4, niac_vals=[500_000] * 4))
        self.assertEqual(f["net_income_to_common_ttm"], 2_000_000)


if __name__ == "__main__":
    unittest.main()
