"""
Pins the 13F issuer-name matching fix (AUDIT-2026-07-02 P2 #20) in
data/form13f_client.py.

The old predicate used a naive substring test (`target in name`), which
produced false positives: the ticker "KEY" matched "KEYSIGHT TECHNOLOGIES"
and "BANC" matched "BANCOLOMBIA SA". Those wrong rows then persisted into
merge-only quarter snapshots. The fix (`_issuer_matches`) requires a
word/token-boundary match: a bare ticker must equal a whole word token of
the issuer name, never a substring of one.
"""
import sys
import types
import unittest

# Stub streamlit before importing modules that decorate with st.cache_data.
_st = types.ModuleType("streamlit")
_st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
_st.cache_resource = _st.cache_data
sys.modules.setdefault("streamlit", _st)

from data.form13f_client import _issuer_matches  # noqa: E402


class TestIssuerMatching(unittest.TestCase):
    def test_old_false_positives_rejected(self):
        # Bare ticker must NOT bleed into a longer word token.
        self.assertFalse(_issuer_matches("KEY", "KEYSIGHT TECHNOLOGIES INC"))
        self.assertFalse(_issuer_matches("BANC", "BANCOLOMBIA SA"))

    def test_legitimate_ticker_token_match(self):
        # Ticker equals a standalone word token of the issuer name.
        self.assertTrue(_issuer_matches("KEY", "KEY CORP"))
        self.assertTrue(_issuer_matches("BANC", "BANC OF CALIFORNIA INC"))

    def test_full_name_equality(self):
        self.assertTrue(_issuer_matches("KEYCORP", "KEYCORP"))

    def test_multiword_company_name_phrase(self):
        # A cleaned multi-word company-name search term matches only when its
        # exact phrase appears in the issuer name (the company-name path used
        # by fetch_institutional_holdings when company_name is supplied).
        self.assertTrue(_issuer_matches("KeyCorp", "KEYCORP"))
        self.assertTrue(
            _issuer_matches("Banc of California", "BANC OF CALIFORNIA INC")
        )
        self.assertFalse(
            _issuer_matches("Banc of California", "BANCOLOMBIA SA")
        )

    def test_case_insensitive_and_punctuation(self):
        # Punctuation in the issuer name is a token separator, not a match.
        self.assertTrue(_issuer_matches("key", "KEY, CORP."))
        self.assertFalse(_issuer_matches("KEY", "KEYSIGHT, INC."))

    def test_empty_target_never_matches(self):
        self.assertFalse(_issuer_matches("", "KEYCORP"))


if __name__ == "__main__":
    unittest.main()
