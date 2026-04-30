"""
Cloud Storage abstraction for persistent data.

Uses Google Cloud Storage when GCS_BUCKET env var is set (Cloud Run),
falls back to local filesystem otherwise (local dev).

All data stored as JSON files under prefixes:
  consensus/{TICKER}_{PERIOD}.json
  estimates_cache/{TICKER}.json
  watchlist.json
  portfolio.json
"""

import json
import os
from pathlib import Path

# GCS bucket name — set this in Cloud Run env vars
GCS_BUCKET = os.environ.get("GCS_BUCKET", "")

_gcs_client = None
_gcs_bucket = None


def _get_bucket():
    """Lazy-init GCS bucket connection."""
    global _gcs_client, _gcs_bucket
    if _gcs_bucket is not None:
        return _gcs_bucket
    try:
        from google.cloud import storage

        # On Cloud Run, default credentials work automatically.
        # Locally, try to use gcloud CLI credentials.
        try:
            _gcs_client = storage.Client(project=os.environ.get("GCLOUD_PROJECT", "ace-beanbag-486220-a8"))
        except Exception:
            # Try using gcloud CLI auth as fallback
            import google.auth
            import google.auth.transport.requests
            from google.oauth2 import credentials as oauth2_creds
            import subprocess, json as _json

            # Get access token — try token file first, then gcloud CLI
            token = None

            # Method 1: Read from token file (written by deploy script or bash)
            token_file = Path(__file__).parent.parent / ".gcs_token"
            if token_file.exists():
                token = token_file.read_text().strip()

            # Method 2: Try gcloud CLI
            if not token:
                gcloud_paths = [
                    "gcloud",
                    os.path.expanduser("~/google-cloud-sdk-install/google-cloud-sdk/bin/gcloud.cmd"),
                    r"C:\Users\chris\google-cloud-sdk-install\google-cloud-sdk\bin\gcloud.cmd",
                    "/usr/bin/gcloud",
                    "/usr/local/bin/gcloud",
                ]
                for gpath in gcloud_paths:
                    try:
                        result = subprocess.run(
                            [gpath, "auth", "print-access-token"],
                            capture_output=True, text=True, timeout=10,
                        )
                        if result.returncode == 0 and result.stdout.strip():
                            token = result.stdout.strip()
                            break
                    except (FileNotFoundError, OSError):
                        continue

            if token:
                creds = oauth2_creds.Credentials(token=token)
                _gcs_client = storage.Client(
                    project="ace-beanbag-486220-a8",
                    credentials=creds,
                )
            else:
                raise Exception("Could not get gcloud access token")

        _gcs_bucket = _gcs_client.bucket(GCS_BUCKET)
        return _gcs_bucket
    except Exception as e:
        print(f"[GCS] Could not connect to bucket {GCS_BUCKET}: {e}")
        return None


def is_gcs_enabled() -> bool:
    """Check if GCS storage is configured."""
    return bool(GCS_BUCKET)


# ── Write ──────────────────────────────────────────────────────────────

def save_json(prefix: str, filename: str, data: dict) -> bool:
    """Save a JSON file. Uses GCS if available, local filesystem otherwise."""
    # Always save locally too (for dev and as cache)
    local_dir = Path(__file__).parent.parent / prefix
    local_dir.mkdir(exist_ok=True)
    local_path = local_dir / filename
    local_path.write_text(json.dumps(data, indent=2, default=str))

    # Also save to GCS if enabled
    if is_gcs_enabled():
        try:
            bucket = _get_bucket()
            if bucket:
                blob = bucket.blob(f"{prefix}/{filename}")
                blob.upload_from_string(
                    json.dumps(data, indent=2, default=str),
                    content_type="application/json",
                )
                return True
        except Exception as e:
            print(f"[GCS] Error saving {prefix}/{filename}: {e}")

    return True


# ── Read ───────────────────────────────────────────────────────────────

def load_json(prefix: str, filename: str) -> dict | None:
    """Load a JSON file. Tries GCS first, falls back to local."""
    if is_gcs_enabled():
        try:
            bucket = _get_bucket()
            if bucket:
                blob = bucket.blob(f"{prefix}/{filename}")
                if blob.exists():
                    return json.loads(blob.download_as_text())
        except Exception as e:
            print(f"[GCS] Error loading {prefix}/{filename}: {e}")

    # Fall back to local
    local_path = Path(__file__).parent.parent / prefix / filename
    if local_path.exists():
        return json.loads(local_path.read_text())

    return None


# ── List ───────────────────────────────────────────────────────────────

def list_files(prefix: str, pattern: str = "*.json") -> list[str]:
    """List filenames in a prefix. Merges GCS + local results."""
    filenames = set()

    # Local files
    local_dir = Path(__file__).parent.parent / prefix
    if local_dir.exists():
        import fnmatch
        for f in local_dir.iterdir():
            if fnmatch.fnmatch(f.name, pattern):
                filenames.add(f.name)

    # GCS files
    if is_gcs_enabled():
        try:
            bucket = _get_bucket()
            if bucket:
                blobs = bucket.list_blobs(prefix=f"{prefix}/")
                for blob in blobs:
                    name = blob.name.replace(f"{prefix}/", "", 1)
                    if name and not name.endswith("/"):
                        import fnmatch
                        if fnmatch.fnmatch(name, pattern):
                            filenames.add(name)
        except Exception as e:
            print(f"[GCS] Error listing {prefix}/: {e}")

    return sorted(filenames)


# ── Delete ─────────────────────────────────────────────────────────────

def delete_json(prefix: str, filename: str) -> bool:
    """Delete a JSON file from both GCS and local."""
    # Local
    local_path = Path(__file__).parent.parent / prefix / filename
    if local_path.exists():
        local_path.unlink()

    # GCS
    if is_gcs_enabled():
        try:
            bucket = _get_bucket()
            if bucket:
                blob = bucket.blob(f"{prefix}/{filename}")
                if blob.exists():
                    blob.delete()
        except Exception as e:
            print(f"[GCS] Error deleting {prefix}/{filename}: {e}")

    return True
