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


if __name__ == "__main__":
    unittest.main()
