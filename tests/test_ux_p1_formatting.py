"""
UX review 2026-09-24 P1 formatting items (owner decisions 2026-09-30).

  UX-P1-09  "billions" formatter printed "$0.0B"/"$0.1B" for sub-$1B values
            (Screen "Mkt Cap ($B)": BAFN, ATLO) — below $1B it scales to $M.
  UX-P1-12  (coordinator follow-up) a real non-zero amount that rounds to zero
            at the cell's resolution printed "$0.0M" (ALBY 30-89 past-dues
            $3K), indistinguishable from a reported zero — now "<$0.1M".
  UX-P1-11  negative zero: "-0.00%" / "-0%" / "(0.00%)" — a value that rounds
            to zero carries no sign.
  UX-P1-22  three negative conventions (+ UX-P2-10 "$-1.54"): the shared
            formatters put the sign BEFORE the "$" (market data / KPIs:
            "-$1.54"); statement and ratio TABLES show accounting parens
            ("($1.54)", "(4.27%)", "($9.30B)") — via neg_parens() server-side
            or the table component's leading-minus → parens pass.
  UX-P1-19  "n/a" mixed with "—" in one table: every on-screen absent cell is
            "—" (the Excel export keeps "n/a").
  UX-P1-20  literal "**= Common Equity Tier 1 capital**" markdown in the
            holdco capital-walk cells — subtotal rows are bolded by row style.
  UX-P1-21  two Annual/Quarterly controls on Company › Capital Adequacy — the
            statement table's toggle now drives the holdco block too.

Pure / stubbed — no network, no Streamlit server.
Run: python -m unittest tests.test_ux_p1_formatting -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

from utils.formatting import (  # noqa: E402
    fmt_dollars, format_value, neg_parens, pct, thou,
    usd_compact_from_thousands as usd_k,
)


class TestSubBillionScalesToMillions(unittest.TestCase):
    """UX-P1-09: config market_cap is format "billions", decimals 1."""

    def test_sub_billion_market_cap_is_millions(self):
        self.assertEqual(format_value(117_200_000, "billions", 1), "$117.2M")  # ATLO-size
        self.assertEqual(format_value(40_000_000, "billions", 1), "$40.0M")    # BAFN-size
        self.assertEqual(format_value(999_000_000, "billions", 1), "$999.0M")

    def test_billion_and_trillion_tiers_unchanged(self):
        self.assertEqual(format_value(1_000_000_000, "billions", 1), "$1.0B")
        self.assertEqual(format_value(2_100_000_000, "billions", 1), "$2.1B")
        self.assertEqual(format_value(1.5e12, "billions", 1), "$1.5T")

    def test_never_zero_point_b(self):
        for v in (1e6, 4.9e7, 5e7, 1.2e8, 9.4e8):
            out = format_value(v, "billions", 1)
            self.assertNotRegex(out, r"^\$0\.\dB$", v)
            self.assertTrue(out.endswith("M"), out)


class TestSubResolutionIsNotZero(unittest.TestCase):
    """UX-P1-12 follow-up: $3K in a $M (1 dp) cell is "<$0.1M", not "$0.0M"."""

    def test_millions_tiny_positive_and_negative(self):
        self.assertEqual(format_value(3_000, "millions", 1), "<$0.1M")     # ALBY PD 30-89
        self.assertEqual(format_value(-3_000, "millions", 1), ">-$0.1M")
        self.assertEqual(format_value(3_000, "millions", 2), "<$0.01M")

    def test_exact_zero_is_a_reported_zero(self):
        self.assertEqual(format_value(0, "millions", 1), "$0.0M")
        self.assertEqual(format_value(0.0, "billions", 1), "$0.0M")

    def test_at_resolution_rounds_normally(self):
        self.assertEqual(format_value(60_000, "millions", 1), "$0.1M")
        self.assertEqual(format_value(160_300_000, "millions", 1), "$160.3M")
        self.assertEqual(format_value(2.5e9, "millions", 1), "$2.5B")

    def test_billions_formatter_same_rule(self):
        self.assertEqual(format_value(3_000, "billions", 1), "<$0.1M")
        self.assertEqual(format_value(-3_000, "billions", 1), ">-$0.1M")


class TestNegativeZero(unittest.TestCase):
    """UX-P1-11: Compare NCO "-0.00%", Trends ROAA "-0.00%", statement
    "(0.00%)" — a value that rounds to zero prints unsigned."""

    def test_pct_helpers(self):
        self.assertEqual(pct(-0.004), "0.00%")
        self.assertEqual(pct(-0.004, 1), "0.0%")
        self.assertEqual(format_value(-0.004, "pct", 2), "0.00%")
        self.assertEqual(format_value(-0.3, "pct", 0), "0%")
        # Still negative once rounded → keeps its sign.
        self.assertEqual(pct(-0.006), "-0.01%")
        self.assertEqual(format_value(-0.53, "pct", 2), "-0.53%")

    def test_parens_never_wrap_a_zero(self):
        self.assertEqual(neg_parens(pct(-0.004)), "0.00%")         # not "(0.00%)"

    def test_other_signed_formatters(self):
        self.assertEqual(thou(-0.4), "0")
        self.assertEqual(usd_k(-0.0004), "$0")                     # -$0.40
        self.assertEqual(fmt_dollars(-0.4), "$0")
        self.assertEqual(format_value(-0.001, "currency", 2), "$0.00")
        self.assertEqual(format_value(-0.001, "ratio", 2), "0.00x")
        self.assertEqual(format_value(-0.001, "number", 2), "0.00")
        self.assertEqual(format_value(-40_000, "millions", 1), ">-$0.1M")


class TestNegativeConventions(unittest.TestCase):
    """UX-P1-22 / UX-P2-10: sign before the "$"; parens for tables."""

    def test_market_convention_sign_before_dollar(self):
        self.assertEqual(format_value(-1.54, "currency", 2), "-$1.54")   # Screen EPS
        self.assertEqual(usd_k(-9_300_000), "-$9.30B")                   # was "$-9.30B"
        self.assertEqual(usd_k(-843), "-$843K")
        self.assertEqual(format_value(-2.5e9, "billions", 1), "-$2.5B")
        self.assertEqual(format_value(-5e6, "millions", 1), "-$5.0M")
        self.assertEqual(fmt_dollars(-1.2e9), "-$1.20B")

    def test_no_formatter_ever_emits_dollar_minus(self):
        for v in (-0.5, -1.54, -843.0, -12_345.0, -2.5e6, -9.3e9, -1.1e12):
            outs = [usd_k(v), fmt_dollars(v)] + [
                format_value(v, f, 2) for f in
                ("currency", "millions", "billions", "dollars_auto")]
            for out in outs:
                self.assertNotIn("$-", out, (v, out))

    def test_neg_parens(self):
        self.assertEqual(neg_parens("-$9.30B"), "($9.30B)")
        self.assertEqual(neg_parens("-4.27%"), "(4.27%)")
        self.assertEqual(neg_parens("-$1.54"), "($1.54)")
        self.assertEqual(neg_parens("$1.54"), "$1.54")
        self.assertEqual(neg_parens("—"), "—")          # em dash is not a minus
        self.assertIsNone(neg_parens(None))

    def test_component_parens_pass_sees_the_shared_output(self):
        """Templated FDIC statements / Financial Highlights / RC-R walk render
        negatives through _build_component's JS: a td.val whose text matches
        /^-[\\d$.,]/ becomes "(…)". "$-9.30B" never matched — the root of
        UX-P2-10 on the Income Statement. Pin the contract: the formatter
        outputs those tables use match the component's own regex."""
        from ui.financial_highlights import _build_component, _dollars_ps, _ratio_pct
        html = _build_component("", "", {}, "e", None, "https://x")
        m = re.search(r"if \(/(.+?)/\.test\(t\)\)", html)
        self.assertIsNotNone(m, "component's leading-minus → parens pass is gone")
        neg_re = re.compile(m.group(1))
        for out in (usd_k(-9_300_000), pct(-4.27), _dollars_ps(-1.54),
                    _ratio_pct(-1, 50)):
            self.assertRegex(out, neg_re, out)
        self.assertEqual(_dollars_ps(-1.54), "-$1.54")
        self.assertNotRegex(pct(-0.004), neg_re)         # a zero is not wrapped


class TestStatementCells(unittest.TestCase):
    """ui/financials_statements: Company-Reported tables emit parens
    server-side; the per-page _fmt copies are one _cr_hl_fmt."""

    @classmethod
    def setUpClass(cls):
        import ui.financials_statements as fs
        cls.fs = fs

    def test_cr_usd_parens(self):
        f = self.fs._cr_usd
        self.assertEqual(f(-9_300_000_000), "($9.30B)")
        self.assertEqual(f(673_000_000), "$673.0M")
        self.assertEqual(f(None), "")

    def test_cr_hl_fmt_kinds(self):
        f = self.fs._cr_hl_fmt
        self.assertIsNone(f(None, "usd"))                  # callers drop all-None rows
        self.assertEqual(f(-2_500_000, "usd"), "($2.5M)")
        self.assertEqual(f(-1.54, "eps"), "($1.54)")       # was "$(1.54)"
        self.assertEqual(f(2.07, "eps"), "$2.07")
        self.assertEqual(f(-0.0427, "pct2"), "(4.27%)")
        self.assertEqual(f(0.0112, "pct2"), "1.12%")
        self.assertEqual(f(-0.0000004, "pct2"), "0.00%")   # no "(0.00%)"
        self.assertEqual(f(1.5, "x"), "1.50x")

    def test_templated_helpers(self):
        v = self.fs._psd(-1.54)
        self.assertEqual((str(v), v.raw), ("-$1.54", -1.54))   # was "$-1.54"
        self.assertEqual(str(self.fs._pctv(-0.001)), "0.00%")
        self.assertEqual(str(self.fs._usd(-9_300_000)), "-$9.30B")


class TestHoldcoCapitalBlock(unittest.TestCase):
    """ui/capital_dynamics holdco table + walk (Regions-shaped fixture, the
    same values tests/test_render_smoke pins)."""

    META = {"form": "10-K", "date": "2026-02-24", "accession": "acc", "doc": "rf.htm"}
    CAP = {"2025-12-31": {
        "cet1_ratio": 0.1089, "t1_ratio": 0.1199, "total_ratio": 0.1389,
        "lev_ratio": None, "cet1_cap": 13.49e9, "t1_cap": 14.859e9,
        "tier2_cap": 2.346e9, "total_cap": 17.205e9, "rwa": 123.9e9,
        "_anchored": True, "_walk_reconciles": True,
        "_walk": {"common_equity": 19.00e9, "goodwill": 5.733e9,
                  "other_intangibles": 0.140e9, "aoci": -1.535e9,
                  "subordinated_debt": None, "intangibles": 5.873e9,
                  "aoci_treatment": "included"}}}

    @classmethod
    def setUpClass(cls):
        import ui.capital_dynamics as cd
        cls.cd = cd

    def _render(self, period=None):
        import data.sec_filing_scraper as sfs
        cd, st = self.cd, self.cd.st
        md, radios = [], []

        def _radio(label, options=None, **k):
            radios.append(k.get("key"))
            return "Annual"
        res = {"meta": self.META, "capital": self.CAP}
        with mock.patch.object(st, "markdown", lambda s, *a, **k: md.append(str(s)),
                               create=True), \
             mock.patch.object(st, "caption", lambda s, *a, **k: md.append(str(s)),
                               create=True), \
             mock.patch.object(st, "info", lambda *a, **k: None, create=True), \
             mock.patch.object(st, "radio", _radio, create=True), \
             mock.patch.object(cd, "get_cik", lambda t: 1281761), \
             mock.patch.object(cd, "get_fdic_cert", lambda t: 12368), \
             mock.patch.object(cd, "get_name", lambda t: "Regions"), \
             mock.patch.object(cd, "table_export", lambda *a, **k: None), \
             mock.patch("data.ir_provider.fresh_capital", lambda cik: None), \
             mock.patch.object(sfs, "holdco_capital_for", lambda cik, cert=None: res):
            if period is None:
                cd._render_holdco_capital("RF")
            else:
                cd._render_holdco_capital("RF", period=period)
        return "\n".join(md), radios

    def test_walk_subtotals_bold_without_markdown_asterisks(self):
        h, _ = self._render()
        self.assertIn("Regulatory capital walk", h)
        self.assertNotIn("**", h)                                   # UX-P1-20
        for lab in ("= Common Equity Tier 1 capital", "= Tier 1 capital",
                    "= Tier 2 capital", "= Total capital"):
            self.assertIn(f'<tr style="font-weight:700"><td>{lab}</td>', h, lab)
        self.assertIn("<tr><td>Total common equity</td>", h)       # component: not bold

    def test_absent_is_em_dash_and_deductions_in_parens(self):
        h, _ = self._render()
        self.assertNotIn("n/a</td>", h)                             # UX-P1-19
        self.assertIn("<td>—</td>", h)          # leverage ratio / sub-debt untagged
        # Less: goodwill 5.733B → "($5.73B)", red .neg, "$" LaTeX-escaped.
        self.assertIn('<td class="neg">(&#36;5.73B)</td>', h)
        self.assertNotIn("&#36;-", h)                               # never "$-5.73B"
        self.assertIn("10.89%", h)
        self.assertIn("&#36;13.49B", h)

    def test_one_period_control_on_the_templated_page(self):
        """UX-P1-21: given the page's period, the block renders no radio of
        its own; standalone (CR › Regulatory Capital) it keeps its toggle."""
        _, radios = self._render(period="Annual")
        self.assertEqual(radios, [])
        _, radios = self._render()
        self.assertEqual(radios, ["hc_period_RF"])

    def test_templated_page_no_longer_renders_the_block(self):
        """Superseded by UX-P2-24 (owner decision 2026-10-05): the holdco
        block lives only on Company Reported › Regulatory Capital, so the
        Templated page has no second period control at all — it links there
        (pinned in tests/test_ux_p2_owner_calls.py)."""
        import inspect
        src = inspect.getsource(self.cd.render_capital_dynamics)
        self.assertNotIn("_render_holdco_capital(", src)
        self.assertIn("holdco_capital_pointer_html(ticker)", src)


if __name__ == "__main__":
    unittest.main()
