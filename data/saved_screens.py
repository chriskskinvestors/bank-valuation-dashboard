"""
Saved screens — persist named filter configurations to local + GCS storage.

A saved screen stores:
  - name (user-chosen label)
  - table key (which screening tab it was built from)
  - bank filter (Watchlist / Portfolio / All Banks)
  - sort metric + direction
  - list of metric filters: (metric_key, operator, value)
  - selected columns (for custom column picker)
"""

from __future__ import annotations
import json
from datetime import datetime

from data.cloud_storage import save_json, load_json, list_files, delete_json

SCREENS_PREFIX = "saved_screens"


def save_screen(name: str, config: dict) -> bool:
    """Save a screen config under a user-provided name."""
    if not name or not name.strip():
        return False
    safe = _safe_filename(name)
    payload = {
        "name": name,
        "saved_at": datetime.now().isoformat(),
        "config": config,
    }
    save_json(SCREENS_PREFIX, f"{safe}.json", payload)
    return True


def load_screen(name: str) -> dict | None:
    safe = _safe_filename(name)
    data = load_json(SCREENS_PREFIX, f"{safe}.json")
    if not data:
        return None
    return data.get("config") or {}


def list_screens() -> list[dict]:
    """Return all saved screens: {name, saved_at, tab, filter_count}."""
    results = []
    for fname in list_files(SCREENS_PREFIX, pattern="*.json"):
        data = load_json(SCREENS_PREFIX, fname)
        if not data:
            continue
        cfg = data.get("config") or {}
        results.append({
            "name": data.get("name", fname.replace(".json", "")),
            "saved_at": data.get("saved_at", ""),
            "tab": cfg.get("tab_key"),
            "filter_count": len(cfg.get("filters", [])),
            "filename": fname,
        })
    # Sort by most recent first
    results.sort(key=lambda x: x.get("saved_at", ""), reverse=True)
    return results


def delete_screen(name: str) -> bool:
    safe = _safe_filename(name)
    delete_json(SCREENS_PREFIX, f"{safe}.json")
    return True


def _safe_filename(name: str) -> str:
    """Convert a screen name to a safe filename."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name.strip())
    return safe[:60]
