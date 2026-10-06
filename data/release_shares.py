"""
Common shares outstanding at quarter-end, read from an earnings release and
TIED OUT against the release's own equity (OTC market cap, owner spec
2026-10-06).

Non-SEC-filer banks publish no cover-page share count, so the release is the
only source. A share count is the one input a wrong magnitude turns into a
plausible-wrong market cap (×1000 from a thousands column), so nothing here
is served on label match alone — the count must REPRODUCE the release's own
total equity through its own per-share book value:

    stated BV/share (or TBV/share) × shares  ≈  total (common) equity  (±1%)

Two readers feed the same tie-out, both period-proven via the shared
release_metrics header parser (never positional):

  1. TABLE ROW — a "Shares outstanding (at period end)" / "Common Stock Shares
     Outstanding" / "Outstanding Shares at End of Period" row at the expected
     quarter-end column. Weighted/average, preferred, treasury, authorized and
     diluted/basic rows never match. The count is accepted only as the RAW
     printed value: a table whose units statement does not except share data
     ("in thousands, except per share data") may print the count in
     thousands, so its share rows are refused — never scaled by a guess.
  2. BALANCE-SHEET CAPTION — the equity caption "Voting Common Stock:
     6,978,754, 6,973,747 and 6,984,013 Shares Issued and Outstanding at
     June 30, 2026, March 31, 2026 and December 31, 2025" (FDVA 2Q26), read
     across the cells/rows the caption is split over. Counts pair with the
     caption's own stated dates positionally (one count = every date); any
     other count/date arity is refused. A caption whose class context names
     preferred or treasury stock is skipped; captions without "common" are
     skipped. The common classes (voting + non-voting) are SUMMED.

When both readers speak they must agree (±0.2%), else None. The equity total
comes from a "Total (common) stockholders' equity" row at the same column;
its scale is the table's stated unit, or — when no unit is stated — any of
×1/×1e3/×1e6 may tie (the equity scale only VALIDATES; it never reaches the
output, and only one scale can land within 1%). Anything that fails is None:
n/a on the card, never a guess.
"""
from __future__ import annotations

import re

from data.release_metrics import (_CHANGE_COL, _DTOK, _MONTHS3,  # noqa: F401
                                  _period_header, _period_qend, _row_values,
                                  _table_rows)

_SHARES_BAND = (1_000.0, 2e10)
_TIE_TOL = 0.01          # BV × shares vs equity
_AGREE_TOL = 0.002       # candidates for the count must agree (±0.2%)

# A period-end common share count row, anchored at both ends of the label.
_SHARE_ROW = re.compile(
    r"(?i)^\s*(?:"
    r"(?:total |period[- ]end(?:ing)? |ending |end[- ]of[- ]period )?"
    r"(?:common (?:stock )?)?shares(?: of common stock)? (?:issued and )?"
    r"outstanding"
    r"(?:,? (?:ending|end of (?:period|quarter|year)|"
    r"at (?:period|quarter|year)[- ]end|at end of (?:period|quarter|year)))?"
    r"|outstanding (?:common )?shares(?: at (?:end of period|period[- ]end))?"
    r")\s*(?:\(\d\))?\s*:?\s*$")
_SHARE_ROW_NEVER = re.compile(
    r"(?i)weighted|average|preferred|treasury|authorized|diluted|basic")

# Units statement whose exception covers SHARE data (not just per-share):
# "except share and per share data", "except for share amounts". "except per
# share data" leaves share rows possibly in thousands → refused.
_UNIT_STMT = re.compile(r"(?i)(?:\bin|\(\$)\s?(thousands|millions|billions)\b")
_SHARE_EXCEPTED = re.compile(r"(?i)except[^)]*?(?<!per )(?<!per-)\bshares?\b")

# Total equity row (never "Total liabilities and …" — start-anchored).
_EQUITY_ROW = re.compile(
    r"(?i)^\s*total (?:common )?(?:(?:stock|share)holders['’]?,? |shareowners['’]? )?"
    r"equity\s*(?:\(\d\))?\s*$")

# Equity caption: a run of counts immediately before "shares (issued and)
# outstanding"; the tail carries the stated dates.
_NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+)"
# (?<![\d,]) — a count must start at a number's first digit, never mid-number.
# The dated tail is read from the stream AFTER the match (not consumed by the
# pattern), so one caption's tail can never swallow the next caption's count.
_CAPTION = re.compile(
    r"(?i)(?<![\d,])(?P<counts>" + _NUM + r"(?:\s*,\s*" + _NUM + r")*"
    r"(?:,?\s+and\s+" + _NUM + r")?)"
    r"\s+(?:common\s+)?shares\s+(?:issued\s+and\s+)?outstanding\b")
_TAIL_SPAN = 200
_TAIL_CUT = re.compile(r"(?i)respectively|\bshares?\b|\bstock\b|;|\n")
_NUMERIC_CELL = re.compile(r"^[\s$%()\-–—,.\d]*$")


def _table_scale(table_text: str) -> float | None:
    m = _UNIT_STMT.search(table_text)
    return ({"thousands": 1e3, "millions": 1e6, "billions": 1e9}[m.group(1).lower()]
            if m else None)


def _share_rows(rows, qends, hdr_i, n_change, col, table_text) -> list[float]:
    """Raw period-end common share counts from this table's rows at `col`."""
    if _table_scale(table_text) is not None and not _SHARE_EXCEPTED.search(table_text):
        return []                         # count may be in thousands → refuse
    out = []
    for cells in rows[hdr_i + 1:]:
        label_idx = next((i for i, c in enumerate(cells) if c), None)
        if label_idx is None:
            continue
        label = cells[label_idx]
        if _SHARE_ROW_NEVER.search(label) or not _SHARE_ROW.match(label):
            continue
        row_text = " ".join(cells)
        if "$" in row_text or "%" in row_text:
            continue
        vals = _row_values(cells[label_idx:])
        if len(vals) == len(qends) + n_change and n_change:
            vals = vals[:len(qends)]
        if len(vals) != len(qends):
            continue
        v = vals[col]
        if _SHARES_BAND[0] <= v <= _SHARES_BAND[1] and float(v).is_integer():
            out.append(v)
    return out


def _equity_rows(rows, qends, hdr_i, n_change, col, table_text) -> list[float]:
    """Total (common) equity candidates at `col`, in RAW dollars at every
    scale the table permits (stated unit → one value; none → ×1/×1e3/×1e6)."""
    scale = _table_scale(table_text)
    scales = [scale] if scale else [1.0, 1e3, 1e6]
    out = []
    for cells in rows[hdr_i + 1:]:
        label_idx = next((i for i, c in enumerate(cells) if c), None)
        if label_idx is None:
            continue
        label = cells[label_idx]
        if not _EQUITY_ROW.match(label):
            continue
        row_text = " ".join(cells)
        if "%" in row_text:
            continue
        vals = _row_values(cells[label_idx:])
        if len(vals) == len(qends) + n_change and n_change:
            vals = vals[:len(qends)]
        if len(vals) != len(qends):
            continue
        if vals[col] > 0:
            out.extend(vals[col] * s for s in scales)
    return out


def _caption_shares(rows, expected_qend: str) -> float | None:
    """Sum of the common-class counts the equity captions state for
    `expected_qend`; None when no common caption resolves to that date or
    any caption's count/date arity is unprovable."""
    stream = " ".join(
        c for cells in rows for c in cells
        if c and not _NUMERIC_CELL.match(c))
    total, found, prev_end = 0.0, False, 0
    for m in _CAPTION.finditer(stream):
        window = stream[max(prev_end, m.start() - 220):m.start()].lower()
        prev_end = m.end()
        if "preferred" in window or "treasury" in window:
            continue
        if "common" not in window:
            continue
        counts = [float(x.replace(",", ""))
                  for x in re.findall(_NUM, m.group("counts"))]
        tail = stream[m.end():m.end() + _TAIL_SPAN]
        cut = _TAIL_CUT.search(tail)
        tail = tail[:cut.start()] if cut else tail
        dates = [q for dm in _DTOK.finditer(tail)
                 if (q := _period_qend(dm.group(0)))]
        if not dates or expected_qend not in dates:
            continue
        if len(counts) == len(dates):
            v = counts[dates.index(expected_qend)]
        elif len(counts) == 1:
            v = counts[0]
        else:
            return None                   # arity unprovable → refuse all
        if v and not (_SHARES_BAND[0] <= v <= _SHARES_BAND[1]):
            return None
        total += v
        found = True
    return total if found and total > 0 else None


def extract_shares_outstanding(html: str, expected_qend: str,
                               per_share: dict | None = None) -> dict:
    """{"shares": float | None, "tie": {...}} — the release's common shares
    outstanding at `expected_qend`, served ONLY when they tie out against
    the release's own equity through its stated per-share book value
    (`per_share` = the release's extracted metrics: bv_ps / tbv_ps). `tie`
    records the proof (basis, per-share value, equity, diff) or the reason
    for refusal — provenance for every served count."""
    row_cands: list[float] = []
    caption_cands: list[float] = []
    equity_cands: list[float] = []
    for thtml in re.findall(r"(?is)<table[^>]*>(.*?)</table>", html or ""):
        rows = _table_rows(thtml)
        table_text = " ".join(" ".join(c for c in cells if c)
                              for cells in rows).lower()
        cap = _caption_shares(rows, expected_qend)
        if cap is not None:
            caption_cands.append(cap)
        hdr = _period_header(rows)
        if not hdr:
            continue
        qends, hdr_i, _span_i, n_change = hdr
        if qends.count(expected_qend) != 1:
            continue
        col = qends.index(expected_qend)
        row_cands += _share_rows(rows, qends, hdr_i, n_change, col, table_text)
        equity_cands += _equity_rows(rows, qends, hdr_i, n_change, col, table_text)

    def _agree(vs: list[float]) -> float | None:
        if not vs:
            return None
        return vs[0] if (max(vs) - min(vs)) <= _AGREE_TOL * max(vs) else None

    rows_v = _agree(row_cands)
    cap_v = _agree(caption_cands)
    if row_cands and rows_v is None:
        return {"shares": None, "tie": {"reason": "share rows disagree",
                                         "rows": row_cands}}
    if caption_cands and cap_v is None:
        return {"shares": None, "tie": {"reason": "captions disagree",
                                         "captions": caption_cands}}
    if rows_v is not None and cap_v is not None \
            and abs(rows_v - cap_v) > _AGREE_TOL * max(rows_v, cap_v):
        return {"shares": None, "tie": {"reason": "row vs caption disagree",
                                         "row": rows_v, "caption": cap_v}}
    shares = rows_v if rows_v is not None else cap_v
    if shares is None:
        return {"shares": None, "tie": {"reason": "no share count found"}}
    basis = [(k, (per_share or {}).get(k)) for k in ("bv_ps", "tbv_ps")
             if (per_share or {}).get(k)]
    if not basis:
        return {"shares": None, "tie": {"reason": "no stated per-share book value",
                                         "count": shares}}
    if not equity_cands:
        return {"shares": None, "tie": {"reason": "no total equity row",
                                         "count": shares}}
    best = None
    for k, ps in basis:
        for eq in equity_cands:
            diff = abs(ps * shares - eq) / eq
            if best is None or diff < best[0]:
                best = (diff, k, ps, eq)
    diff, k, ps, eq = best
    if diff <= _TIE_TOL:
        return {"shares": shares,
                "tie": {"basis": k, "per_share": ps, "equity": eq,
                        "diff_pct": round(diff * 100, 3),
                        "source": "row" if rows_v is not None else "caption"}}
    return {"shares": None,
            "tie": {"reason": "tie-out failed", "count": shares, "basis": k,
                    "per_share": ps, "equity": eq,
                    "diff_pct": round(diff * 100, 3)}}
