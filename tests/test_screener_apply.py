"""Screener Metric-filters dialog has an explicit Apply (owner, 2026-09-22).

The filter widgets write session_state directly, so the only way to see
results used to be the dialog's X — which reads as "cancel". Pin: the
dialog body ends with a primary Apply button whose click closes the dialog
by rerunning (Streamlit's documented way to close a dialog programmatically).
"""
import unittest
from pathlib import Path


class TestFiltersDialogApply(unittest.TestCase):
    def test_apply_button_closes_dialog_via_rerun(self):
        src = (Path(__file__).parent.parent / "app.py").read_text(encoding="utf-8")
        start = src.index('@st.dialog("Metric filters"')
        end = src.index("filter_specs = _filter_specs_from_state()", start)
        body = src[start:end]
        self.assertIn('st.button("Apply", type="primary"', body)
        after = body[body.index('st.button("Apply"'):]
        self.assertIn("st.rerun()", after.split("\n\n")[0],
                      "Apply must close the dialog by rerunning")
        self.assertIn('key=f"filt_apply_{tab_key}"', body)


if __name__ == "__main__":
    unittest.main()
