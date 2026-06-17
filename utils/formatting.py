"""
Number formatting and color coding helpers for the dashboard UI.
"""

from __future__ import annotations  # Lazy-evaluate all type hints (PEP 563).
                                     # Required because pd.io.formats.style.Styler
                                     # isn't always importable at module-load time
                                     # under older pandas / pinned Python versions.

import re

import pandas as pd
from config import METRICS_BY_KEY


# ── Bank name display formatter ──────────────────────────────────────────
# Names reach the UI from heterogeneous sources with inconsistent casing:
# SEC EDGAR's entityName is ALL CAPS ("PATRIOT NATIONAL BANCORP INC"), the
# curated BANK_MAP is Title Case ("Carter Bankshares Inc."), and FDIC/snapshot
# names carry "/DE/"-style state codes. Routing every name through this one
# formatter (see data.bank_mapping.get_name) makes the whole dashboard
# consistent without a data migration. The function is idempotent.

# Registration suffix EDGAR appends at the END, two forms:
#   • fully slash-enclosed marker: "… /DE/", "KEYCORP /NEW/"
#   • trailing 2-letter state, no closing slash: "WELLS FARGO & COMPANY/MN"
# A 2-letter cap on the unenclosed form keeps an internal slash safe — it must
# never strip "/Frost" from "CULLEN/FROST BANKERS".
_STATE_SUFFIX_RE = re.compile(r"(\s*/[A-Za-z0-9.]{1,9}/|\s*/[A-Za-z]{2})\s*$")

# Minor words kept lowercase unless first.
_MINOR_WORDS = {"of", "and", "the", "for", "in", "on", "at", "to", "a", "an"}

# Roman numerals (suffixes like "Capital Trust II") kept uppercase.
_ROMAN = {"ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"}

# Acronyms containing a vowel that must NOT be down-cased when the source is
# ALL CAPS (the no-vowel heuristic below catches HBT, FNB, SB, NBT, … already).
_ACRONYM_WHITELIST = {"USA", "US", "NA", "NY", "BOK", "UMB", "OZK", "ESB", "ESSA"}

# Trailing corporate-form tokens to drop entirely (longest phrases first so
# "& co" / "national association" win before "co"/"na"). Display only — the
# meaningful "Bancorp/Bancshares/Financial/Holdings/Group" stay.
_DROP_SUFFIXES = [
    "national association", "& company", "& co",
    "incorporated", "corporation", "company",
    "inc", "corp", "co", "ltd", "limited", "llc", "plc", "lp",
    "n.a.", "na",
]


def _title_word(word: str, is_first: bool, ticker: str | None) -> str:
    low = word.lower()
    if not is_first and low in _MINOR_WORDS:
        return low
    if low in _ROMAN:
        return word.upper()
    if word.upper() in _ACRONYM_WHITELIST:
        return word.upper()
    # A word equal to the ticker is the company's own acronym — keep it
    # uppercase (ACNB, ESSA, HBT), while real words whose ticker differs
    # (AMES/ATLO, ARROW/AROW) still title-case normally.
    if ticker and word.upper() == ticker.upper():
        return word.upper()
    # No-vowel short token ⇒ acronym (FNB, SB, NBT, WSFS, C&F, F&M).
    letters = re.sub(r"[^a-z]", "", low)
    if letters and len(letters) <= 5 and not re.search(r"[aeiou]", letters):
        return word.upper()
    # Default: capitalize each alphabetic run (handles hyphens, "&").
    return re.sub(r"[A-Za-z]+", lambda m: m.group(0).capitalize(), word)


def _drop_corporate_suffix(name: str) -> str:
    """Strip trailing corporate-form tokens repeatedly, but never reduce a
    name to empty (a name that IS only a suffix is left untouched)."""
    while True:
        base = name.rstrip(" ,.")
        low = base.lower()
        for phrase in _DROP_SUFFIXES:
            if low == phrase:
                return name  # whole name is the suffix — keep as-is
            if (low.endswith(" " + phrase) or low.endswith("," + phrase)
                    or low.endswith(", " + phrase)):
                name = base[: len(base) - len(phrase)].rstrip(" ,&")
                break
        else:
            return base


def format_bank_name(raw: str | None, ticker: str | None = None,
                     *, drop_suffix: bool = True) -> str:
    """Normalize a bank's display name: strip EDGAR state suffixes, Title-Case
    ALL-CAPS sources (preserving acronyms, minor words, roman numerals), and
    drop trailing corporate-form tokens (Inc./Corp./Co./& Co/National
    Association). Already-mixed-case names keep their casing (so intentional
    camel-case like "HomeTrust"/"BancFirst"/"InsCorp" survives). Passing the
    ticker keeps a name that IS the ticker acronym uppercase (ACNB, ESSA).
    Idempotent."""
    if not raw:
        return raw or ""
    name = str(raw).strip()
    while True:  # strip stacked markers, e.g. "… /CA/ /NEW/"
        stripped = _STATE_SUFFIX_RE.sub("", name).strip()
        if stripped == name:
            break
        name = stripped
    # Only re-case when the source is shouting; otherwise trust its casing.
    letters = re.sub(r"[^A-Za-z]", "", name)
    if letters and name == name.upper():
        words = name.split()
        name = " ".join(_title_word(w, i == 0, ticker)
                        for i, w in enumerate(words))
    if drop_suffix:
        name = _drop_corporate_suffix(name)
    return re.sub(r"\s+", " ", name).strip(" ,.&-/")


# ── Shared numeric primitives ────────────────────────────────────────────
# One implementation for the null-safe coercion/format helpers that were
# previously copy-pasted across financial_highlights / financials_statements /
# bank_detail (and defined twice in one of them).

def num(v) -> float | None:
    """Null-safe float: None for None/NaN/unparseable."""
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def thou(v) -> str:
    """Comma-grouped integer (FDIC's native $-in-thousands display), or —."""
    v = num(v)
    return f"{v:,.0f}" if v is not None else "—"


def pct(v, dp: int = 2) -> str:
    """Percentage with dp decimals, or —."""
    v = num(v)
    return f"{v:.{dp}f}%" if v is not None else "—"


def usd_compact_from_thousands(v_thousands) -> str:
    """FDIC $thousands → compact $X.XXB / $X.XM / $XK / $N, or —."""
    v = num(v_thousands)
    if v is None:
        return "—"
    d = v * 1000.0
    a = abs(d)
    if a >= 1e9:
        return f"${d/1e9:,.2f}B"
    if a >= 1e6:
        return f"${d/1e6:,.1f}M"
    # $1K–$999K compacts to $XK rather than spelling out the full figure
    # (e.g. $843K, not $843,000) so a sub-$1M cell matches the rest of the
    # column's compact convention. Below $1K shows the exact dollar amount.
    if a >= 1e3:
        return f"${d/1e3:,.0f}K"
    return f"${d:,.0f}"


def fmt_dollars(dollars: float | None, decimals: int = 2) -> str:
    """
    Auto-scale a dollar value to T / B / M / K / $.
    Input: raw dollars (not thousands).
    """
    if dollars is None or (isinstance(dollars, float) and pd.isna(dollars)):
        return "—"
    try:
        v = float(dollars)
    except (TypeError, ValueError):
        return "—"
    abs_v = abs(v)
    sign = "-" if v < 0 else ""
    if abs_v >= 1e12:
        return f"{sign}${abs_v/1e12:,.{decimals}f}T"
    elif abs_v >= 1e9:
        return f"{sign}${abs_v/1e9:,.{decimals}f}B"
    elif abs_v >= 1e6:
        return f"{sign}${abs_v/1e6:,.{decimals}f}M"
    elif abs_v >= 1e3:
        return f"{sign}${abs_v/1e3:,.0f}K"
    return f"{sign}${abs_v:,.0f}"


def fmt_dollars_from_thousands(amount_k: float | None, decimals: int = 2) -> str:
    """Format a thousands-of-dollars value with auto T/B/M/K scaling."""
    if amount_k is None:
        return "—"
    try:
        return fmt_dollars(float(amount_k) * 1000, decimals)
    except (TypeError, ValueError):
        return "—"


def format_value(value, fmt: str, decimals: int = 2) -> str:
    """Format a single value according to its format type."""
    # Boolean flag (e.g. one-time-earnings distortion) — render before the
    # numeric coercion below so False shows blank, not "—".
    if fmt == "flag":
        return "⚠️" if value else ""

    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"

    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)

    if fmt == "currency":
        return f"${value:,.{decimals}f}"
    elif fmt == "pct":
        return f"{value:.{decimals}f}%"
    elif fmt == "ratio":
        return f"{value:.{decimals}f}x"
    elif fmt == "millions":
        # Auto-upgrade to B if >= $1B
        if abs(value) >= 1e9:
            return f"${value / 1e9:,.{decimals}f}B"
        return f"${value / 1e6:,.{decimals}f}M"
    elif fmt == "billions":
        # Auto-upgrade to T if >= $1T
        if abs(value) >= 1e12:
            return f"${value / 1e12:,.{decimals}f}T"
        return f"${value / 1e9:,.{decimals}f}B"
    elif fmt == "dollars_auto":
        # Auto-scale dollars: T ≥ $1T, B ≥ $1B, M ≥ $1M, K ≥ $1K
        abs_v = abs(value)
        sign = "-" if value < 0 else ""
        if abs_v >= 1e12:
            return f"{sign}${abs_v / 1e12:,.{decimals}f}T"
        elif abs_v >= 1e9:
            return f"{sign}${abs_v / 1e9:,.{decimals}f}B"
        elif abs_v >= 1e6:
            return f"{sign}${abs_v / 1e6:,.{decimals}f}M"
        elif abs_v >= 1e3:
            return f"{sign}${abs_v / 1e3:,.0f}K"
        else:
            return f"{sign}${abs_v:,.0f}"
    elif fmt == "number":
        if value >= 1e6:
            return f"{value / 1e6:,.1f}M"
        elif value >= 1e3:
            return f"{value / 1e3:,.1f}K"
        return f"{value:,.{decimals}f}"
    return str(value)


def get_color(key: str, value) -> str:
    """
    Return a CSS color for a metric value based on its thresholds.
    Green = good, yellow = warning, red = bad, white = neutral.
    """
    m = METRICS_BY_KEY.get(key)
    if not m or not m.get("color_rule") or value is None:
        return ""

    try:
        value = float(value)
    except (TypeError, ValueError):
        return ""

    rule = m["color_rule"]
    thresholds = m.get("thresholds", {})
    good = thresholds.get("good")
    warn = thresholds.get("warn")

    if good is None or warn is None:
        return ""

    # Light theme — emerald / amber / red
    if rule == "higher_better":
        if value >= good:
            return "color: #059669"  # emerald
        elif value >= warn:
            return "color: #d97706"  # amber
        else:
            return "color: #dc2626"  # red
    elif rule == "lower_better":
        if value <= good:
            return "color: #059669"
        elif value <= warn:
            return "color: #d97706"
        else:
            return "color: #dc2626"

    return ""


def get_bg_color(key: str, value) -> str:
    """Return a subtle background color for conditional formatting in tables.
    Tuned for light theme — soft pastel overlays."""
    m = METRICS_BY_KEY.get(key)
    if not m or not m.get("color_rule") or value is None:
        return ""

    try:
        value = float(value)
    except (TypeError, ValueError):
        return ""

    rule = m["color_rule"]
    thresholds = m.get("thresholds", {})
    good = thresholds.get("good")
    warn = thresholds.get("warn")

    if good is None or warn is None:
        return ""

    _GOOD = "background-color: #ecfdf5; color: #065f46"
    _WARN = "background-color: #fffbeb; color: #92400e"
    _BAD  = "background-color: #fef2f2; color: #991b1b"

    if rule == "higher_better":
        if value >= good:
            return _GOOD
        elif value >= warn:
            return _WARN
        else:
            return _BAD
    elif rule == "lower_better":
        if value <= good:
            return _GOOD
        elif value <= warn:
            return _WARN
        else:
            return _BAD

    return ""


def style_dataframe(df: pd.DataFrame, columns: list[str]) -> pd.io.formats.style.Styler:
    """
    Apply conditional background coloring to a DataFrame for display in Streamlit.
    """
    def _apply_colors(row):
        styles = []
        for col in row.index:
            if col in columns and col in METRICS_BY_KEY:
                bg = get_bg_color(col, row[col])
                styles.append(bg)
            else:
                styles.append("")
        return styles

    return df.style.apply(_apply_colors, axis=1)


def format_dataframe_display(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """
    Create a display-ready copy of the DataFrame with formatted string values.
    """
    display_df = df.copy()
    for col in columns:
        m = METRICS_BY_KEY.get(col)
        if m:
            display_df[col] = display_df[col].apply(
                lambda v, m=m: format_value(v, m["format"], m.get("decimals", 2))
            )
    # Rename columns to labels
    rename_map = {}
    for col in columns:
        m = METRICS_BY_KEY.get(col)
        if m:
            rename_map[col] = m["label"]
    display_df = display_df.rename(columns=rename_map)
    return display_df
