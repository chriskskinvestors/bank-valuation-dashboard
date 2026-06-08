"""
Unit tests that lock in the MATH of every core valuation/profitability
formula with synthetic inputs and hand-computed expected outputs.

These guard against silent regressions in the computation layer — the
golden-dataset test checks live *inputs*, but nothing previously verified
the formulas themselves. Every assertion here is independent of live data.

Run: PYTHONIOENCODING=utf-8 python -X utf8 tests/test_metric_formulas.py
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from analysis.valuation import (  # noqa: E402
    compute_pe_ratio, compute_pb_ratio, compute_ptbv_ratio,
    compute_dividend_yield, compute_market_cap, compute_change_pct,
    _infer_quarter, _annualize_ytd, _derive_quarterly_value,
    compute_roatce, compute_roatce_holdco, compute_4q_avg, compute_roatce_4q,
    compute_roatce_blended, compute_fair_ptbv, compute_ptbv_discount,
    compute_fair_value_price,
)

EPS = 1e-9


def approx(a, b, tol=1e-6):
    return a is not None and abs(a - b) <= tol


def test_price_ratios():
    assert approx(compute_pe_ratio(80.0, 8.0), 10.0)
    assert compute_pe_ratio(80.0, 0.0) is None        # eps <= 0
    assert compute_pe_ratio(80.0, -1.0) is None
    assert compute_pe_ratio(None, 8.0) is None
    assert approx(compute_pb_ratio(12.0, 10.0), 1.2)
    assert compute_pb_ratio(12.0, 0.0) is None
    assert approx(compute_ptbv_ratio(15.0, 10.0), 1.5)
    assert compute_ptbv_ratio(15.0, 0.0) is None
    print("PASS: price ratios (P/E, P/B, P/TBV)")


def test_yield_mktcap_change():
    assert approx(compute_dividend_yield(100.0, 3.0), 3.0)   # 3/100 * 100
    assert compute_dividend_yield(0.0, 3.0) is None          # price <= 0
    assert compute_dividend_yield(100.0, None) is None
    assert approx(compute_market_cap(50.0, 1e8), 5e9)
    assert compute_market_cap(None, 1e8) is None
    assert approx(compute_change_pct(110.0, 100.0), 10.0)
    assert approx(compute_change_pct(90.0, 100.0), -10.0)
    assert compute_change_pct(110.0, 0.0) is None
    print("PASS: dividend yield, market cap, change %")


def test_infer_quarter():
    assert _infer_quarter("20260331") == 1
    assert _infer_quarter("2026-06-30") == 2
    assert _infer_quarter("20260930") == 3
    assert _infer_quarter("2026-12-31") == 4
    assert _infer_quarter(None) is None
    import datetime as dt
    assert _infer_quarter(dt.date(2026, 9, 30)) == 3
    print("PASS: _infer_quarter")


def test_annualize_ytd():
    assert approx(_annualize_ytd(100.0, 1), 400.0)   # Q1 ×4
    assert approx(_annualize_ytd(100.0, 2), 200.0)   # Q2 ×2
    assert approx(_annualize_ytd(90.0, 3), 120.0)    # Q3 ×4/3
    assert approx(_annualize_ytd(100.0, 4), 100.0)   # Q4 ×1
    assert approx(_annualize_ytd(100.0, None), 100.0)  # unknown → unchanged
    assert _annualize_ytd(None, 1) is None
    print("PASS: _annualize_ytd")


def _hist_4q():
    """Desc-sorted 4-quarter YTD history, all in fiscal 2025."""
    return [
        {"REPDTE": "20251231", "NETINC": 400.0, "EQTOT": 10000.0},  # Q4 YTD
        {"REPDTE": "20250930", "NETINC": 300.0, "EQTOT": 9900.0},   # Q3 YTD
        {"REPDTE": "20250630", "NETINC": 200.0, "EQTOT": 9800.0},   # Q2 YTD
        {"REPDTE": "20250331", "NETINC": 100.0, "EQTOT": 9700.0},   # Q1 YTD
    ]


def test_derive_quarterly_value():
    h = _hist_4q()
    assert approx(_derive_quarterly_value("NETINC", h, 0), 100.0)  # Q4: 400-300
    assert approx(_derive_quarterly_value("NETINC", h, 1), 100.0)  # Q3: 300-200
    assert approx(_derive_quarterly_value("NETINC", h, 2), 100.0)  # Q2: 200-100
    assert approx(_derive_quarterly_value("NETINC", h, 3), 100.0)  # Q1: YTD itself
    print("PASS: _derive_quarterly_value (YTD → single quarter)")


def test_roatce_variants():
    # Sub-bank, Q4: NI 1000 (×1), TCE = 10000 - 2000 = 8000 → 12.5%
    fdic_q4 = {"NETINC": 1000.0, "EQTOT": 10000.0, "INTANGW": 2000.0,
               "REPDTE": "20261231"}
    assert approx(compute_roatce(fdic_q4), 12.5)
    # Same bank reported at Q1 (NI 250 YTD ×4 = 1000) → identical 12.5%
    fdic_q1 = dict(fdic_q4, NETINC=250.0, REPDTE="20260331")
    assert approx(compute_roatce(fdic_q1), 12.5)
    # Negative TCE → None
    assert compute_roatce({"NETINC": 100.0, "EQTOT": 100.0, "INTANGW": 200.0,
                           "REPDTE": "20261231"}) is None

    # HoldCo: NI 1000, equity 10000, gw 1000, intang 1000 → TCE 8000 → 12.5%
    sec = {"net_income": 1000.0, "book_value_total": 10000.0,
           "goodwill": 1000.0, "intangibles": 1000.0}
    assert approx(compute_roatce_holdco(sec), 12.5)
    assert compute_roatce_holdco({}) is None
    print("PASS: ROATCE (sub-bank + holdco)")


def test_roatce_4q_and_avg():
    h = _hist_4q()
    # 4Q NI = 100+100+100+100 = 400; avg TCE = (10000+9900+9800+9700)/4 = 9850
    # → 400 / 9850 * 100 = 4.06091%
    assert approx(compute_roatce_4q(h), 400.0 / 9850.0 * 100, tol=1e-4)
    # 4q average of a ratio field
    hr = [{"NIMY": 3.0}, {"NIMY": 3.2}, {"NIMY": 2.8}, {"NIMY": 3.0}]
    assert approx(compute_4q_avg(hr, "NIMY"), 3.0)
    assert compute_4q_avg([], "NIMY") is None
    print("PASS: ROATCE 4Q + 4Q average")


def test_fair_value_chain():
    # Blended: 0.75*16 + 0.25*12 = 15
    assert approx(compute_roatce_blended(12.0, 16.0), 15.0)
    assert approx(compute_roatce_blended(None, 16.0), 16.0)   # fallback to 4q
    assert approx(compute_roatce_blended(12.0, None), 12.0)   # fallback to current
    assert compute_roatce_blended(None, None) is None
    # Fair P/TBV = roatce / 10
    assert approx(compute_fair_ptbv(15.0), 1.5)
    assert approx(compute_fair_ptbv(-3.0), 0.0)               # floor at 0
    assert compute_fair_ptbv(None) is None
    # Discount: (1.5 - 1.2)/1.5 * 100 = 20% undervalued
    assert approx(compute_ptbv_discount(1.2, 1.5), 20.0)
    assert approx(compute_ptbv_discount(1.3, 1.0), -30.0)     # overvalued
    assert compute_ptbv_discount(1.2, 0.0) is None
    # Fair price = fair_ptbv * tbvps
    assert approx(compute_fair_value_price(1.5, 10.0), 15.0)
    assert compute_fair_value_price(1.5, 0.0) is None
    print("PASS: fair-value chain (blended → fair P/TBV → discount → price)")


def main():
    test_price_ratios()
    test_yield_mktcap_change()
    test_infer_quarter()
    test_annualize_ytd()
    test_derive_quarterly_value()
    test_roatce_variants()
    test_roatce_4q_and_avg()
    test_fair_value_chain()
    print("\nALL METRIC-FORMULA TESTS PASSED")


if __name__ == "__main__":
    main()
