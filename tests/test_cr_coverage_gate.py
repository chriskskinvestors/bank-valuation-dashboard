"""
Pins the pure comparison logic of the Company-Reported coverage gate
(tests/test_cr_coverage.py compare()): what counts as a regression (fails)
vs an explained residual or an addition (passes with a note). Offline —
never touches the network or the measurement machinery.
"""
import unittest

from tests.test_cr_coverage import compare


def _row(cls, n=0):
    return {"class": cls, "n": n, "extra": None, "err": None}


def _baseline(fn_classes):
    return {"recorded": "2026-08-04",
            "functions": {fn: {"classes": classes}
                          for fn, classes in fn_classes.items()}}


class TestCompare(unittest.TestCase):
    def test_ok_to_empty_is_a_regression(self):
        base = _baseline({"fn": {"ABCB": "OK-multiyear"}})
        regs, _ = compare(base, {"fn": {"ABCB": _row("EMPTY")}})
        self.assertEqual(len(regs), 1)
        self.assertIn("ABCB", regs[0])

    def test_multiyear_to_single_is_a_regression(self):
        base = _baseline({"fn": {"ABCB": "OK-multiyear"}})
        regs, _ = compare(base, {"fn": {"ABCB": _row("OK-single", 1)}})
        self.assertEqual(len(regs), 1)

    def test_error_always_fails_even_without_baseline(self):
        regs, _ = compare(None, {"fn": {"KEY": _row("ERROR")}})
        self.assertEqual(len(regs), 1)
        self.assertIn("KEY", regs[0])

    def test_baseline_empty_residual_passes(self):
        # EMPTY at baseline = explained residual (genuine non-disclosure or a
        # known parser gap) — still EMPTY must NOT fail the gate.
        base = _baseline({"fn": {"CASH": "EMPTY"}})
        regs, notes = compare(base, {"fn": {"CASH": _row("EMPTY")}})
        self.assertEqual(regs, [])
        self.assertEqual(notes, [])

    def test_improvement_passes_with_note(self):
        base = _baseline({"fn": {"CASH": "EMPTY"}})
        regs, notes = compare(base, {"fn": {"CASH": _row("OK-multiyear", 5)}})
        self.assertEqual(regs, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("improved", notes[0])

    def test_new_bank_passes_with_note(self):
        base = _baseline({"fn": {"ABCB": "OK-multiyear"}})
        regs, notes = compare(
            base, {"fn": {"ABCB": _row("OK-multiyear", 5),
                          "NEWB": _row("EMPTY")}})
        self.assertEqual(regs, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("NEWB", notes[0])

    def test_new_function_passes_with_note(self):
        regs, notes = compare(_baseline({}),
                              {"new_fn": {"ABCB": _row("OK-single", 1)}})
        self.assertEqual(regs, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("new_fn", notes[0])

    def test_unchanged_full_pass_is_silent(self):
        base = _baseline({"fn": {"ABCB": "OK-multiyear", "CASH": "EMPTY"}})
        regs, notes = compare(base, {"fn": {"ABCB": _row("OK-multiyear", 5),
                                            "CASH": _row("EMPTY")}})
        self.assertEqual((regs, notes), ([], []))

    def test_subsampled_run_checks_only_measured_banks(self):
        # --slow-sub runs fewer banks; unmeasured baseline banks must not
        # produce phantom regressions or notes.
        base = _baseline({"fn": {"ABCB": "OK-multiyear", "PNFP": "OK-multiyear"}})
        regs, notes = compare(base, {"fn": {"ABCB": _row("OK-multiyear", 5)}})
        self.assertEqual((regs, notes), ([], []))


if __name__ == "__main__":
    unittest.main()
