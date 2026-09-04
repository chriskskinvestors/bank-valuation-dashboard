"""ui/states.py — loading skeleton + empty-state components (polish pass)."""
import unittest

from tests import _streamlit_stub  # noqa: F401


class TestSkeleton(unittest.TestCase):
    def test_html_shape(self):
        from ui.states import skeleton_html
        h = skeleton_html(rows=3, cols=2)
        self.assertEqual(3, h.count('<div class="row">'))
        self.assertEqual(6, h.count("<i "))
        self.assertIn('class="ksk-skel"', h)

    def test_context_manager_clears_slot(self):
        # The slot must be emptied even when the wrapped block raises —
        # otherwise a failed pane leaves a phantom shimmer behind.
        from ui import states

        class _Slot:
            def __init__(self):
                self.log = []

            def markdown(self, *a, **k):
                self.log.append("markdown")

            def empty(self):
                self.log.append("empty")

        slot = _Slot()
        st = states.st
        orig = getattr(st, "empty", None)
        st.empty = lambda: slot
        try:
            with self.assertRaises(ValueError):
                with states.skeleton():
                    raise ValueError("pane blew up")
        finally:
            if orig is None:
                del st.empty
            else:
                st.empty = orig
        self.assertEqual(["markdown", "empty"], slot.log)


class TestEmptyState(unittest.TestCase):
    def test_escapes_html(self):
        from ui import states
        captured = []
        st = states.st
        orig = getattr(st, "markdown", None)
        st.markdown = lambda body, **k: captured.append(body)
        try:
            states.empty_state("No <script>alert(1)</script> here",
                              hint="a & b")
        finally:
            if orig is None:
                del st.markdown
            else:
                st.markdown = orig
        self.assertEqual(1, len(captured))
        self.assertNotIn("<script>", captured[0])
        self.assertIn("&lt;script&gt;", captured[0])
        self.assertIn("a &amp; b", captured[0])

    def test_hint_optional(self):
        from ui import states
        captured = []
        st = states.st
        orig = getattr(st, "markdown", None)
        st.markdown = lambda body, **k: captured.append(body)
        try:
            states.empty_state("Nothing here")
        finally:
            if orig is None:
                del st.markdown
            else:
                st.markdown = orig
        self.assertNotIn('class="l2"', captured[0])


if __name__ == "__main__":
    unittest.main()
