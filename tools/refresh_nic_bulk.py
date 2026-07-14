"""
Refresh the GCS mirror of the Fed NIC bulk files (Corporate Structure tab).

NPW's Cloudflare bot management 403s Cloud Run egress IPs outright (curl
included — prod logs 2026-07-14), so production can never download the
bulk zips itself; data/nic_client.py reads the mirror this script fills.
Run it from any network Cloudflare doesn't block:

  - monthly via .github/workflows/refresh-nic-bulk.yml (GitHub runner)
  - manually from the dev box when the workflow's egress gets blocked too:
      $env:GCS_BUCKET='ksk-bank-dashboard-data'; python -m tools.refresh_nic_bulk

Fetches curl-only (python TLS fingerprints are guaranteed 403s that burn
per-IP bot score), validates each zip's CSV header against the exact
columns nic_client parses, and only then uploads. A bad fetch exits
non-zero and NEVER overwrites the last good mirror object.
"""
from __future__ import annotations

import io
import sys
import time
import zipfile

from data.nic_client import ATTR_COLS, BULK_URLS, REL_COLS, _curl_fetch
from data.cloud_storage import is_gcs_enabled, save_bytes

_GCS_PREFIX = "nic_bulk"
_EXPECTED_COLS = {"relationships": REL_COLS, "attributes_active": ATTR_COLS}
_MIN_BYTES = 1_000_000  # both zips are several MB; a challenge page is not
# Back-to-back hits trip Cloudflare's bot scoring even on allowed networks
# (verified 2026-07-14: first fetch OK, immediate second one 403). Space the
# fetches out and retry a 403 once after a longer cool-down.
_SPACING_SECONDS = 30
_RETRY_WAIT_SECONDS = 90


def _fetch(name: str, url: str) -> bytes | None:
    content = _curl_fetch(url, name, b"PK")
    if content is None:
        print(f"     {name}: retrying once in {_RETRY_WAIT_SECONDS}s "
              "(bot-score cool-down)")
        time.sleep(_RETRY_WAIT_SECONDS)
        content = _curl_fetch(url, name, b"PK")
    return content


def _validate(name: str, content: bytes) -> str | None:
    """None when the zip is a plausible NIC bulk file, else the problem."""
    if len(content) < _MIN_BYTES:
        return f"only {len(content)} bytes — too small to be the bulk file"
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
        names = [n for n in zf.namelist() if not n.endswith("/")]
        if len(names) != 1:
            return f"expected a single CSV member, got {names!r}"
        with zf.open(names[0]) as fh:
            header = fh.readline().decode("utf-8-sig", errors="replace")
    except Exception as e:  # noqa: BLE001 — any unzip failure is a bad file
        return f"unreadable zip: {type(e).__name__}: {e}"
    missing = [c for c in _EXPECTED_COLS[name] if c not in header]
    if missing:
        return f"CSV header missing column(s) {missing} — layout changed?"
    return None


def main() -> int:
    if not is_gcs_enabled():
        print("GCS_BUCKET is not set — nothing to mirror to.")
        return 1

    failures = 0
    for i, (name, url) in enumerate(BULK_URLS.items()):
        if i:
            time.sleep(_SPACING_SECONDS)
        content = _fetch(name, url)
        if content is None:
            print(f"FAIL {name}: download failed (Cloudflare block from this "
                  "network? run from an unblocked one)")
            failures += 1
            continue
        problem = _validate(name, content)
        if problem:
            print(f"FAIL {name}: {problem} — NOT uploaded")
            failures += 1
            continue
        if save_bytes(_GCS_PREFIX, f"{name}.zip", content, "application/zip"):
            print(f"OK   {name}: {len(content):,} bytes → "
                  f"{_GCS_PREFIX}/{name}.zip")
        else:
            print(f"FAIL {name}: GCS upload failed")
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
