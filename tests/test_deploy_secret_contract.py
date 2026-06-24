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


if __name__ == "__main__":
    unittest.main()
