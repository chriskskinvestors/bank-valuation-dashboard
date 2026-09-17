"""Non-root container contract (pre-assessment security sweep, 2026-09-16).

The image runs as an unprivileged `app` user over a root-owned, read-only
code tree; only the runtime write paths are chowned. Two things keep that
from silently breaking production:

  1. every save_json cache prefix in the code is in the Dockerfile's
     writable list — so the local cache copy keeps working;
  2. save_json never lets a failed LOCAL write abort the durable GCS write
     (a prefix added later without a Dockerfile entry must degrade to
     "no local cache", not "data silently not persisted").
"""
import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")


def _save_json_prefixes() -> set[str]:
    """Resolve every save_json(<PREFIX_CONSTANT>, ...) call to its string."""
    prefixes = set()
    for py in list((ROOT / "data").rglob("*.py")) + list((ROOT / "ui").rglob("*.py")) \
            + list((ROOT / "jobs").rglob("*.py")) + [ROOT / "app.py"]:
        src = py.read_text(encoding="utf-8")
        consts = dict(re.findall(r'^([A-Z][A-Z0-9_]*)\s*=\s*["\']([a-z0-9_]+)["\']',
                                 src, flags=re.M))
        for arg in re.findall(r"save_json\(\s*([A-Za-z0-9_\"']+)\s*,", src):
            if arg.strip("\"'") != arg:
                prefixes.add(arg.strip("\"'"))
            elif arg in consts:
                prefixes.add(consts[arg])
            elif arg != "prefix":                 # save_json's own signature
                raise AssertionError(f"{py.name}: unresolved save_json prefix {arg}")
    return prefixes


class TestDockerfileNonRoot(unittest.TestCase):
    def test_runs_as_unprivileged_user_after_setup(self):
        user_at = DOCKERFILE.find("\nUSER app")
        self.assertGreater(user_at, 0, "Dockerfile has no `USER app`")
        self.assertGreater(user_at, DOCKERFILE.find("COPY . ."),
                           "USER must come after the code COPY and chown")
        self.assertIn("useradd", DOCKERFILE)
        self.assertIn("HOME=/home/app", DOCKERFILE)

    def test_every_cache_prefix_is_writable(self):
        # The chown spans backslash-continued lines: take every line that
        # ends in "\" plus the final one.
        chown = re.search(r"chown -R app:app((?:[^\n]*\\\n)*[^\n]*)", DOCKERFILE)
        self.assertIsNotNone(chown, "no chown of runtime paths")
        owned = set(re.findall(r"/app/([a-z0-9_]+)", chown.group(1)))
        prefixes = _save_json_prefixes()
        self.assertGreaterEqual(len(prefixes), 10)          # the scan works
        self.assertEqual(set(), prefixes - owned,
                         "save_json prefixes missing from the Dockerfile chown")
        self.assertIn("tests", owned)          # verify-metrics / live-audit CSVs


class TestSaveJsonLocalFailure(unittest.TestCase):
    def test_gcs_write_survives_unwritable_local_dir(self):
        import data.cloud_storage as cs
        bucket = MagicMock()
        with patch.object(cs, "is_gcs_enabled", return_value=True), \
             patch.object(cs, "_get_bucket", return_value=bucket), \
             patch("pathlib.Path.mkdir", side_effect=PermissionError("ro")):
            ok = cs.save_json("brand_new_prefix", "x.json", {"a": 1})
        self.assertTrue(ok)
        bucket.blob.assert_called_once_with("brand_new_prefix/x.json")
        bucket.blob.return_value.upload_from_string.assert_called_once()

    def test_local_only_mode_reports_failure(self):
        import data.cloud_storage as cs
        with patch.object(cs, "is_gcs_enabled", return_value=False), \
             patch("pathlib.Path.mkdir", side_effect=PermissionError("ro")):
            self.assertFalse(cs.save_json("brand_new_prefix", "x.json", {"a": 1}))


if __name__ == "__main__":
    unittest.main()
