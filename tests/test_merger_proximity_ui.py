"""Market Analysis ▸ Merger Planning (HHI) + Branch Proximity render guards.

Nav wiring assertions pin the two new leaves into COMPANY_NAV and the
renderer registry (the A17 sync test in test_audit_regressions enforces the
global invariant; these pin the specific leaves by name).

The render guards drive the REAL page renderers end-to-end — real
analysis/merger_hhi math, real data/branches_store SQL — against an
isolated in-memory SQLite branches store, with the ticker→cert mapping
stubbed at the page-module seam. Rendering goes through the shared
streamlit stub (tests/_streamlit_stub, installed by tests/__init__),
extended per its documented pattern with recording + scripted widgets
(AppTest cannot run under the suite-wide stub, which is installed before
any test module imports pipeline code). Every extension is restored in
tearDown so no sibling suite sees the recorders.

Merger fixture (county 11111, hand-computed as in tests/test_merger_hhi):
    A=400, B=300, C=200, D=100 → pre-HHI 3000, post (A+B=70%) 5400,
    Δ 2400 → DOJ-flagged. Cert 7 ("Solo") operates only in county 88888 —
    no overlap with A.

Proximity fixture (hand distances as in tests/test_branch_geo):
    subject cert 1: Main Office (40.0,-75.0), a no-coords branch, and
    North Branch (41.5,-75.0). Competitors: cert 2 at (40.1,-75.0) =
    6.91 mi, cert 3 at (40.0,-75.1) = 5.29 mi, cert 7 at (41.5,-75.05) =
    2.59 mi from North Branch. Radius 10 → pairs exist; radius 1 → none,
    so the nearest-competitor fallback renders.
"""
import unittest

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

import data.db as db
import data.branches_store as bs

_MISSING = object()


def _ins(conn, cert, brnum, ticker, name, fips, msa, dep,
         lat=None, lng=None, branch="br", city="Town", state="PA",
         year=2024):
    conn.execute(text(
        "INSERT INTO branches (cert, brnum, year, ticker, bank_name, "
        "branch_name, address, city, state, zip, county, stcntybr, "
        "msa_code, msa_name, deposits, lat, lng, serv_type) VALUES "
        "(:cert, :brnum, :year, :tk, :nm, :br, 'addr', :city, :st, "
        "'19000', :cty, :fips, :msa, :msan, :dep, :lat, :lng, '12')"),
        {"cert": cert, "brnum": brnum, "year": year, "tk": ticker,
         "nm": name, "br": branch, "city": city, "st": state,
         "cty": f"County {fips}", "fips": fips, "msa": msa,
         "msan": f"MSA {msa}", "dep": dep, "lat": lat, "lng": lng})


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _StubUI:
    """Recording + scripted-widget extension of the shared streamlit stub
    (the documented extension pattern: install() then setattr extras).
    restore() puts every touched attribute back."""

    def __init__(self):
        from tests._streamlit_stub import install
        self.st = install()
        self.markdowns: list[str] = []
        self.captions: list[str] = []
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.select_calls: dict[str, list] = {}   # widget key -> options
        self.select_values: dict[str, object] = {}  # widget key -> scripted
        names = ("markdown", "caption", "info", "warning", "error",
                 "spinner", "container", "selectbox", "radio",
                 "plotly_chart", "download_button")
        self._saved = {n: getattr(self.st, n, _MISSING) for n in names}
        st = self.st
        st.markdown = lambda text, **k: self.markdowns.append(str(text))
        st.caption = lambda text, **k: self.captions.append(str(text))
        st.info = lambda text, **k: self.infos.append(str(text))
        st.warning = lambda text, **k: self.warnings.append(str(text))
        st.error = lambda text, **k: self.warnings.append(str(text))
        st.spinner = lambda *a, **k: _Ctx()
        st.container = lambda *a, **k: _Ctx()
        st.plotly_chart = lambda *a, **k: None
        st.download_button = lambda *a, **k: None
        st.selectbox = self._selectbox
        st.radio = self._radio

    def _scripted(self, key, options):
        if key in self.select_values:
            v = self.select_values[key]
            assert v in list(options), (
                f"scripted value {v!r} not in widget {key!r} options — "
                f"stale test script. Options: {list(options)[:8]}…")
            return v
        return _MISSING

    def _selectbox(self, label, options=None, index=0, key=None, **k):
        opts = list(options or [])
        self.select_calls[key] = opts
        v = self._scripted(key, opts)
        if v is not _MISSING:
            return v
        if index is None or not opts:
            return None
        return opts[index]

    def _radio(self, label, options=None, index=0, key=None, **k):
        opts = list(options or [])
        self.select_calls[key] = opts
        v = self._scripted(key, opts)
        if v is not _MISSING:
            return v
        return opts[index or 0] if opts else None

    def restore(self):
        for n, v in self._saved.items():
            if v is _MISSING:
                if hasattr(self.st, n):
                    delattr(self.st, n)
            else:
                setattr(self.st, n, v)

    # blobs for assertions
    def md(self) -> str:
        return "\n".join(self.markdowns)

    def info_text(self) -> str:
        return "\n".join(self.infos)

    def caption_text(self) -> str:
        return "\n".join(self.captions)


class _SqliteStoreCase(unittest.TestCase):
    """Shared harness: isolated sqlite branches store + cert-mapping stub
    at the page-module seam + the recording streamlit stub."""

    PAGE_MODULE = None      # set by subclass (the ui page module)
    SUBJECT_CERT = 1

    def setUp(self):
        self._eng = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self._orig_get_engine = db.get_engine
        db.get_engine = lambda: self._eng
        bs._engine = None
        bs.init_branches_schema()
        with self._eng.begin() as c:
            self._fixture(c)
        mod = self.PAGE_MODULE
        self._orig_cert = mod.get_fdic_cert
        self._orig_name = mod.get_name
        mod.get_fdic_cert = lambda t: self.SUBJECT_CERT
        mod.get_name = lambda t: "Alpha Bancorp"
        self.ui = _StubUI()

    def tearDown(self):
        self.ui.restore()
        mod = self.PAGE_MODULE
        mod.get_fdic_cert = self._orig_cert
        mod.get_name = self._orig_name
        db.get_engine = self._orig_get_engine
        bs._engine = None

    def _fixture(self, conn):
        raise NotImplementedError


class TestNavWiring(unittest.TestCase):
    def test_new_market_analysis_leaves_wired(self):
        from ui.company_nav import (COMPANY_NAV, COMPANY_SECTION_OF,
                                    _RENDERERS)
        for leaf in ("Merger Planning (HHI)", "Branch Proximity"):
            self.assertIn(leaf, COMPANY_NAV["Market Analysis"], leaf)
            self.assertIn(leaf, _RENDERERS, leaf)
            self.assertTrue(callable(_RENDERERS[leaf]), leaf)
            self.assertEqual(COMPANY_SECTION_OF[leaf], "Market Analysis")

    def test_nav_registry_still_in_sync(self):
        # Same invariant the A17 structural test enforces — asserted here
        # too so this module fails standalone if the wiring regresses.
        from ui.company_nav import COMPANY_NAV, _RENDERERS
        flat = {leaf for v in COMPANY_NAV.values() if isinstance(v, list)
                for leaf in v}
        templated = set(COMPANY_NAV["Financials"]["Templated"])
        self.assertEqual(flat | templated, set(_RENDERERS))


class TestMergerPlanningUI(_SqliteStoreCase):
    @property
    def PAGE_MODULE(self):
        import ui.merger_planning as mp
        return mp

    def _fixture(self, c):
        # county 11111: A=400/B=300/C=200/D=100 → pre 3000, post 5400,
        # Δ 2400, flagged (hand-computed, tests/test_merger_hhi docstring)
        _ins(c, 1, 1, "AAA", "Alpha", "11111", "10001", 400)
        _ins(c, 2, 2, "BBB", "Beta", "11111", "10001", 300)
        _ins(c, 3, 3, None, "Gamma", "11111", "10001", 200)
        _ins(c, 4, 4, None, "Delta", "11111", "10001", 100)
        _ins(c, 7, 5, None, "Solo", "88888", "10008", 500)   # no overlap

    def _render(self):
        from ui.merger_planning import render_merger_planning
        render_merger_planning("AAA")

    def test_no_selection_prompt(self):
        self._render()
        self.assertIn("Pick a merger partner", self.ui.info_text())
        opts = self.ui.select_calls.get("mp_partner")
        self.assertIsNotNone(opts, "partner picker missing")
        self.assertIn("BBB — Beta", opts)
        # subject bank must not be offered as its own partner
        self.assertNotIn("AAA — Alpha", opts)

    def test_renders_hand_computed_screen(self):
        self.ui.select_values["mp_partner"] = "BBB — Beta"
        self._render()
        blob = self.ui.md()
        self.assertIn("OVERLAPPING MARKETS", blob)      # summary pills
        self.assertIn("3,000", blob)                    # pre-HHI
        self.assertIn("5,400", blob)                    # post-HHI
        self.assertIn("+2,400", blob)                   # ΔHHI
        self.assertIn("Flagged", blob)                  # DOJ screen flag
        self.assertIn("70.0%", blob)                    # combined share
        self.assertIn("40.0%", blob)                    # A share
        # DOJ thresholds cited in the caption
        caps = self.ui.caption_text()
        self.assertIn("1,800", caps)
        self.assertIn("2,500", caps)

    def test_msa_toggle_dispatches_msa_kind(self):
        self.ui.select_values["mp_partner"] = "BBB — Beta"
        self.ui.select_values["_lazytab_mp_kind"] = "By MSA"
        self._render()
        # same fixture market mirrored under msa 10001 → same HHI values
        self.assertIn("5,400", self.ui.md())
        self.assertIn("MSA 10001", self.ui.md())

    def test_honest_empty_no_overlap(self):
        self.ui.select_values["mp_partner"] = "Solo"
        self._render()
        self.assertIn("o overlapping markets", self.ui.info_text())
        self.assertNotIn("HHI Post", self.ui.md())      # no fabricated table

    def test_no_cert_mapping(self):
        self.PAGE_MODULE.get_fdic_cert = lambda t: None
        self._render()
        # The notice is an empty_state (rendered via markdown) since the
        # 2026-09-03 polish pass — no longer an st.info box.
        self.assertIn("No FDIC certificate mapping", self.ui.md())


class TestBranchProximityUI(_SqliteStoreCase):
    @property
    def PAGE_MODULE(self):
        import ui.branch_proximity as bp
        return bp

    def _fixture(self, c):
        # subject (cert 1): Main Office at (40,-75), a no-coords branch,
        # North Branch at (41.5,-75). Distances per tests/test_branch_geo.
        _ins(c, 1, 1, "AAA", "Alpha", "11111", "10001", 500,
             lat=40.0, lng=-75.0, branch="Main Office", city="Media")
        _ins(c, 1, 2, "AAA", "Alpha", "11111", "10001", 100)  # no coords
        _ins(c, 1, 3, "AAA", "Alpha", "22222", "10002", 200,
             lat=41.5, lng=-75.0, branch="North Branch", city="Hawley")
        _ins(c, 2, 1, "CMP", "CompA", "11111", "10001", 300,
             lat=40.1, lng=-75.0, branch="CompA Br")    # 6.91 mi
        _ins(c, 3, 1, None, "CompB", "11111", "10001", 250,
             lat=40.0, lng=-75.1, branch="CompB Br")    # 5.29 mi
        _ins(c, 7, 1, None, "Near3", "22222", "10002", 150,
             lat=41.5, lng=-75.05, branch="Near3 Br")   # 2.59 mi of North

    def _render(self):
        from ui.branch_proximity import render_branch_proximity
        render_branch_proximity("AAA")

    def test_renders_pairs_at_radius_10(self):
        self.ui.select_values["bp_radius"] = 10
        self._render()
        blob = self.ui.md()
        self.assertIn("Who competes within 10 miles", blob)
        self.assertIn("Main Office", blob)              # per-branch header
        self.assertIn("North Branch", blob)
        self.assertIn("CompA", blob)
        self.assertIn("CompB", blob)
        self.assertIn("Near3", blob)
        self.assertIn("SUBJECT BRANCHES", blob)         # pills
        # hand distances surface in the table (5.29 → 5.3, 6.91 → 6.9)
        self.assertIn("5.3 mi", blob)
        self.assertIn("6.9 mi", blob)

    def test_fallback_when_radius_returns_nothing(self):
        self.ui.select_values["bp_radius"] = 1
        self._render()
        info = self.ui.info_text()
        self.assertIn("No competitor branches within 1 mi", info)
        # nearest to the largest branch (Main Office) is CompB at 5.29 mi
        self.assertIn("Main Office", info)
        self.assertIn("5.3 mi away", info)
        self.assertIn("CompB", self.ui.md())

    def test_honest_empty_no_sod_rows(self):
        self.PAGE_MODULE.get_fdic_cert = lambda t: 99
        self._render()
        self.assertIn("No SOD branches for cert 99", self.ui.info_text())

    def test_coverage_counts_captioned(self):
        self._render()   # default radius 5
        caps = self.ui.caption_text()
        # the no-coords subject branch is counted, never silently dropped
        self.assertIn("1 of the subject's branches lack coordinates", caps)


if __name__ == "__main__":
    unittest.main()
