"""(2026-08-20) Radio-as-tabs dot-hiding CSS must never be positional.

Streamlit 1.58→1.60 changed the radio label DOM and every
`label>div:first-of-type{display:none}` dot-hider started blanking the
LABEL CONTENT instead of the dot. PR #33 fixed the six scopes in
ui/styles.py — but ui/macro.py (five scopes) and app.py (sc_subnav)
carried LOCAL COPIES of the same rule, against the no-local-copies rule,
and stayed broken for over two weeks until the owner hit the blank
Market & Macro sub-tabs ("WHERE ARE MY SUB TABS (AGAIN)").

This pin fails on ANY positional dot-hider anywhere in the repo, so the
next inline copy (or a revert) is caught at CI, not by the owner. The
sanctioned selector hides label divs that neither ARE nor CONTAIN
[data-testid="stMarkdownContainer"] — structure-proof on both DOMs.

Run: python -m unittest tests.test_radio_nav_css_structure
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# A label dot-hider built on child POSITION rather than the stable
# data-testid contract. Matches `>label>div:first-of-type` and
# `>label>div:first-child` (whitespace-tolerant), which under the 1.60 DOM
# hide the content wrapper — blank tabs.
_POSITIONAL_HIDER = re.compile(
    r">\s*label\s*>\s*div\s*:\s*first-(?:of-type|child)")

# Historical comments legitimately mention the pattern — only match inside
# actual selector usage by requiring the file line NOT be a pure comment.
_COMMENT_LINE = re.compile(r"^\s*(#|//|\*|/\*)")


class TestNoPositionalLabelHiders(unittest.TestCase):
    def test_no_positional_dot_hider_anywhere(self):
        offenders = []
        for path in REPO.rglob("*.py"):
            rel = path.relative_to(REPO).as_posix()
            if rel.startswith((".claude/", "tests/")):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if _COMMENT_LINE.match(line):
                    continue
                if _POSITIONAL_HIDER.search(line):
                    offenders.append(f"{rel}:{i}")
        self.assertEqual(
            offenders, [],
            "positional radio-label dot-hider(s) found — these blank the "
            "tab labels under Streamlit>=1.60. Use the structure-proof "
            "selector from ui/styles.py (hide label divs that neither are "
            "nor contain [data-testid=\"stMarkdownContainer\"]): "
            + ", ".join(offenders))


if __name__ == "__main__":
    unittest.main()
