"""
Provenance tracking for every data point in the portal.

Each metric value should carry metadata about WHERE it came from so an
analyst can trace it back to the primary source filing. This is the
foundation of data trust — no number without provenance.

Structures:

    Source(
        origin="SEC" | "FDIC" | "YFINANCE" | "FRED" | "IBKR" | "COMPUTED",
        identifier=<primary key>,    # SEC: CIK, FDIC: CERT, FRED: series_id
        concept=<field name>,         # XBRL concept or FDIC field
        as_of=<YYYY-MM-DD>,           # end date of reported period
        filed=<YYYY-MM-DD>,           # date of filing/fetch
        form=<form type>,             # 10-K, 10-Q, 8-K, Call Report, etc.
        unit=<USD | shares | pct | bps | ...>,
        notes=<human-readable caveats>,
    )

    Valued(value, source) — a value plus its provenance.

The SEC and FDIC clients return Valued objects instead of raw scalars.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional


@dataclass(frozen=True)
class Source:
    """Where a value came from."""
    origin: str                       # "SEC" | "FDIC" | "YFINANCE" | "FRED" | "IBKR" | "COMPUTED"
    identifier: str = ""              # CIK / CERT / series_id / ticker
    concept: str = ""                 # XBRL concept or FDIC field name
    as_of: str = ""                   # YYYY-MM-DD
    filed: str = ""                   # YYYY-MM-DD
    form: str = ""                    # e.g. "10-K", "10-Q", "Call Report"
    unit: str = ""                    # USD, shares, pct, etc.
    notes: str = ""
    # For COMPUTED values, record what inputs fed it:
    derived_from: tuple = field(default_factory=tuple)  # tuple of Source

    def describe(self) -> str:
        """One-line human-readable description."""
        if self.origin == "COMPUTED":
            inputs = ", ".join(s.concept for s in self.derived_from) if self.derived_from else "—"
            return f"Computed from: {inputs}"
        if self.origin == "SEC":
            return f"SEC XBRL · CIK {self.identifier} · {self.concept} · {self.form} as of {self.as_of}"
        if self.origin == "FDIC":
            return f"FDIC Call Report · Cert {self.identifier} · {self.concept} as of {self.as_of}"
        if self.origin == "FRED":
            return f"FRED · Series {self.identifier or self.concept} as of {self.as_of}"
        if self.origin == "YFINANCE":
            return f"Yahoo Finance · {self.concept} as of {self.as_of}"
        if self.origin == "IBKR":
            return f"IBKR live · {self.concept}"
        return f"{self.origin} · {self.concept} · {self.as_of}"

    def age_days(self) -> int | None:
        """How old is this data, in days? Returns None if as_of missing."""
        if not self.as_of:
            return None
        try:
            d = datetime.strptime(self.as_of, "%Y-%m-%d")
            return (datetime.now() - d).days
        except Exception:
            return None


@dataclass
class Valued:
    """A value plus its provenance."""
    value: Any
    source: Source

    def __repr__(self):
        return f"Valued({self.value!r}, source={self.source.origin}:{self.source.concept})"

    # Convenience: if callers forget provenance and use `val`, allow it to act like a scalar
    def __float__(self):
        return float(self.value) if self.value is not None else float("nan")

    def __int__(self):
        return int(self.value) if self.value is not None else 0

    def __bool__(self):
        return self.value is not None and self.value != 0

    def __eq__(self, other):
        if isinstance(other, Valued):
            return self.value == other.value
        return self.value == other


def to_dict(s: Source) -> dict:
    """Serialize Source for JSON / UI."""
    return asdict(s)


# Backwards-compat: when upstream code expects a scalar, provide a helper
# that unwraps Valued → scalar.
def unwrap(v):
    """Return the raw scalar value; pass through if already unwrapped."""
    if isinstance(v, Valued):
        return v.value
    return v


def unwrap_dict(d: dict) -> dict:
    """Recursively unwrap a dict of Valued objects."""
    return {k: unwrap(v) if not isinstance(v, dict) else unwrap_dict(v) for k, v in d.items()}


def provenance_of(v) -> Source | None:
    """Extract provenance from a Valued, else None."""
    if isinstance(v, Valued):
        return v.source
    return None
