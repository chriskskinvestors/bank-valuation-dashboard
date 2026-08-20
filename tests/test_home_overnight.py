"""
Tests for Home's "Overnight & Breaking" strip (docs/HOME-MACRO-PLAN.md
priority #1): categorized topic news (Macro / Geopolitical / Domestic /
Markets) rendered above the grid from the events store's topic feeds.

Pins (pure builder ui.home._af_overnight_table):
  • every category empty (or no store) -> "" — the section collapses,
    never an empty box or fabricated items
  • a populated category renders its headlines; an empty sibling category
    renders an honest "no items" line
  • headlines are HTML-escaped; a URL renders a new-tab link, no URL
    renders plain text
  • the per-category cap holds (5 items)
  • source name + relative time land in the meta cell
"""
import unittest

from tests import _streamlit_stub

_streamlit_stub.install()

import ui.home as home  # noqa: E402


def _item(head, url="https://example.com/a", src="Reuters", when=""):
    return {"headline": head, "url": url, "source_name": src,
            "published_at": when}


class TestOvernightTable(unittest.TestCase):

    def test_all_empty_collapses(self):
        self.assertEqual(home._af_overnight_table({}), "")
        self.assertEqual(home._af_overnight_table(
            {"macro": [], "geopolitical": [], "domestic": [], "markets": []}),
            "")

    def test_populated_and_empty_categories(self):
        h = home._af_overnight_table({"macro": [_item("Fed holds rates")]})
        self.assertIn("Overnight &amp; Breaking", h)
        self.assertIn("Fed holds rates", h)
        self.assertIn("Macro", h)
        self.assertIn("Geopolitical", h)                 # column still shown
        self.assertIn("no items in the last 24h", h)     # honest empty state
        self.assertIn("Reuters", h)                      # source in meta

    def test_headline_escaped_and_linked(self):
        h = home._af_overnight_table(
            {"markets": [_item('S&P <b>"rallies"</b>')]})
        self.assertIn("S&amp;P", h)
        self.assertIn("&lt;b&gt;", h)
        self.assertNotIn("<b>", h)
        self.assertIn('href="https://example.com/a"', h)
        self.assertIn('target="_blank"', h)

    def test_urlless_item_renders_plain(self):
        h = home._af_overnight_table({"domestic": [_item("Shutdown talks", url="")]})
        self.assertIn("Shutdown talks", h)
        self.assertNotIn("fstory", h)                    # no dead link

    def test_per_category_cap(self):
        items = [_item(f"Headline {i}") for i in range(9)]
        h = home._af_overnight_table({"macro": items})
        self.assertEqual(h.count('class="ovnitem"'), 5)


if __name__ == "__main__":
    unittest.main()
