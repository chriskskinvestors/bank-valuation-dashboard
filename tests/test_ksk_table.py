"""ui/tables.ksk_table — the house table renderer (polish lane 2)."""
import unittest

from tests import _streamlit_stub  # noqa: F401

import pandas as pd

from ui.tables import ksk_table_html


class TestKskTableHtml(unittest.TestCase):
    def _df(self):
        return pd.DataFrame([
            {"Window": "3M", "Net": "+$1.2M", "Ratio": "2 : 1"},
            {"Window": "6M", "Net": "-$540.0K", "Ratio": "—"},
            {"Window": "1Y", "Net": "—", "Ratio": "1 : 3"},
        ])

    def test_sign_coloring_only_on_signed_cols(self):
        h = ksk_table_html(self._df(), signed_cols=("Net",))
        self.assertIn('class="num pos">+$1.2M', h)
        self.assertIn('class="num neg">-$540.0K', h)
        # the dash never gets colored
        self.assertNotIn('neg">—', h)
        self.assertNotIn('pos">—', h)

    def test_alignment_autodetect(self):
        h = ksk_table_html(self._df())
        self.assertIn('<th class="txt">Window</th>', h)
        self.assertIn('<th class="num">Net</th>', h)

    def test_html_is_escaped(self):
        df = pd.DataFrame([{"A": "<img src=x onerror=1>", "B": "5 & 6"}])
        h = ksk_table_html(df)
        self.assertNotIn("<img", h)
        self.assertIn("&lt;img", h)
        self.assertIn("5 &amp; 6", h)

    def test_scroll_container_when_capped(self):
        h = ksk_table_html(self._df(), max_height_px=640)
        self.assertIn("max-height:640px;overflow-y:auto", h)
        self.assertNotIn("max-height", ksk_table_html(self._df()))

    def test_html_cols_insert_verbatim_others_stay_escaped(self):
        # Linked-ticker conversion (deposit market-share tables, owner
        # formatting report 2026-09-16): the Ticker anchor passes through,
        # every other cell keeps its escaping.
        df = pd.DataFrame([{
            "Rank": "1",
            "Ticker": '<a href="?s=Company&bank=BAC" target="_self">BAC</a>',
            "Bank": "Bank <of> America",
        }])
        h = ksk_table_html(df, html_cols=("Ticker",))
        self.assertIn('<a href="?s=Company&bank=BAC"', h)
        self.assertIn("Bank &lt;of&gt; America", h)
        # html_cols never right-align, whatever their content looks like
        self.assertIn('<th class="txt">Ticker</th>', h)


if __name__ == "__main__":
    unittest.main()


class TestNanHandling(unittest.TestCase):
    def test_nan_and_none_render_as_dash(self):
        import numpy as np
        df = pd.DataFrame([{"A": "x", "B": np.nan, "C": None},
                           {"A": "y", "B": 1.25, "C": "ok"}])
        h = ksk_table_html(df)
        self.assertNotIn("nan", h.lower().replace("financial", ""))
        self.assertEqual(2, h.count(">—<"))
        self.assertIn(">1.25<", h)
