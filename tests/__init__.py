"""Package init: install the shared streamlit stub BEFORE any test module
imports pipeline code.

unittest discovery imports every test module before running any test, so a
single module that reaches `import streamlit` through the pipeline (e.g. via
data/sec_client) binds the REAL package for the whole process — and real
st.cache_data memoizes functions like get_latest_fundamentals by cik ACROSS
test modules, so later modules patching fetch_company_facts get another
module's cached results (2026-08-19: 24 tests failed only in composition;
test_share_equity_coherence's FSUN fixture was served another bank's share
count). A 2026-08-19 scan found 18 offender modules, so per-module
install-before-import discipline (PR #45's approach) cannot be the only
guard. Installing here is idempotent and additive (see _streamlit_stub) and
runs before any sibling module import, killing the class by construction.

Script-style suites (python tests/test_render_smoke.py) do not import this
package and keep their own rich fakes.
"""
from tests._streamlit_stub import install as _install

_install()
