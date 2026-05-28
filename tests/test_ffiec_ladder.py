"""
Unit tests for the FFIEC securities maturity ladder integration into
the phased rate sensitivity model.

Run:  python tests/test_ffiec_ladder.py
"""

from __future__ import annotations
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


def test_ladder_to_yearly_pace_basic():
    """Linear amortization within bucket → expected cumulative pace."""
    from data.ffiec_client import maturity_ladder_to_yearly_pace
    ladder = {
        "buckets": {
            "le_3mo": 0.10, "3mo_1y": 0.20, "1y_3y": 0.30,
            "3y_5y": 0.25, "5y_15y": 0.13, "gt_15y": 0.02,
        },
    }
    pace = maturity_ladder_to_yearly_pace(ladder)
    # Y1 = le_3mo + 3mo_1y = 0.30
    assert abs(pace[1] - 0.30) < 1e-6, pace
    # Y2 = Y1 + 1/2 × (1y-3y) = 0.45
    assert abs(pace[2] - 0.45) < 1e-6, pace
    # Y3 = Y2 + 1/2 × (1y-3y) = 0.60
    assert abs(pace[3] - 0.60) < 1e-6, pace
    # Y4 = Y3 + 1/2 × (3y-5y) = 0.725
    assert abs(pace[4] - 0.725) < 1e-6, pace
    # Y5 = Y4 + 1/2 × (3y-5y) = 0.85
    assert abs(pace[5] - 0.85) < 1e-6, pace
    print("PASS: ladder→pace basic linear amortization")


def test_ladder_to_yearly_pace_short_bank():
    """A bank with everything maturing in < 1 year — Y1 ≈ 100%."""
    from data.ffiec_client import maturity_ladder_to_yearly_pace
    ladder = {"buckets": {"le_3mo": 0.6, "3mo_1y": 0.4, "1y_3y": 0,
                          "3y_5y": 0, "5y_15y": 0, "gt_15y": 0}}
    pace = maturity_ladder_to_yearly_pace(ladder)
    assert abs(pace[1] - 1.0) < 1e-6
    assert abs(pace[5] - 1.0) < 1e-6
    print("PASS: short-duration bank")


def test_ladder_to_yearly_pace_long_bank():
    """All securities > 15 yrs → Y5 ≈ 0 (none has repriced)."""
    from data.ffiec_client import maturity_ladder_to_yearly_pace
    ladder = {"buckets": {"le_3mo": 0, "3mo_1y": 0, "1y_3y": 0,
                          "3y_5y": 0, "5y_15y": 0, "gt_15y": 1.0}}
    pace = maturity_ladder_to_yearly_pace(ladder)
    for y, v in pace.items():
        assert abs(v) < 1e-6, f"Year {y} should be 0 for all-long bank, got {v}"
    print("PASS: long-duration bank")


def test_compute_repricing_pace_with_ladder_vs_generic():
    """Bank-specific ladder should produce different pace than generic default."""
    from analysis.rate_sensitivity import compute_repricing_pace
    inputs = {"securities_share": 0.4, "loans_share": 0.6}
    short_ladder = {
        "buckets": {"le_3mo": 0.5, "3mo_1y": 0.5, "1y_3y": 0,
                    "3y_5y": 0, "5y_15y": 0, "gt_15y": 0}
    }
    pace_generic = compute_repricing_pace(inputs, floating_loan_share=0.3)
    pace_ladder = compute_repricing_pace(
        inputs, floating_loan_share=0.3, securities_ladder=short_ladder,
    )
    # With short-duration securities the bank-specific Y1 should be HIGHER
    # than the generic (~29%/yr) assumption — all securities reprice in Y1.
    assert pace_ladder[1] > pace_generic[1], (
        f"Short ladder Y1 ({pace_ladder[1]:.3f}) should beat generic ({pace_generic[1]:.3f})"
    )
    print(f"PASS: ladder Y1 {pace_ladder[1]:.3f} > generic Y1 {pace_generic[1]:.3f}")


def test_phased_scenario_with_ladder():
    """End-to-end run with ladder + generic — confirm shape + ladder_source field."""
    from analysis.rate_sensitivity import run_rate_sensitivity_phased
    fdic_latest = {
        "ASSET": 100e6, "NIMY": 3.0, "INTINCY": 5.0, "INTEXPY": 2.0,
        "DEP": 90e6, "DEPIDOM": 60e6, "DEPNIDOM": 30e6,
        "LNLSNET": 70e6, "SC": 30e6, "ERNAST": 100e6,
        "ITAX": 2.1, "PTAXNETINC": 10.0,
    }
    ladder = {
        "buckets": {"le_3mo": 0.4, "3mo_1y": 0.3, "1y_3y": 0.2,
                    "3y_5y": 0.05, "5y_15y": 0.04, "gt_15y": 0.01},
        "reporting_period": "03/31/2026",
        "weighted_avg_duration_years": 1.5,
        "total_usd": 30_000_000_000,
        "source": "ffiec",
    }
    r_ladder = run_rate_sensitivity_phased(fdic_latest, securities_ladder=ladder)
    r_generic = run_rate_sensitivity_phased(fdic_latest)
    assert r_ladder["ladder_source"] == "ffiec"
    assert r_generic["ladder_source"] == "generic"
    # Short-duration ladder → faster repricing in Y1
    assert r_ladder["repricing_pace"][1] > r_generic["repricing_pace"][1]
    print("PASS: end-to-end phased + ladder")


def main():
    test_ladder_to_yearly_pace_basic()
    test_ladder_to_yearly_pace_short_bank()
    test_ladder_to_yearly_pace_long_bank()
    test_compute_repricing_pace_with_ladder_vs_generic()
    test_phased_scenario_with_ladder()
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    main()
