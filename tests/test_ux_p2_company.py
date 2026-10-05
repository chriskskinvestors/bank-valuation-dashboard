"""UX-review P2 polish on the Company pages (docs/REVIEW-2026-09-24-ux.md).

UX-P2-14  Corporate Profile showed "$829.30B" (Market Data) beside "$829.3B"
          (Valuation) for one market cap — both now use the METRICS spec.
UX-P2-15  Recent Filings listed a Form 4 as "4 — 2026-09-10".
UX-P2-18  People Summary mixed "BACON ASHLEY" with "Leopold Robin" and listed
          the issuer itself ("JPMORGAN CHASE & CO — Insider") as a person.
UX-P2-26  Recent Documents labelled "10-Q (10-Q)".
UX-P2-28  Market Share / Branch List printed "5141" branches.

Run: python -m unittest tests.test_ux_p2_company
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests import _streamlit_stub  # noqa: E402

_streamlit_stub.install()

import ui.bank_detail as bd  # noqa: E402
import ui.people_summary as ps  # noqa: E402
import ui.recent_documents as rd  # noqa: E402
from config import METRICS_BY_KEY  # noqa: E402
from utils.formatting import format_value  # noqa: E402


class TestMarketCapOneFormatter(unittest.TestCase):
    """UX-P2-14: the Market Data block reads the same METRICS spec the
    Valuation table (and the Screen) use, so one page shows one string."""

    def test_market_data_spec_is_the_metrics_spec(self):
        self.assertIs(bd._MC_FMT, METRICS_BY_KEY["market_cap"])

    def test_same_string_both_blocks(self):
        m = METRICS_BY_KEY["market_cap"]
        s = format_value(829_300_000_000, m["format"], m["decimals"])
        self.assertEqual(s, "$829.3B")
        # The old local _usd_b produced the 2-dp variant the review flagged.
        self.assertEqual(bd._usd_b(829_300_000_000), "$829.30B")


class TestFormLabel(unittest.TestCase):
    """UX-P2-15: bare-number EDGAR codes read as "Form N"; lettered codes
    are self-describing."""

    def test_bare_number_forms(self):
        self.assertEqual(bd._form_label("4"), "Form 4")
        self.assertEqual(bd._form_label("3"), "Form 3")
        self.assertEqual(bd._form_label("4/A"), "Form 4/A")

    def test_lettered_forms_unchanged(self):
        for f in ("10-Q", "8-K", "10-K/A", "DEF 14A"):
            self.assertEqual(bd._form_label(f), f)

    def test_empty(self):
        self.assertEqual(bd._form_label(""), "")
        self.assertEqual(bd._form_label(None), "")


class TestPeopleNames(unittest.TestCase):
    """UX-P2-18."""

    def test_all_caps_is_title_cased(self):
        self.assertEqual(ps._person_name("BACON ASHLEY"), "Bacon Ashley")
        self.assertEqual(ps._person_name("NOVAKOVIC PHEBE N"), "Novakovic Phebe N")

    def test_mixed_case_kept(self):
        self.assertEqual(ps._person_name("Leopold Robin"), "Leopold Robin")
        self.assertEqual(ps._person_name("Erdoes Mary E."), "Erdoes Mary E.")

    def test_apostrophes_and_generational_suffix(self):
        self.assertEqual(ps._person_name("O'NEILL JAMES JR"), "O'Neill James Jr")
        self.assertEqual(ps._person_name("SMITH JOHN III"), "Smith John III")

    def test_mc_prefix_keeps_inner_capital(self):
        # Was "Mcgrath John" / "Mcdonald Ann" — the inner capital was lost.
        self.assertEqual(ps._person_name("MCGRATH JOHN P"), "McGrath John P")
        self.assertEqual(ps._person_name("SMITH-MCDONALD ANN"), "Smith-McDonald Ann")
        # "Mac" is ambiguous from all-caps (Mack, Macy) — plain capitalize;
        # a bare "MC" token stays "Mc".
        self.assertEqual(ps._person_name("MACKAY ANN"), "Mackay Ann")
        self.assertEqual(ps._person_name("MACY MC"), "Macy Mc")
        # Filer-cased input is never re-cased.
        self.assertEqual(ps._person_name("Mcgrath John"), "Mcgrath John")

    def test_issuer_row_detected_against_display_name(self):
        with patch.object(ps, "get_name", return_value="JPMorgan Chase"):
            self.assertTrue(ps._is_issuer("JPMORGAN CHASE & CO", "JPM"))
            self.assertFalse(ps._is_issuer("BACON ASHLEY", "JPM"))

    def test_issuer_check_never_matches_on_blank(self):
        with patch.object(ps, "get_name", return_value=""):
            self.assertFalse(ps._is_issuer("", "XXXX"))


class TestDocLabel(unittest.TestCase):
    """UX-P2-26: a bare code is its own label."""

    def test_no_parenthetical_echo(self):
        for f in ("10-Q", "10-K", "11-K", "8-K"):
            self.assertEqual(rd.doc_label({"form": f, "items": ""}), f)

    def test_descriptive_labels_keep_code(self):
        self.assertEqual(rd.doc_label({"form": "DEF 14A"}), "Proxy (DEF 14A)")
        self.assertEqual(rd.doc_label({"form": "8-K", "is_earnings": True}),
                         "Earnings Release (ER)")


class TestPeopleAbsentDash(unittest.TestCase):
    """Owner rule 2026-09-30: an absent value renders "—" on screen; the
    Excel export keeps its own n/a (the raw None goes to table_export)."""

    def _render(self):
        st = MagicMock()
        export = MagicMock()
        person = {"name": "Jane Roe", "age": None, "position": None,
                  "role": None, "director_since": None, "independent": None,
                  "committees": [], "bio": None}
        with patch.object(ps, "st", st), patch.object(ps, "title_bar"), \
             patch.object(ps, "table_export", export), \
             patch.object(ps, "get_name", return_value="Test Bancorp"), \
             patch.object(ps, "get_cik", return_value=123), \
             patch("data.people.get_proxy_people",
                   return_value={"people": [person], "filed": None,
                                 "source_url": None}), \
             patch("data.people.get_insider_roster",
                   return_value=[{"name": "DOE JOHN", "role": "Director",
                                  "latest_date": None}]):
            ps.render_people_summary("TEST")
        html = " ".join(str(c.args[0]) for c in st.markdown.call_args_list
                        if c.args)
        cap = " ".join(str(c.args[0]) for c in st.caption.call_args_list
                       if c.args)
        return html, cap, export

    def test_yn_absent_is_dash(self):
        self.assertEqual(ps._yn(None), "—")
        self.assertEqual(ps._yn(True), "Yes")
        self.assertEqual(ps._yn(False), "No")

    def test_tables_render_dash_never_na(self):
        html, cap, _ = self._render()
        self.assertNotIn("n/a", html)
        self.assertNotIn("n/a", cap)
        # Age, Position, Director Since, Independent, Committees — all absent.
        self.assertIn('<td style="text-align:right;">—</td>'
                      '<td style="text-align:left;">—</td>'
                      '<td style="text-align:right;">—</td>'
                      '<td style="text-align:left;">—</td>'
                      '<td style="text-align:left;">—</td>', html)
        # Section 16 roster: absent latest filing date.
        self.assertIn('<td style="text-align:left;">Director</td>'
                      '<td style="text-align:left;">—</td>', html)

    def test_export_keeps_raw_absent_values(self):
        _, _, export = self._render()
        df = export.call_args.args[0]
        self.assertIsNone(df["Independent"].iloc[0])
        self.assertIsNone(df["Position"].iloc[0])
        self.assertNotIn("—", df.astype(str).to_numpy().ravel().tolist())


if __name__ == "__main__":
    unittest.main()
