"""UX-review P2 polish on Home + Market & Macro (docs/REVIEW-2026-09-24-ux.md).

UX-P2-07  Spread names used three dashes: "2Y − 10Y" (Home), "2Y – 10Y"
          (Rates), "HY - IG" (Credit). PR #228 moved the boards/charts to the
          Home form (U+2212 with spaces); the Regime card and its caption still
          read "2Y−10Y" / "3M−10Y" (no spaces). One form everywhere now.

Run: python -m unittest tests.test_ux_p2_home_macro -v
"""
from __future__ import annotations

import io
import re
import sys
import tokenize
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

import pandas as pd  # noqa: E402

# A spread leg: a tenor (2Y, 3M, 10Y, 30Y) or a named rate/credit leg.
_LEG = r"(?:\d+[YM]|HY|IG|BBB|FF)"
# Any dash between two legs that is NOT the house form " − " (U+2212, spaced).
_BAD_SPREAD = re.compile(
    rf"\b{_LEG}(?:\s*[-–—]\s*|−(?! )|(?<! )−)\s*{_LEG}\b")
_GOOD_SPREAD = re.compile(rf"\b{_LEG} − {_LEG}\b")


def _string_literals(path: Path) -> list[tuple[int, str]]:
    """Every string literal (incl. f-strings) in a module — what can reach the
    screen; comments are excluded."""
    src = path.read_text(encoding="utf-8")
    return [(tok.start[0], tok.string)
            for tok in tokenize.generate_tokens(io.StringIO(src).readline)
            if tok.type == tokenize.STRING]


class TestOneSpreadDash(unittest.TestCase):
    """UX-P2-07: every spread label in ui/home.py and ui/macro.py is
    'short − long' with U+2212 and spaces."""

    def test_no_other_dash_between_spread_legs(self):
        bad = []
        for rel in ("ui/home.py", "ui/macro.py"):
            for line, s in _string_literals(REPO_ROOT / rel):
                if _BAD_SPREAD.search(s):
                    bad.append(f"{rel}:{line}: {s[:80]}")
        self.assertEqual(bad, [], "spread labels not in the 'A − B' form")

    def test_house_form_present_in_both_modules(self):
        for rel in ("ui/home.py", "ui/macro.py"):
            lits = " ".join(s for _l, s in _string_literals(REPO_ROOT / rel))
            self.assertIn("2Y − 10Y", lits, rel)
            self.assertIn("3M − 10Y", lits, rel)

    def test_pattern_catches_the_reported_variants(self):
        # The three forms from the review + the Regime residue all fail.
        for s in ("2Y – 10Y", "HY - IG", "2Y−10Y", "3M−10Y"):
            self.assertRegex(s, _BAD_SPREAD)
        self.assertNotRegex("2Y − 10Y", _BAD_SPREAD)
        self.assertRegex("HY − IG", _GOOD_SPREAD)

    def test_regime_card_and_caption_use_house_form(self):
        from ui import macro
        st = MagicMock()
        # FRED T10Y2Y / T10Y3M are long − short; the card shows short − long.
        vals = {"T10Y2Y": 0.50, "T10Y3M": -0.25, "BAMLH0A0HYM2": 3.10,
                "DFF": 4.33}
        with patch.object(macro, "st", st), \
             patch.object(macro, "recession_probability",
                          return_value={"level": "low", "score": 10,
                                        "factors": []}), \
             patch.object(macro, "latest_value", side_effect=vals.get), \
             patch.object(macro, "fetch_series",
                          return_value=pd.DataFrame(columns=["date", "value"])):
            macro._render_regime()
        md = " ".join(str(c.args[0]) for c in st.markdown.call_args_list
                      if c.args)
        self.assertIn("2Y − 10Y -0.50pp · 3M − 10Y +0.25pp", md)
        self.assertNotIn("2Y−10Y", md)
        cap = " ".join(str(c.args[0]) for c in st.caption.call_args_list
                       if c.args)
        self.assertIn("Curve: 2Y − 10Y / 3M − 10Y shape", cap)


def _render_regime_md(vals, prior=None):
    """Render the Regime card with patched FRED values; returns the markdown.
    `prior` is a {series_id: value} map served as the 90/180-day-ago reading
    (fetch_series returns a 2-point frame: prior long ago, latest today)."""
    from ui import macro
    st = MagicMock()
    prior = prior or {}

    def _series(sid, years=2):
        if sid not in prior:
            return pd.DataFrame(columns=["date", "value"])
        return pd.DataFrame({
            "date": pd.to_datetime(["2020-01-01", "2026-10-01"]),
            "value": [prior[sid], vals.get(sid)]})

    with patch.object(macro, "st", st), \
         patch.object(macro, "recession_probability",
                      return_value={"level": "low", "score": 10,
                                    "factors": []}), \
         patch.object(macro, "latest_value", side_effect=vals.get), \
         patch.object(macro, "fetch_series", side_effect=_series):
        macro._render_regime()
    return " ".join(str(c.args[0]) for c in st.markdown.call_args_list
                    if c.args)


def _regime_rows(md):
    """{dimension: (state, detail)} parsed from the Regime card table."""
    body = md[md.index("<tbody>"):]
    rows = re.findall(r"<tr><td>([^<]+)</td><td[^>]*>(?:<span[^>]*></span>)?"
                      r"([^<]*)</td><td[^>]*>([^<]*)</td></tr>", body)
    return {dim: (state, detail) for dim, state, detail in rows}


class TestRegimeAbsentAndZeroSign(unittest.TestCase):
    """Owner rules 2026-09-30: an absent value renders "—" (was "n/a"), and a
    value that rounds to zero at its display precision carries no sign
    ("0.00", never "+0.00"/"-0.00")."""

    def test_all_absent_renders_dash(self):
        rows = _regime_rows(_render_regime_md({}))
        self.assertEqual(rows, {"Yield Curve": ("—", "—"),
                                "Credit": ("—", "—"),
                                "Fed Path": ("—", "—")})

    def test_no_na_token_anywhere_on_the_card(self):
        self.assertNotIn("n/a", _render_regime_md({}))

    def test_curve_spread_rounding_to_zero_is_unsigned(self):
        # -0.004 negated → +0.004 → "+0.00" before; 0.004 → "-0.00" before.
        md = _render_regime_md({"T10Y2Y": -0.004, "T10Y3M": 0.004,
                                "BAMLH0A0HYM2": 3.10, "DFF": 4.33})
        detail = _regime_rows(md)["Yield Curve"][1]
        self.assertEqual(detail, "2Y − 10Y 0.00pp · 3M − 10Y 0.00pp")

    def test_curve_spread_nonzero_keeps_sign(self):
        md = _render_regime_md({"T10Y2Y": -0.006, "T10Y3M": 0.006,
                                "BAMLH0A0HYM2": 3.10, "DFF": 4.33})
        detail = _regime_rows(md)["Yield Curve"][1]
        self.assertEqual(detail, "2Y − 10Y +0.01pp · 3M − 10Y -0.01pp")

    def test_fed_path_change_rounding_to_zero_is_unsigned(self):
        for prior in (4.334, 4.326):   # change -0.004 / +0.004
            md = _render_regime_md({"T10Y2Y": 0.5, "T10Y3M": 0.5,
                                    "BAMLH0A0HYM2": 3.10, "DFF": 4.33},
                                   prior={"DFF": prior})
            detail = _regime_rows(md)["Fed Path"][1]
            self.assertEqual(detail, "Fed Funds 4.33% · 0.00pp / 6mo", prior)

    def test_fed_path_change_nonzero_keeps_sign(self):
        md = _render_regime_md({"T10Y2Y": 0.5, "T10Y3M": 0.5,
                                "BAMLH0A0HYM2": 3.10, "DFF": 4.33},
                               prior={"DFF": 4.58})
        self.assertEqual(_regime_rows(md)["Fed Path"][1],
                         "Fed Funds 4.33% · -0.25pp / 6mo")


class TestMacroAbsentIsDash(unittest.TestCase):
    """Owner rule (2026-09-30): absent → "—" on screen. The Macro formatters
    and the credit-regime line still printed "n/a" after the P1 wave."""

    def test_formatters(self):
        import ui.macro as macro
        for out in (macro._fmt_vol(None), macro._fmt_level(None, "yoy_pct"),
                    macro._fmt_delta({"delta": None, "basis": "yoy_pct"})):
            self.assertIn("—", out)
            self.assertNotIn("n/a", out)

    def test_no_on_screen_na_literal_left(self):
        import ast
        import inspect
        import ui.macro as macro
        tree = ast.parse(inspect.getsource(macro))
        lits = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and ("n/a" in n.value)]
        # Only the two comparisons that MAP a helper's "n/a" label to "—".
        self.assertTrue(all(v == "n/a" for v in lits), lits)


if __name__ == "__main__":
    unittest.main()
