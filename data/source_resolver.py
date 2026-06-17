"""Freshest-wins source resolver (DATA-SOURCING-ARCHITECTURE increment 3).

Per metric/dataset, an ordered list of **providers** in freshness order
(IR earnings release → SEC 10-K/10-Q → FDIC/FFIEC call report). Each provider
returns a `SourceRecord` (a value plus its provenance) or `None` when it can't
supply that metric. `resolve()` returns the record with the most recent
`as_of`; ties break toward the provider listed first (the fresher *source
class*). The chosen `source` + `as_of` + `doc_link` flow into the existing
click-through provenance, so a new provider plugs in without touching the UI.

This is the ROUTING layer only. It chooses among values the providers already
extracted and reconcile-gated; it never computes, scales, or guesses a value.
A provider that isn't confident returns `None`; if every provider returns
`None`, `resolve()` returns `None` and the caller renders n/a. The cardinal
rule (never ship a plausible-wrong number) is preserved end to end — the
resolver can only ever surface a value a provider already vouched for.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Optional, Sequence


@dataclass(frozen=True)
class SourceRecord:
    """One provider's answer for a metric, with the provenance to trace it.

    `as_of` is the machine-readable disclosure/period date used for the
    freshest-wins comparison; `display_asof` is the pretty string for the UI
    card (defaults to the ISO date). `value is None` means "no value" — such a
    record is treated as absent by `resolve()`, never surfaced.
    """
    value: Any
    as_of: Optional[date]
    source: str
    doc_link: Optional[str] = None
    display_asof: str = ""
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.display_asof and self.as_of is not None:
            # frozen dataclass: bypass the immutability guard for this derived default
            object.__setattr__(self, "display_asof", self.as_of.isoformat())


# A provider is any zero-arg callable returning a SourceRecord or None. Keeping
# it a thunk (not a value) lets resolve() skip the work of slower providers once
# it has them all — and lets a provider fail in isolation without aborting the
# resolve (an exception is caught and treated as "no answer").
Provider = Callable[[], Optional[SourceRecord]]


def resolve(providers: Sequence[Provider]) -> Optional[SourceRecord]:
    """Return the freshest available record, or None if no provider supplies one.

    Selection: a record with a real `as_of` always beats one without; among
    dated records the latest `as_of` wins; an exact tie (or two undated records)
    breaks toward the provider listed first, i.e. the fresher source class. A
    provider that raises, returns None, or returns a `value is None` record is
    treated as "no answer".
    """
    candidates: list[tuple[int, SourceRecord]] = []
    for idx, provider in enumerate(providers):
        try:
            rec = provider()
        except Exception:
            # A provider failing (source down, parse error) must not abort the
            # resolve — the next-freshest source should still answer.
            rec = None
        if rec is not None and rec.value is not None:
            candidates.append((idx, rec))
    if not candidates:
        return None

    def rank(item: tuple[int, SourceRecord]):
        idx, rec = item
        # max() wins: dated beats undated; latest date beats earlier; on a tie,
        # the smaller index (fresher source class) wins via -idx.
        return (rec.as_of is not None, rec.as_of or date.min, -idx)

    return max(candidates, key=rank)[1]


def first_available(providers: Sequence[Provider]) -> Optional[SourceRecord]:
    """Return the first provider that answers, in list order — for metrics where
    the ordering already encodes preference (e.g. a holdco-basis value that only
    one source can supply) and recency isn't the deciding factor. Same
    None/`value is None` skipping as `resolve()`."""
    for provider in providers:
        try:
            rec = provider()
        except Exception:
            rec = None
        if rec is not None and rec.value is not None:
            return rec
    return None
