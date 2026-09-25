"""Templated Income Statement, Quarterly view — single-quarter columns
(docs/REVIEW-2026-09-24-numbers.md P0-1).

FDIC files every income field calendar-YTD. The Quarterly view used to render
those raw fields under quarter labels, so a "Q2 '26" column was the six-month
figure (JPM net income showed $31.34B where the quarter is $17.37B). Pins:

  * every flow field in a Quarterly column is YTD(q) − YTD(q−1), Q1 as filed;
  * a column whose prior quarter is not in the history renders dead, never
    the YTD figure;
  * computed kinds (diff / ppnr / etr / noniother) see the single-quarter
    inputs, so NII and the effective tax rate are the quarter's;
  * the Annual view is untouched (12/31 YTD = the full year);
  * a field outside the flow set (a balance) is shown as filed;
  * FFIEC RI-E detail joined to the same columns is de-cumulated too;
  * the flow-field set covers every field _INCOME references (structural).

Hand-computed fixture ($000): NETINC YTD Q1 100,000 / Q2 250,000 / Q3
400,000 / Q4 520,000 → quarters 100,000 / 150,000 / 150,000 / 120,000.
"""
import re
import types
import unittest
from unittest import mock

import pandas as pd

import ui.export as ex
import ui.financials_statements as FS


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stub_st(period, captions):
    st = types.SimpleNamespace()
    st.radio = lambda *a, **k: period
    st.caption = lambda msg, *a, **k: captions.append(str(msg))
    st.markdown = st.warning = st.info = lambda *a, **k: None
    st.spinner = lambda *a, **k: _Ctx()
    st.columns = lambda spec, **k: [_Ctx() for _ in (spec if isinstance(spec, list) else range(spec))]
    st.container = lambda *a, **k: _Ctx()
    return st


def _q(date, **f):
    return {"REPDTE": date, **f}


# Two years of quarters; 2025-Q1 is the oldest row so it has no prior quarter.
HIST = pd.DataFrame([
    _q("2025-03-31", NETINC=100_000, INTINC=300_000, EINTEXP=100_000, NONII=50_000,
       NONIX=120_000, ITAX=30_000, PTAXNETINC=130_000, ISERCHG=10_000, ASSET=9_000_000),
    _q("2025-06-30", NETINC=250_000, INTINC=620_000, EINTEXP=210_000, NONII=110_000,
       NONIX=250_000, ITAX=70_000, PTAXNETINC=320_000, ISERCHG=21_000, ASSET=9_100_000),
    _q("2025-09-30", NETINC=400_000, INTINC=950_000, EINTEXP=330_000, NONII=170_000,
       NONIX=390_000, ITAX=110_000, PTAXNETINC=510_000, ISERCHG=33_000, ASSET=9_200_000),
    _q("2025-12-31", NETINC=520_000, INTINC=1_300_000, EINTEXP=460_000, NONII=240_000,
       NONIX=540_000, ITAX=140_000, PTAXNETINC=660_000, ISERCHG=46_000, ASSET=9_300_000),
    _q("2026-03-31", NETINC=130_000, INTINC=360_000, EINTEXP=130_000, NONII=65_000,
       NONIX=150_000, ITAX=40_000, PTAXNETINC=170_000, ISERCHG=12_000, ASSET=9_400_000),
    _q("2026-06-30", NETINC=290_000, INTINC=740_000, EINTEXP=270_000, NONII=135_000,
       NONIX=310_000, ITAX=85_000, PTAXNETINC=375_000, ISERCHG=25_000, ASSET=9_500_000),
])

SPEC = [
    ("Income", [
        ("Net income", "dollar", "NETINC"),
        ("Net interest income", "diff", "INTINC", "EINTEXP"),
        ("Pre-provision net revenue", "ppnr"),
        ("Effective tax rate", "etr"),
        ("Other non-interest income", "noniother", "ISERCHG"),
        ("Other non-interest expense", "dollar", "EOTHNINT"),
    ]),
    ("Balance", [
        ("Total Assets", "dollar", "ASSET"),
    ]),
]
FLOWS = frozenset({"NETINC", "INTINC", "EINTEXP", "NONII", "NONIX", "ITAX",
                   "PTAXNETINC", "ISERCHG", "EOTHNINT"})

_RI_DETAIL = [
    {"reporting_period": "2026-03-31", "tax_exempt_loan_income": 4_000,
     "tax_exempt_sec_income": 1_000},
    {"reporting_period": "2026-06-30", "tax_exempt_loan_income": 9_000,
     "tax_exempt_sec_income": 2_500},
]
_RIE_DETAIL = [
    {"reporting_period": "2026-03-31", "data_processing": 20_000, "legal": None,
     "expense_writeins": [{"label": "Software", "value": 10_000, "value_usd": 10_000_000}]},
    {"reporting_period": "2026-06-30", "data_processing": 45_000, "legal": 3_000,
     "expense_writeins": [{"label": "Software", "value": 30_000, "value_usd": 30_000_000}]},
]


def _render(period, spec=SPEC, flow_fields=FLOWS, with_ri=False, hist=HIST):
    captions, html = [], []
    fake_ex = types.SimpleNamespace(download_button=lambda *a, **k: None,
                                    container=lambda *a, **k: _Ctx())
    fake_components = types.SimpleNamespace(html=lambda h, **k: html.append(h))
    import data.loaders as loaders
    import data.call_report_store as crs
    with mock.patch.object(FS, "st", _stub_st(period, captions)), \
         mock.patch.object(FS, "components", fake_components), \
         mock.patch.object(FS, "get_bank_info",
                           lambda t: {"name": "Test Bancorp", "fdic_cert": 4242, "cik": None}), \
         mock.patch.object(FS, "_render_statement_trends", lambda *a, **k: None), \
         mock.patch.object(loaders, "load_fdic_hist_df", lambda t, q: hist.copy()), \
         mock.patch.object(crs, "get_stored_ri_detail", lambda cert, quarters=40: _RI_DETAIL), \
         mock.patch.object(crs, "get_stored_rie_detail", lambda cert, quarters=40: _RIE_DETAIL), \
         mock.patch.object(ex, "st", fake_ex):
        FS.render_statement("TBNK", "inc", "Income Statement", spec, trends=[],
                            with_ri=with_ri, flow_fields=flow_fields)
    return "".join(html), captions


def _rows(html):
    """{label → [cell texts]} from the rendered statement table."""
    out = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S):
        m = re.search(r'<td class="lbl">(.*?)</td>', tr, flags=re.S)
        if not m:
            continue
        label = re.sub(r"<.*?>", "", m.group(1)).replace("&nbsp;", "").strip()
        vals = re.findall(r'<td class="val[^"]*"[^>]*>(.*?)</td>', tr, flags=re.S)
        out[label] = [re.sub(r"<.*?>", "", v).strip() for v in vals]
    return out


DEAD = {"—", "n/a", ""}


class TestQuarterlyColumnsAreSingleQuarter(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        html, cls.captions = _render("Quarterly")
        cls.rows = _rows(html)

    def test_dollar_row_is_decumulated(self):
        # Q1'25 has no prior quarter in the history → dead; then the quarters.
        ni = self.rows["Net income"]
        self.assertEqual(ni[0], "$100.0M")                  # Q1: YTD is the quarter
        self.assertEqual(ni[1:4], ["$150.0M", "$150.0M", "$120.0M"])
        self.assertEqual(ni[4], "$130.0M")                  # Q1'26 as filed
        self.assertEqual(ni[5], "$160.0M")                  # 290 − 130, not 290
        self.assertNotIn("$290.0M", ni)                     # the old YTD figure never renders

    def test_diff_kind_sees_single_quarter_inputs(self):
        # Q2'26 NII = (740−360) − (270−130) = 380 − 140 = 240, not 740−270=470.
        self.assertEqual(self.rows["Net interest income"][5], "$240.0M")

    def test_ppnr_uses_single_quarter_inputs(self):
        # Q2'26: NII 240 + NONII (135−65=70) − NONIX (310−150=160) = 150.
        self.assertEqual(self.rows["Pre-provision net revenue"][5], "$150.0M")

    def test_effective_tax_rate_is_the_quarters(self):
        # Q2'26: tax (85−40=45) ÷ pretax (375−170=205) = 21.95%, not 85/375=22.67%.
        self.assertEqual(self.rows["Effective tax rate"][5], "21.95%")

    def test_noniother_residual_is_single_quarter(self):
        # Q2'26: NONII 70 − ISERCHG (25−12=13) = 57.
        self.assertEqual(self.rows["Other non-interest income"][5], "$57.0M")

    def test_balance_field_is_shown_as_filed(self):
        self.assertEqual(self.rows["Total Assets"][5], "$9.50B")

    def test_missing_prior_quarter_renders_dead_not_ytd(self):
        # A Q2 column whose Q1 is absent: cut the history at 2025-06-30.
        html, _ = _render("Quarterly", hist=HIST[HIST["REPDTE"] != "2025-03-31"].copy())
        ni = _rows(html)["Net income"]
        self.assertIn(ni[0], DEAD)                          # never "$250.0M"
        self.assertEqual(ni[1], "$150.0M")                  # Q3 still de-cumulates

    def test_caption_says_single_quarter(self):
        self.assertTrue(any("SINGLE quarter" in c for c in self.captions), self.captions)


class TestAnnualViewUnchanged(unittest.TestCase):

    def test_fy_column_is_the_ytd_figure(self):
        html, captions = _render("Annual")
        rows = _rows(html)
        self.assertEqual(rows["Net income"], ["$520.0M"])   # 12/31 YTD = full year
        self.assertEqual(rows["Net interest income"], ["$840.0M"])
        self.assertEqual(rows["Effective tax rate"], ["21.21%"])
        self.assertFalse(any("SINGLE quarter" in c for c in captions))

    def test_point_in_time_statement_never_decumulates(self):
        # flow_fields=None (balance sheet, capital): Quarterly shows as filed.
        html, captions = _render("Quarterly", flow_fields=None)
        self.assertEqual(_rows(html)["Net income"][5], "$290.0M")
        self.assertFalse(any("SINGLE quarter" in c for c in captions))


class TestRiDetailDecumulated(unittest.TestCase):

    def test_rie_rows_follow_the_column_span(self):
        html, _ = _render("Quarterly", with_ri=True)
        rows = _rows(html)
        dp = rows["Data processing expenses"]
        self.assertEqual(dp[4], "$20.0M")                   # Q1'26 as filed
        self.assertEqual(dp[5], "$25.0M")                   # 45 − 20, not 45
        wi = rows["Write-in: Software"]
        self.assertEqual(wi[4], "$10.0M")
        self.assertEqual(wi[5], "$20.0M")                   # 30 − 10
        # Legal: below threshold in Q1 (None) → Q2 cannot be derived cleanly.
        self.assertIn(rows["Legal expense"][5], DEAD)
        # Columns without stored detail stay dead.
        self.assertTrue(all(v in DEAD for v in dp[:4]), dp)

    def test_rie_rows_annual_view_as_filed(self):
        # FY view joins 12/31 detail as filed (no diffing); the fixture stores
        # none for 2025-12-31, so the sub-block is not inserted at all
        # (existing behavior: no itemization in the window → no rows).
        html, _ = _render("Annual", with_ri=True)
        rows = _rows(html)
        self.assertNotIn("Data processing expenses", rows)
        self.assertEqual(rows["Net income"], ["$520.0M"])


class TestDecumHelpers(unittest.TestCase):

    def test_decum_record_hand_computed(self):
        hist = {pd.Timestamp(r["REPDTE"]).normalize(): r for r in HIST.to_dict("records")}
        q2 = hist[pd.Timestamp("2026-06-30")]
        out = FS._decum_record(q2, {"NETINC", "INTINC"}, hist)
        self.assertEqual(out["NETINC"], 160_000)
        self.assertEqual(out["INTINC"], 380_000)
        self.assertEqual(out["ASSET"], 9_500_000)           # not in fields → untouched
        self.assertEqual(q2["NETINC"], 290_000)             # input record not mutated

    def test_decum_record_missing_prior_is_none(self):
        rec = {"REPDTE": "2025-12-31", "NETINC": 520_000}
        self.assertIsNone(FS._decum_record(rec, {"NETINC"}, {})["NETINC"])

    def test_decum_detail_q1_passthrough_and_missing_prior(self):
        cur = {"reporting_period": "2026-03-31", "data_processing": 20_000}
        self.assertIs(FS._decum_detail(cur, None, "2026-03-31"), cur)
        cur2 = {"reporting_period": "2026-06-30", "data_processing": 45_000}
        self.assertIsNone(FS._decum_detail(cur2, None, "2026-06-30"))

    def test_decum_detail_differences_numbers_and_writeins(self):
        out = FS._decum_detail(_RIE_DETAIL[1], _RIE_DETAIL[0], "2026-06-30")
        self.assertEqual(out["data_processing"], 25_000)
        self.assertIsNone(out["legal"])                     # prior below threshold
        self.assertEqual(out["reporting_period"], "2026-06-30")
        self.assertEqual(out["expense_writeins"][0]["value"], 20_000)
        self.assertEqual(out["expense_writeins"][0]["value_usd"], 20_000_000)


class TestFlowFieldSetCoversSpec(unittest.TestCase):

    def test_every_income_field_is_a_flow_field(self):
        missing = FS._fdic_fields_in_spec(FS._INCOME) - FS._INCOME_FLOW_FIELDS
        self.assertEqual(missing, frozenset(), missing)

    def test_computed_kind_inputs_are_flow_fields(self):
        for fl in ("INTINC", "EINTEXP", "NONII", "NONIX", "ITAX", "PTAXNETINC"):
            self.assertIn(fl, FS._INCOME_FLOW_FIELDS)

    def test_income_page_passes_flow_fields(self):
        from pathlib import Path
        src = (Path(__file__).parent.parent / "ui" / "financials_statements.py").read_text(encoding="utf-8")
        call = src[src.index('render_statement(ticker, "is", "Income Statement"'):]
        self.assertIn("flow_fields=_INCOME_FLOW_FIELDS", call[:400])


if __name__ == "__main__":
    unittest.main()
