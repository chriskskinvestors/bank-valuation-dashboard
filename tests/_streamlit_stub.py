"""
Shared, order-independent streamlit stub for the unittest suites.

Test modules that import project code decorating with @st.cache_data /
@st.cache_resource / @st.fragment at module load (or doing
`import streamlit.components.v1`) must call install() BEFORE those imports.

The old house pattern — every test file building its own module and
`sys.modules.setdefault`-ing it — made suite composition a race: whichever
test module imported first won, and a thinner stub broke any later suite in
the same process that needed more attributes (b2139a4 fixed one instance of
the class; this helper fixes the class). install() is additive and
idempotent: it completes whatever "streamlit" module is already installed
(another suite's stub — or the real package, which already carries every
attribute) and never replaces an existing attribute, so any combination of
suites passes in any order.

Suites needing richer widget behavior (e.g. tests/test_rate_sensitivity_labels)
call install() first and then setattr their extras on the returned module.
tests/smoke_views.py and tests/test_render_smoke.py intentionally keep their
own forced rich fakes: they run as standalone script processes (CI invokes
`python tests/test_render_smoke.py` directly) and drive full page renders.
"""
import sys
import types


def _passthru(*a, **k):
    # Pass-through decorator handling both bare (@st.cache_data) and
    # parameterized (@st.cache_data(ttl=...), @st.fragment(run_every=...))
    # forms.
    if a and callable(a[0]):
        return a[0]
    return lambda f: f


def assert_fixture_columns(test, html, hist_rows, period):
    """Anti-dead-stub guard for statement render tests.

    When a renderer's data seam migrates, a stub of the OLD seam keeps
    "passing" while the render silently pulls LIVE data (f6c689c deadened
    test_audit_regressions' balance-sheet stub exactly this way: the render
    showed 8 real quarters, the 2 fixture rows never entered the pipeline,
    and one fixture assert passed coincidentally against real values).
    A statement's period headers are derived 1:1 from the fixture REPDTEs,
    so leaked live data shows up as extra/other columns — assert the exact
    header set the fixtures imply, mirroring render_statement's labeling
    (Annual: December rows, tail(5), "FY2025"; Quarterly: tail(8), "Q1 '26").
    """
    import pandas as pd
    dates = sorted(pd.to_datetime([r["REPDTE"] for r in hist_rows]))
    if period == "Annual":
        dates = [d for d in dates if d.month == 12][-5:]
        labels = [f"FY{d.year}" for d in dates]
    else:
        dates = dates[-8:]
        labels = [f"Q{(d.month - 1) // 3 + 1} '{str(d.year)[2:]}" for d in dates]
    test.assertEqual(
        html.count('class="colh"'), len(labels),
        f"rendered period-column count != fixture rows ({labels}) — the "
        "data-seam stub is dead and the render pulled live data")
    for lab in labels:
        test.assertIn(
            f">{lab}</th>", html,
            f"fixture column {lab!r} missing from rendered headers — the "
            "data-seam stub is dead and the render pulled live data")


def install():
    """Install or complete the streamlit stub; returns the module."""
    st = sys.modules.get("streamlit")
    if st is None:
        st = types.ModuleType("streamlit")
    for attr in ("cache_data", "cache_resource", "fragment"):
        if not hasattr(st, attr):
            setattr(st, attr, _passthru)
    if not hasattr(st, "session_state"):
        st.session_state = {}
    comp = sys.modules.get("streamlit.components")
    if comp is None:
        comp = getattr(st, "components", None) or types.ModuleType(
            "streamlit.components")
    v1 = sys.modules.get("streamlit.components.v1")
    if v1 is None:
        v1 = getattr(comp, "v1", None) or types.ModuleType(
            "streamlit.components.v1")
    if not hasattr(v1, "html"):
        v1.html = lambda *a, **k: None
    comp.v1 = v1
    if not hasattr(st, "components"):
        st.components = comp
    sys.modules["streamlit"] = st
    sys.modules["streamlit.components"] = comp
    sys.modules["streamlit.components.v1"] = v1
    return st
