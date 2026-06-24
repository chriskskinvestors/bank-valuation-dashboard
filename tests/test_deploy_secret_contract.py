"""Pins the deploy.yml secret contract — regression lock for the 2026-06-24
blank-prices outage.

Root cause then: the deploy's secrets-arg builder appended a secret to
`--set-secrets` only when `gcloud secrets describe` succeeded and *silently
skipped* it otherwise. Because `--set-secrets` replaces the entire set, a deploy
where any describe failed shipped the service MISSING that key — FMP_API_KEY
absent → every price fetch empty → blank prices app-wide.

The fix (ce1a90d) made a missing/inaccessible required secret FAIL the deploy
loudly instead. These tests lock that contract so a future edit can't quietly
re-introduce the silent-drop behaviour:
  1. all required secrets are still listed in the builder loop,
  2. the builder fails loudly (::error:: + exit 1) when any is missing,
  3. the assembled secrets arg is actually wired into `gcloud run deploy`.
"""
import re
import unittest
from pathlib import Path

DEPLOY_YML = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "deploy.yml"

# Every secret the running SERVICE must have mounted. Dropping any of these is
# what caused the outage; the app reads them as the FMP_API_KEY etc. env vars.
REQUIRED_SECRETS = {
    "anthropic-api-key",
    "fred-api-key",
    "fmp-api-key",
    "ffiec-username",
    "ffiec-jwt-token",
}


class TestDeploySecretContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DEPLOY_YML.read_text(encoding="utf-8")

    def test_deploy_yml_exists(self):
        self.assertTrue(DEPLOY_YML.is_file(), f"missing {DEPLOY_YML}")

    def test_all_required_secrets_are_listed(self):
        # The builder iterates `for s in <names>; do`. Pull that list and assert
        # every required secret is present — a removed name = a droppable secret.
        m = re.search(r"for s in ([^\n;]+?);\s*do", self.text)
        self.assertIsNotNone(m, "could not find the `for s in ...; do` secrets loop")
        listed = set(m.group(1).split())
        missing = REQUIRED_SECRETS - listed
        self.assertEqual(
            set(), missing,
            f"deploy.yml secrets loop no longer lists required secret(s): {sorted(missing)}",
        )

    def test_builder_fails_loudly_on_missing_secret(self):
        # A missing/inaccessible secret must abort the deploy, never be skipped.
        self.assertIn(
            "::error::", self.text,
            "the secrets builder no longer emits a loud ::error:: on a missing secret",
        )
        self.assertRegex(
            self.text, r"if\s*\[\[\s*-n\s*\"\$MISSING\"\s*\]\];\s*then[\s\S]*?exit 1",
            "the secrets builder no longer `exit 1`s when a required secret is missing",
        )

    def test_secrets_arg_is_wired_into_deploy(self):
        # The assembled value must actually reach `gcloud run deploy`, or the
        # guard would be theatre.
        self.assertIn("--set-secrets=", self.text)
        self.assertIn("steps.secrets_arg.outputs.secrets", self.text)


class TestJobSecretCoverageMap(unittest.TestCase):
    """Well-formedness of ops/report_job_secret_coverage.py's EXPECTED map —
    catches typos (an expected job not in the sync list, or an unknown secret)."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        path = DEPLOY_YML.resolve().parents[2] / "ops" / "report_job_secret_coverage.py"
        spec = importlib.util.spec_from_file_location("_jobcov", path)
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def test_expected_jobs_are_in_the_synced_job_list(self):
        unknown = set(self.mod.EXPECTED) - set(self.mod.JOBS)
        self.assertEqual(set(), unknown, f"EXPECTED names not in JOBS: {sorted(unknown)}")

    def test_expected_secrets_are_known_env_vars(self):
        known = {"FMP_API_KEY", "FRED_API_KEY", "ANTHROPIC_API_KEY",
                 "FFIEC_USERNAME", "FFIEC_JWT_TOKEN"}
        used = set().union(*self.mod.EXPECTED.values())
        self.assertTrue(used <= known, f"unknown secret env var(s): {sorted(used - known)}")

    def test_observe_only_default(self):
        # Stays non-blocking until a clean baseline is established by hand.
        self.assertTrue(self.mod.OBSERVE_ONLY)


if __name__ == "__main__":
    unittest.main()
