"""Deep-link ?tab= sub-tab restore regression tests (audit 2026-07-02 #10).

The bug: the URL-restore pre-seed in app.py wrote `company_subtab::{section}`
for flat (non-Financials) sections, but the sub-tab radio's widget key is
built with the basis interpolated — `company_subtab::{section}::{basis}` —
which for flat sections (basis=None) evaluates to
`company_subtab::{section}::None`. The pre-seeded value landed under a key
the widget never reads, so a refresh/shared link to any non-first sub-tab of
Valuation, News & Filings, or Ownership opened the section's FIRST tab.

The fix routes BOTH sites through one shared builder, app.py's _subtab_key.
app.py executes Streamlit at import time and can't be imported in unit tests
(same constraint as tests/test_company_url_state.py), so the helper is
extracted from app.py's AST and exec'd — the function under test is the
actual shipped source, not a copy.
"""
import ast
import unittest
from pathlib import Path

from ui.company_nav import COMPANY_NAV

_APP_PATH = Path(__file__).parent.parent / "app.py"
_APP_SRC = _APP_PATH.read_text(encoding="utf-8")


def _load_subtab_key():
    """Compile app.py's top-level _subtab_key (and only it) into a namespace."""
    tree = ast.parse(_APP_SRC)
    fn = next((n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "_subtab_key"),
              None)
    if fn is None:
        raise AssertionError("_subtab_key not found at app.py top level — "
                             "the shared sub-tab key builder was removed")
    ns = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]),
                 str(_APP_PATH), "exec"), ns)
    return ns["_subtab_key"], ast.get_source_segment(_APP_SRC, fn)


_subtab_key, _HELPER_SRC = _load_subtab_key()


class TestSubtabKeyParity(unittest.TestCase):
    def test_flat_section_preseed_matches_widget_key(self):
        # The exact regression: the pre-seed calls _subtab_key(sec) (no basis),
        # the widget calls _subtab_key(sec, company_basis) with basis=None for
        # flat sections. Both must yield the SAME key — and specifically the
        # `::None` form the widget has always used, so live sessions keep their
        # remembered sub-tab across this change.
        flat = [s for s, nav in COMPANY_NAV.items() if not isinstance(nav, dict)]
        self.assertTrue(flat, "no flat sections in COMPANY_NAV — test is vacuous")
        for sec in flat:
            self.assertEqual(_subtab_key(sec), _subtab_key(sec, None),
                             f"pre-seed vs widget key diverge for {sec!r}")
            self.assertEqual(_subtab_key(sec), f"company_subtab::{sec}::None",
                             f"flat-section widget key format changed for {sec!r}")

    def test_financials_basis_keys_unchanged(self):
        # Dict-basis (Financials) keys keep the historic
        # `company_subtab::{section}::{basis}` format exactly, so existing
        # user sessions don't lose their remembered sub-tab per basis.
        dict_sections = {s: nav for s, nav in COMPANY_NAV.items()
                         if isinstance(nav, dict)}
        self.assertTrue(dict_sections,
                        "no dict-basis sections in COMPANY_NAV — test is vacuous")
        for sec, nav in dict_sections.items():
            for basis in nav:
                self.assertEqual(
                    _subtab_key(sec, basis),
                    f"company_subtab::{sec}::{basis}",
                    f"basis key format changed for {sec!r}/{basis!r}")

    def test_no_key_construction_outside_helper(self):
        # Divergence guard: app.py must not rebuild a company_subtab:: key by
        # hand anywhere outside _subtab_key — that is exactly how the pre-seed
        # and the widget drifted apart in the first place.
        remainder = _APP_SRC.replace(_HELPER_SRC, "")
        self.assertNotIn(
            'f"company_subtab::', remainder,
            "app.py builds a company_subtab:: key outside _subtab_key — "
            "route it through the shared helper")


if __name__ == "__main__":
    unittest.main(verbosity=2)
