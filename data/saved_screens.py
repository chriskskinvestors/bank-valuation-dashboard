"""
Saved screens — named, versioned, re-runnable screen templates persisted to
local + GCS storage.

A saved screen stores the *how* of a screen (the scope is chosen live and lives
separately as a Bank Group):
  - name (user-chosen label)
  - table key (which screening tab it was built from)
  - sort metric + direction
  - list of metric filters (each a typed spec: absolute / peer-relative / change / trend)
  - selected columns (for the custom column picker)

Versioning: re-saving under an existing name bumps ``version`` and archives the
prior config into a capped ``history`` list, so a screen is a re-runnable template
you can also roll back. ``created_at`` is preserved; ``updated_at`` tracks edits.
"""

from __future__ import annotations
from datetime import datetime

from data.cloud_storage import save_json, load_json, list_files, delete_json

SCREENS_PREFIX = "saved_screens"
_MAX_HISTORY = 10  # prior versions kept per screen


def save_screen(name: str, config: dict) -> bool:
    """Create or update a named screen. Re-saving an existing name bumps its
    version and archives the prior config. Returns False on empty name or a
    non-durable write."""
    if not name or not name.strip():
        return False
    safe = _safe_filename(name)
    now = datetime.now().isoformat()
    existing = load_json(SCREENS_PREFIX, f"{safe}.json")

    if existing:
        prev_version = int(existing.get("version", 1))
        created_at = existing.get("created_at") or existing.get("saved_at", now)
        history = list(existing.get("history", []))
        history.append({
            "version": prev_version,
            "saved_at": existing.get("updated_at") or existing.get("saved_at", now),
            "config": existing.get("config", {}),
        })
        history = history[-_MAX_HISTORY:]
        version = prev_version + 1
    else:
        version = 1
        created_at = now
        history = []

    payload = {
        "name": name,
        "version": version,
        "created_at": created_at,
        "updated_at": now,
        "saved_at": now,          # back-compat with pre-versioning readers
        "config": config,
        "history": history,
    }
    return bool(save_json(SCREENS_PREFIX, f"{safe}.json", payload))


def load_screen(name: str, version: int | None = None) -> dict | None:
    """Return a screen's config — the current version, or a specific prior
    ``version`` from history. None if the screen / version doesn't exist."""
    data = load_json(SCREENS_PREFIX, f"{_safe_filename(name)}.json")
    if not data:
        return None
    if version is None or int(version) == int(data.get("version", 1)):
        return data.get("config") or {}
    for h in data.get("history", []):
        if int(h.get("version", -1)) == int(version):
            return h.get("config") or {}
    return None


def screen_versions(name: str) -> list[dict]:
    """[{version, saved_at, current}] for a screen, newest first."""
    data = load_json(SCREENS_PREFIX, f"{_safe_filename(name)}.json")
    if not data:
        return []
    out = [{
        "version": int(data.get("version", 1)),
        "saved_at": data.get("updated_at") or data.get("saved_at", ""),
        "current": True,
    }]
    for h in reversed(data.get("history", [])):
        out.append({
            "version": int(h.get("version", 0)),
            "saved_at": h.get("saved_at", ""),
            "current": False,
        })
    return out


def list_screens() -> list[dict]:
    """All saved screens: {name, saved_at, version, tab, filter_count, filename}."""
    results = []
    for fname in list_files(SCREENS_PREFIX, pattern="*.json"):
        data = load_json(SCREENS_PREFIX, fname)
        if not data:
            continue
        cfg = data.get("config") or {}
        results.append({
            "name": data.get("name", fname.replace(".json", "")),
            "saved_at": data.get("updated_at") or data.get("saved_at", ""),
            "version": int(data.get("version", 1)),
            "tab": cfg.get("tab_key"),
            "filter_count": len(cfg.get("filters", [])),
            "filename": fname,
        })
    results.sort(key=lambda x: x.get("saved_at", ""), reverse=True)
    return results


def delete_screen(name: str) -> bool:
    delete_json(SCREENS_PREFIX, f"{_safe_filename(name)}.json")
    return True


def _safe_filename(name: str) -> str:
    """Convert a screen name to a safe filename."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name.strip())
    return safe[:60]
