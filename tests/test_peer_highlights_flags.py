"""Best-in-class chips skip engine-flagged values (REVIEW 2026-10-06 Screen
follow-up): PNFP's Synovus-driven 111.6% TBV CAGR won "Fastest TBV growth".

Run: python -m unittest tests.test_peer_highlights_flags
"""
import unittest
from unittest import mock

from tests import _streamlit_stub

_streamlit_stub.install()

import ui.peer_comparison as PC  # noqa: E402


class TestHighlightsSkipFlagged(unittest.TestCase):
    def test_acquisition_flagged_growth_does_not_win(self):
        peers = [
            {"ticker": "PNFP", "tbv_cagr_1y": 111.6,
             "_notes": {"tbv_cagr_1y": "† includes the Synovus acquisition"}},
            {"ticker": "ONB", "tbv_cagr_1y": 12.3},
            {"ticker": "LARK", "tbv_cagr_1y": 12.7},
        ]
        out = []
        with mock.patch.object(PC.st, "markdown", lambda h, **k: out.append(h), create=True):
            PC._render_highlights(peers)
        html = out[0]
        i = html.index("Fastest organic TBV growth")
        chip = html[i:i + 400]
        self.assertIn("LARK", chip)
        self.assertNotIn("PNFP", chip)


if __name__ == "__main__":
    unittest.main()
