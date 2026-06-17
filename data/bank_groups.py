"""
Bank Groups — named, saved lists of bank tickers used as a screening / compare scope.

A group is just a name + a normalized list of tickers, persisted firm-wide through
the same GCS-backed store as saved screens (``data/cloud_storage``). Groups are the
scope object shared by BOTH the Screen and Compare views: build a group by saving a
screen's survivors, by manual pick, or by editing an existing one, then screen or
compare within it.

No per-user identity — IAP gates the whole app to one firm, so groups are shared,
exactly like saved screens.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from data.cloud_storage import save_json, load_json, list_files, delete_json

GROUPS_PREFIX = "bank_groups"
PORTFOLIO_GROUP = "Portfolio"


def _normalize_tickers(tickers) -> list[str]:
    """Uppercase, strip, drop blanks, dedupe, sort — a group is an exact set of
    tickers, so normalization keeps the saved list faithful (no dupes, no casing
    drift) rather than guessing at the user's intent later."""
    seen: dict[str, None] = {}
    for t in tickers or []:
        if t is None:
            continue
        s = str(t).strip().upper()
        if s:
            seen[s] = None  # dict preserves insertion, dedupes
    return sorted(seen)


def _safe_filename(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (name or "").strip())
    return safe[:60]


def save_group(name: str, tickers, description: str = "") -> bool:
    """Create or overwrite a named group. Returns False on an empty name or a
    non-durable write (mirrors save_screen / save_json semantics)."""
    if not name or not name.strip():
        return False
    payload = {
        "name": name.strip(),
        "tickers": _normalize_tickers(tickers),
        "description": (description or "").strip(),
        "saved_at": datetime.now().isoformat(),
    }
    return save_json(GROUPS_PREFIX, f"{_safe_filename(name)}.json", payload)


def load_group(name: str) -> dict | None:
    data = load_json(GROUPS_PREFIX, f"{_safe_filename(name)}.json")
    if not data:
        return None
    data["tickers"] = _normalize_tickers(data.get("tickers"))
    return data


def get_group_tickers(name: str) -> list[str]:
    g = load_group(name)
    return g["tickers"] if g else []


def list_groups() -> list[dict]:
    """All groups as {name, count, saved_at, description}, most-recent first."""
    out = []
    for fname in list_files(GROUPS_PREFIX, pattern="*.json"):
        data = load_json(GROUPS_PREFIX, fname)
        if not data:
            continue
        out.append({
            "name": data.get("name", fname[:-5]),
            "count": len(_normalize_tickers(data.get("tickers"))),
            "saved_at": data.get("saved_at", ""),
            "description": data.get("description", ""),
        })
    out.sort(key=lambda g: g.get("saved_at", ""), reverse=True)
    return out


def delete_group(name: str) -> bool:
    return delete_json(GROUPS_PREFIX, f"{_safe_filename(name)}.json")


def rename_group(old: str, new: str) -> bool:
    if not new or not new.strip():
        return False
    g = load_group(old)
    if not g:
        return False
    ok = save_group(new, g.get("tickers", []), g.get("description", ""))
    if ok and _safe_filename(new) != _safe_filename(old):
        delete_group(old)
    return ok


def add_tickers(name: str, tickers) -> bool:
    g = load_group(name)
    if not g:
        return False
    merged = list(g.get("tickers", [])) + list(tickers or [])
    return save_group(name, merged, g.get("description", ""))


def remove_tickers(name: str, tickers) -> bool:
    g = load_group(name)
    if not g:
        return False
    drop = set(_normalize_tickers(tickers))
    kept = [t for t in g.get("tickers", []) if t not in drop]
    return save_group(name, kept, g.get("description", ""))


def ensure_portfolio_seed() -> None:
    """Seed a "Portfolio" group from the legacy ``portfolio.json`` the first time,
    so the real holdings list (previously ignored by the hardcoded ``portfolio = []``)
    becomes a usable scope. No-op if the group already exists or the file is
    missing / empty / malformed."""
    if load_group(PORTFOLIO_GROUP):
        return
    pj = Path(__file__).parent.parent / "portfolio.json"
    if not pj.exists():
        return
    try:
        tickers = json.loads(pj.read_text())
    except Exception:
        return
    if isinstance(tickers, list) and tickers:
        save_group(PORTFOLIO_GROUP, tickers, "Seeded from portfolio.json")
