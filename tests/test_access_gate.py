"""ui.access_gate — the external-access password policy (pure logic).

Verifies: gate dormant when no password configured; @kskinvestors.com passes on
identity alone; external emails need the correct shared password; unknown email
fails closed when the gate is armed; IAP header parsing.
"""
import unittest

from ui.access_gate import access_decision, _parse_iap_email_header

PW = "s3cret"


class TestAccessDecision(unittest.TestCase):
    def test_dormant_when_no_password_configured(self):
        # Gate disabled -> everyone allowed (the current IAP-only behaviour).
        self.assertEqual(access_decision(None, "", None), "allow")
        self.assertEqual(access_decision("x@gmail.com", "", None), "allow")

    def test_internal_domain_passes_without_password(self):
        self.assertEqual(access_decision("chris@kskinvestors.com", PW, None), "allow")
        # case-insensitive
        self.assertEqual(access_decision("Chris@KSKInvestors.com", PW, None), "allow")

    def test_external_needs_password(self):
        self.assertEqual(access_decision("someone@gmail.com", PW, None), "need_password")
        self.assertEqual(access_decision("someone@gmail.com", PW, ""), "need_password")

    def test_external_correct_password_allows(self):
        self.assertEqual(access_decision("someone@gmail.com", PW, PW), "allow")

    def test_external_wrong_password_rejected(self):
        self.assertEqual(access_decision("someone@gmail.com", PW, "nope"), "bad_password")

    def test_unknown_email_fails_closed_when_armed(self):
        # No identity + gate armed -> must supply the password (never auto-allow).
        self.assertEqual(access_decision(None, PW, None), "need_password")
        self.assertEqual(access_decision(None, PW, PW), "allow")

    def test_service_account_is_trusted(self):
        # The post-deploy smoke authenticates as the deployer SA — must pass
        # without a password so arming the gate never breaks the deploy.
        self.assertEqual(
            access_decision("github-deployer@ace-beanbag-486220-a8.iam.gserviceaccount.com",
                            PW, None), "allow")

    def test_lookalike_domain_is_not_internal(self):
        # Must be exactly @kskinvestors.com, not a suffix trick.
        self.assertEqual(access_decision("evil@notkskinvestors.com", PW, None),
                         "need_password")
        self.assertEqual(access_decision("x@kskinvestors.com.evil.com", PW, None),
                         "need_password")


class TestHeaderParse(unittest.TestCase):
    def test_strips_iap_prefix_and_lowercases(self):
        self.assertEqual(
            _parse_iap_email_header("accounts.google.com:User@Gmail.com"),
            "user@gmail.com")

    def test_external_identities_prefix(self):
        # IAP with Identity Platform (project-level and tenant forms, per the
        # signed-headers doc) — the email must survive the new namespace.
        self.assertEqual(
            _parse_iap_email_header(
                "securetoken.google.com/ace-beanbag-486220-a8:Chris@KSKInvestors.com"),
            "chris@kskinvestors.com")
        self.assertEqual(
            _parse_iap_email_header("securetoken.google.com/proj/tenant-1:a@fund.com"),
            "a@fund.com")

    def test_external_identities_smoke_sa_still_trusted(self):
        # The deploy smoke's SA reaches the app via an Identity Platform user
        # carrying the SA email; arming the gate must not break the deploy.
        email = _parse_iap_email_header(
            "securetoken.google.com/ace-beanbag-486220-a8:"
            "github-deployer@ace-beanbag-486220-a8.iam.gserviceaccount.com")
        self.assertEqual(access_decision(email, PW, None), "allow")

    def test_plain_email(self):
        self.assertEqual(_parse_iap_email_header("a@b.com"), "a@b.com")

    def test_empty(self):
        self.assertIsNone(_parse_iap_email_header(None))
        self.assertIsNone(_parse_iap_email_header(""))


if __name__ == "__main__":
    unittest.main()
