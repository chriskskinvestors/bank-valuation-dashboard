"""Pro-forma merger screening on FDIC Summary-of-Deposits data.

Data layer for Company ▸ Market Analysis ▸ "Merger Planning / HHI / Market
Overlap" (SNL plan §11). Plain data out (dicts + DataFrames) — no Streamlit.

Deposits throughout are SOD $thousands, exactly as stored by
data/branches_store; shares and HHI are scale-free so no unit conversion
happens here.

HHI convention — THE one implementation, shared with the shipped Deposit
Market Share tab (ui/deposit_market_share.py imports hhi_from_deposits from
here): HHI = Σ (share_i in %)² over all insured institutions in the market,
the DOJ 0–10,000 scale. Concentration bands per the shipped caption:
<1,500 unconcentrated, 1,500–2,500 moderately concentrated, >2,500 highly
concentrated.

Merger screen (per the approved DOJ bank-merger convention): a market is
flagged when post-merger HHI > 1,800 with ΔHHI > 100; reported at the
stricter severity when post-merger HHI > 2,500 with ΔHHI > 200.

Honesty rule (cardinal): a market whose inputs are absent or degenerate
(zero total, zero recorded deposits for either party, incomputable HHI) is
skipped with a reason — never rendered as a fabricated 0% share.
"""
from __future__ import annotations

import pandas as pd

# Concentration bands (0–10,000 scale) — shipped caption convention.
HHI_MODERATE = 1500.0
HHI_HIGH = 2500.0
# Merger screen thresholds.
SCREEN_POST = 1800.0
SCREEN_DELTA = 100.0
SCREEN_POST_HIGH = 2500.0
SCREEN_DELTA_HIGH = 200.0

OVERLAP_COLS = [
    "market_key", "market_label", "n_banks", "market_total",
    "branches_a", "deposits_a", "share_a_pct",
    "branches_b", "deposits_b", "share_b_pct",
]
PRO_FORMA_COLS = OVERLAP_COLS + [
    "combined_share_pct", "hhi_pre", "hhi_post", "hhi_delta",
    "concentration_post", "screen_flag", "screen_reason",
]


def hhi_from_deposits(deposits) -> float | None:
    """Herfindahl–Hirschman index from a market's participant deposits.

    HHI = Σ (share_i in %)² over every participant — DOJ 0–10,000 scale.
    Accepts any iterable (list, pandas Series; Decimal values fine).
    Returns None when the total is non-positive or any participant's
    deposits are absent (None/NaN): an incomputable market is reported as
    None, never a fabricated number.
    """
    vals: list[float] = []
    for d in deposits:
        if d is None:
            return None
        f = float(d)
        if f != f:  # NaN
            return None
        vals.append(f)
    total = sum(vals)
    if total <= 0:
        return None
    return sum((v / total * 100.0) ** 2 for v in vals)


def concentration_band(hhi: float) -> str:
    """DOJ band label for an HHI level (shipped caption convention)."""
    if hhi > HHI_HIGH:
        return "highly concentrated"
    if hhi >= HHI_MODERATE:
        return "moderately concentrated"
    return "unconcentrated"


def classify_screen(hhi_post: float, hhi_delta: float
                    ) -> tuple[bool, str | None]:
    """(flagged, reason) under the DOJ bank-merger screen. Thresholds are
    strict inequalities; the stricter condition wins the reason string."""
    if hhi_post > SCREEN_POST_HIGH and hhi_delta > SCREEN_DELTA_HIGH:
        return True, "post-merger HHI > 2,500 with ΔHHI > 200"
    if hhi_post > SCREEN_POST and hhi_delta > SCREEN_DELTA:
        return True, "post-merger HHI > 1,800 with ΔHHI > 100"
    return False, None


def _result(kind: str, year: int | None, cols: list[str],
            markets: pd.DataFrame | None = None,
            skipped: list[dict] | None = None,
            reason: str | None = None) -> dict:
    if markets is None:
        markets = pd.DataFrame(columns=cols)
    return {"markets": markets, "skipped": skipped or [],
            "reason": reason, "year": year, "kind": kind}


def _overlap_rows(cert_a: int, cert_b: int, kind: str, year: int
                  ) -> tuple[list[dict], list[dict], str | None]:
    """Shared core: per-overlapping-market dicts (with private _deposits /
    _others participant lists for the HHI pass), skipped list, reason."""
    from data.branches_store import get_market_participants, has_branches

    parts = get_market_participants(cert_a, kind=kind, year=year)
    if parts is None or parts.empty:
        return [], [], f"no SOD rows for cert {cert_a} in {year}"
    if not has_branches(cert_b, year):
        return [], [], f"no SOD rows for cert {cert_b} in {year}"
    # Prod Postgres returns SUM(BIGINT) as Decimal; coerce once (same
    # boundary coercion the shipped share table does).
    parts = parts.assign(deposits=parts["deposits"].astype(float))

    rows: list[dict] = []
    skipped: list[dict] = []
    for key, m in parts.groupby("market_key", sort=False):
        b_rows = m[m["cert"] == cert_b]
        if b_rows.empty:
            continue  # cert_b absent — not an overlap market
        a_rows = m[m["cert"] == cert_a]  # always present: markets come from a
        label = m["market_label"].iloc[0]
        dep_a = float(a_rows["deposits"].iloc[0])
        dep_b = float(b_rows["deposits"].iloc[0])
        total = float(m["deposits"].sum())
        if total <= 0:
            skipped.append({"market_key": key, "market_label": label,
                            "reason": "market total deposits non-positive"})
            continue
        zeros = [c for c, d in ((cert_a, dep_a), (cert_b, dep_b)) if d <= 0]
        if zeros:
            skipped.append({
                "market_key": key, "market_label": label,
                "reason": "zero recorded deposits for cert "
                          + " and cert ".join(str(c) for c in zeros)})
            continue
        others = m.loc[~m["cert"].isin([cert_a, cert_b]),
                       "deposits"].tolist()
        rows.append({
            "market_key": key,
            "market_label": label,
            "n_banks": int(m["cert"].nunique()),
            "market_total": total,
            "branches_a": int(a_rows["n_branches"].iloc[0]),
            "deposits_a": dep_a,
            "share_a_pct": dep_a / total * 100.0,
            "branches_b": int(b_rows["n_branches"].iloc[0]),
            "deposits_b": dep_b,
            "share_b_pct": dep_b / total * 100.0,
            "_deposits": m["deposits"].tolist(),
            "_others": others,
        })
    reason = None
    if not rows:
        reason = ("no overlapping markets with recorded deposits "
                  "for both banks")
    return rows, skipped, reason


def _common_guards(cert_a: int, cert_b: int, kind: str,
                   year: int | None) -> tuple[int | None, str | None]:
    """Resolve the survey year; return (year, reason). reason set on a
    degenerate call (same cert / empty store / bad kind)."""
    from data.branches_store import get_latest_year

    if kind not in ("county", "msa"):
        return None, f"unknown market kind {kind!r}"
    if int(cert_a) == int(cert_b):
        return None, "cert_a and cert_b are the same institution"
    y = int(year) if year else get_latest_year()
    if y is None:
        return None, "branches store is empty"
    return y, None


def market_overlap(cert_a: int, cert_b: int, kind: str = "county",
                   year: int | None = None) -> dict:
    """Markets (county FIPS or MSA) where BOTH banks take deposits.

    Returns {"markets": DataFrame[OVERLAP_COLS], "skipped": [ {market_key,
    market_label, reason}, ... ], "reason": str|None (why markets is empty,
    when it is), "year": int|None, "kind": kind}. Deposits in $thousands;
    market_total spans ALL insured institutions in the market, not just the
    two parties. Sorted by combined party deposits, largest first.
    """
    y, reason = _common_guards(cert_a, cert_b, kind, year)
    if reason:
        return _result(kind, y, OVERLAP_COLS, reason=reason)
    rows, skipped, reason = _overlap_rows(int(cert_a), int(cert_b), kind, y)
    if not rows:
        return _result(kind, y, OVERLAP_COLS, skipped=skipped, reason=reason)
    df = pd.DataFrame([{k: v for k, v in r.items()
                        if not k.startswith("_")} for r in rows],
                      columns=OVERLAP_COLS)
    df = (df.assign(_combined=df["deposits_a"] + df["deposits_b"])
            .sort_values("_combined", ascending=False)
            .drop(columns="_combined")
            .reset_index(drop=True))
    return _result(kind, y, OVERLAP_COLS, markets=df, skipped=skipped)


def pro_forma_hhi(cert_a: int, cert_b: int, kind: str = "county",
                  year: int | None = None) -> dict:
    """Pre/post-merger HHI per overlapping market for a cert_a + cert_b
    combination.

    Returns {"markets": DataFrame[PRO_FORMA_COLS], "skipped": [...],
    "reason": str|None, "year": int|None, "kind": kind}. Per market:
    hhi_pre over all participants, hhi_post with the two parties combined
    into one participant, hhi_delta = post − pre (≥ 0), concentration_post
    band label, screen_flag/screen_reason per classify_screen. Sorted by
    hhi_delta, largest first (screening priority).
    """
    y, reason = _common_guards(cert_a, cert_b, kind, year)
    if reason:
        return _result(kind, y, PRO_FORMA_COLS, reason=reason)
    rows, skipped, reason = _overlap_rows(int(cert_a), int(cert_b), kind, y)
    out: list[dict] = []
    for r in rows:
        pre = hhi_from_deposits(r["_deposits"])
        post = hhi_from_deposits(
            [r["deposits_a"] + r["deposits_b"]] + r["_others"])
        if pre is None or post is None:
            skipped.append({"market_key": r["market_key"],
                            "market_label": r["market_label"],
                            "reason": "HHI incomputable"})
            continue
        delta = post - pre
        flag, screen_reason = classify_screen(post, delta)
        row = {k: v for k, v in r.items() if not k.startswith("_")}
        row.update({
            "combined_share_pct": r["share_a_pct"] + r["share_b_pct"],
            "hhi_pre": pre,
            "hhi_post": post,
            "hhi_delta": delta,
            "concentration_post": concentration_band(post),
            "screen_flag": flag,
            "screen_reason": screen_reason,
        })
        out.append(row)
    if not out:
        return _result(kind, y, PRO_FORMA_COLS, skipped=skipped,
                       reason=reason or "no computable overlapping markets")
    df = (pd.DataFrame(out, columns=PRO_FORMA_COLS)
          .sort_values("hhi_delta", ascending=False)
          .reset_index(drop=True))
    # pandas coerces None → NaN on construction; keep the contract honest:
    # an unflagged market's screen_reason is None, never NaN.
    df["screen_reason"] = (df["screen_reason"].astype(object)
                           .where(df["screen_reason"].notna(), None))
    return _result(kind, y, PRO_FORMA_COLS, markets=df, skipped=skipped)
