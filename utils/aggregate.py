"""Aggregation primitives that refuse to fabricate.

`strict_sum` is THE way to total a column that may contain unknowns: the sum
is known only when every component is known. pandas' default `sum()` skips
NaN and returns 0.0 for an all-NaN window, and `fillna(0)` / `or 0` on inputs
turn "not reported" into "$0" — the fabricated-aggregate defect class
(AUDIT 2026-08-19, memory note). Use this instead of either.
"""
from __future__ import annotations

import math
from typing import Iterable


def strict_sum(values: Iterable, na=None):
    """Sum of `values` when every value is a known number; otherwise `na`.

    Unknown = None, NaN, or a non-numeric string. An EMPTY input is unknown
    too (there is nothing to total). Pass `na=float("nan")` inside pandas
    aggregations so the result stays numeric-typed.
    """
    total, seen = 0.0, False
    for v in values:
        if v is None:
            return na
        try:
            f = float(v)
        except (TypeError, ValueError):
            return na
        if math.isnan(f):
            return na
        total += f
        seen = True
    return total if seen else na
