"""
Unit tests for data/validation.py reconciliation, composition, and
staleness rules. These are the accuracy tripwires that catch unit errors
(thousands vs dollars), wrong CIK mappings, and stale data before a bad
number ever reaches the dashboard.

Run: PYTHONIOENCODING=utf-8 python -X utf8 tests/test_validation_rules.py
"""
from __future__ import annotations
import sys
import datetime as _dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.validation import (  # noqa: E402
    cross_check_assets, check_loan_composition, check_deposit_composition,
    _coerce_date_str, check_staleness, validate_bank_metrics,
)


def test_assets_reconciliation():
    # Within band → no flag
    assert cross_check_assets(100e9, 98e9) == []
    # HoldCo assets << sub-bank → error (wrong-mapping / unit signature)
    assert any(f.severity == "error" for f in cross_check_assets(40e9, 98e9))
    # HoldCo materially above sub-bank → warning
    assert any(f.severity == "warning" for f in cross_check_assets(150e9, 98e9))
    # Unit error (one side left in thousands) → flagged (non-empty)
    assert cross_check_assets(100e9, 98e6)
    # Missing inputs → safe no-op
    assert cross_check_assets(None, 98e9) == []
    assert cross_check_assets(100e9, None) == []
    assert cross_check_assets(0, 98e9) == []
    print("PASS: assets reconciliation")


def test_loan_composition():
    good = {"LNLSGR": 1000, "LNRE": 600, "LNCI": 250, "LNCON": 100, "LNAG": 20}
    assert check_loan_composition(good) == []
    # A single segment mis-scaled ×1000 exceeds gross → error
    bad = dict(good, LNCI=250_000)
    assert any(f.severity == "error" for f in check_loan_composition(bad))
    # Missing gross → safe no-op
    assert check_loan_composition({}) == []
    print("PASS: loan composition")


def test_deposit_composition():
    gd = {"DEP": 1000, "DEPINS": 700, "DEPUNINS": 290,
          "DEPIDOM": 600, "DEPNIDOM": 350}
    assert check_deposit_composition(gd) == []
    # Mis-scaled component → sum >105% of total → flagged
    assert any(f.field == "deposit_composition"
               for f in check_deposit_composition(dict(gd, DEPUNINS=290_000)))
    # Missing component → insured+uninsured well below total → flagged
    miss = {"DEP": 1000, "DEPINS": 500, "DEPUNINS": 200,
            "DEPIDOM": 600, "DEPNIDOM": 350}
    assert any("only" in f.message for f in check_deposit_composition(miss))
    print("PASS: deposit composition")


def test_date_coercion_and_staleness():
    assert _coerce_date_str("2026-03-31") == "2026-03-31"
    assert _coerce_date_str("20260331") == "2026-03-31"
    assert _coerce_date_str(_dt.date(2026, 3, 31)) == "2026-03-31"
    assert _coerce_date_str(None) is None
    assert _coerce_date_str("garbage") is None
    # Very old → finding; fresh → none
    assert check_staleness("2000-01-01", 135, "fdic") is not None
    fresh = (_dt.date.today() - _dt.timedelta(days=10)).strftime("%Y-%m-%d")
    assert check_staleness(fresh, 135, "fdic") is None
    print("PASS: date coercion + staleness")


def test_end_to_end_validate():
    sec = {"book_value_total": 10e9, "net_income": 1e9, "total_assets_sec": 100e9,
           "shares_outstanding": 5e8, "sec_as_of": "2026-03-31"}
    # Clean bank → no reconciliation/composition/staleness findings
    fresh = (_dt.date.today() - _dt.timedelta(days=20)).strftime("%Y-%m-%d")
    fdic_ok = {"EQTOT": 9_800_000, "ASSET": 98_000_000, "NETINC": 250_000,
               "REPDTE": fresh, "LNLSGR": 60_000_000, "LNRE": 40_000_000,
               "LNCI": 15_000_000, "DEP": 80_000_000, "DEPINS": 56_000_000,
               "DEPUNINS": 23_000_000, "DEPIDOM": 50_000_000, "DEPNIDOM": 29_000_000}
    sec_fresh = dict(sec, sec_as_of=fresh)
    assert validate_bank_metrics({}, sec_data=sec_fresh, fdic_data=fdic_ok) == []
    # Unit-error bank (one loan field ×1000) → caught
    fdic_bad = dict(fdic_ok, LNCI=15_000_000_000)
    fields = {x.field for x in validate_bank_metrics(
        {}, sec_data=sec_fresh, fdic_data=fdic_bad)}
    assert "loan_composition" in fields
    print("PASS: end-to-end validate_bank_metrics")


def main():
    test_assets_reconciliation()
    test_loan_composition()
    test_deposit_composition()
    test_date_coercion_and_staleness()
    test_end_to_end_validate()
    print("\nALL VALIDATION-RULE TESTS PASSED")


if __name__ == "__main__":
    main()
