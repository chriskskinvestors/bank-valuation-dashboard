"""ops/iap_token.py — the bearer the post-deploy warm/smoke sends through IAP.

Pins: iam mode is byte-for-byte the pre-existing workflow request (no behaviour
change until the flag flips); gcip mode exchanges the SA's Google OIDC token
via signInWithIdp exactly as Google's "Use service accounts with external
identities" REST example; audience fallback; failures exit 1 without ever
printing a token; and the workflow wiring (both steps, default mode iam).
HTTP is mocked at urllib.request.urlopen — no network.
"""
import importlib.util
import io
import json
import re
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("iap_token", ROOT / "ops" / "iap_token.py")
iap_token = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(iap_token)

SA = "github-deployer@ace-beanbag-486220-a8.iam.gserviceaccount.com"
BASE_ENV = {"ACCESS_TOKEN": "ya29.access", "SA": SA,
            "IAP_CLIENT_ID": "iap-client.apps.googleusercontent.com"}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ok(payload):
    return _Resp(json.dumps(payload).encode())


def _http_error(url, code, message):
    body = io.BytesIO(json.dumps({"error": {"message": message}}).encode())
    return urllib.error.HTTPError(url, code, "err", {}, body)


class _Recorder:
    """Fake urlopen: answers generateIdToken / signInWithIdp, records requests."""

    def __init__(self, idp_error=None):
        self.calls = []
        self.idp_error = idp_error

    def __call__(self, req, timeout=None):
        body = json.loads(req.data.decode())
        self.calls.append((req.full_url, dict(req.header_items()), body))
        if "generateIdToken" in req.full_url:
            return _ok({"token": "google-oidc-for-" + body["audience"]})
        if "signInWithIdp" in req.full_url:
            if self.idp_error:
                raise _http_error(req.full_url, 400, self.idp_error)
            return _ok({"idToken": "gcip-id-token", "email": SA})
        raise AssertionError("unexpected URL " + req.full_url)


def _run(env, fake):
    with mock.patch.object(iap_token.urllib.request, "urlopen", fake):
        return iap_token.mint(env)


class TestIamMode(unittest.TestCase):
    def test_default_mode_is_the_existing_oidc_request(self):
        fake = _Recorder()
        tok = _run(dict(BASE_ENV), fake)          # IAP_AUTH_MODE unset
        self.assertEqual(tok, "google-oidc-for-iap-client.apps.googleusercontent.com")
        self.assertEqual(len(fake.calls), 1)
        url, headers, body = fake.calls[0]
        self.assertEqual(url, "https://iamcredentials.googleapis.com/v1/projects/-/"
                              f"serviceAccounts/{SA}:generateIdToken")
        self.assertEqual(headers["Authorization"], "Bearer ya29.access")
        self.assertEqual(body, {"audience": "iap-client.apps.googleusercontent.com",
                                "includeEmail": True})

    def test_iam_requires_client_id(self):
        env = dict(BASE_ENV, IAP_CLIENT_ID="")
        with self.assertRaises(iap_token.TokenError):
            _run(env, _Recorder())


class TestGcipMode(unittest.TestCase):
    ENV = dict(BASE_ENV, IAP_AUTH_MODE="gcip", GCIP_API_KEY="AIzaKEY")

    def test_exchanges_google_token_per_documented_request(self):
        fake = _Recorder()
        tok = _run(dict(self.ENV), fake)
        self.assertEqual(tok, "gcip-id-token")
        self.assertEqual(len(fake.calls), 2)
        url, _, body = fake.calls[1]
        self.assertEqual(url, "https://identitytoolkit.googleapis.com/v1/"
                              "accounts:signInWithIdp?key=AIzaKEY")
        self.assertEqual(body, {
            "postBody": "id_token=google-oidc-for-iap-client.apps.googleusercontent.com"
                        "&providerId=google.com",
            "requestUri": "http://localhost",
            "returnIdpCredential": True,
            "returnSecureToken": True,
        })

    def test_audience_defaults_to_iap_client_and_honours_override(self):
        fake = _Recorder()
        _run(dict(self.ENV), fake)
        self.assertEqual(fake.calls[0][2]["audience"], "iap-client.apps.googleusercontent.com")
        fake = _Recorder()
        _run(dict(self.ENV, GCIP_GOOGLE_CLIENT_ID="provider-client"), fake)
        self.assertEqual(fake.calls[0][2]["audience"], "provider-client")

    def test_mode_is_case_and_space_insensitive(self):
        self.assertEqual(_run(dict(self.ENV, IAP_AUTH_MODE=" GCIP "), _Recorder()),
                         "gcip-id-token")

    def test_missing_api_key_fails(self):
        with self.assertRaises(iap_token.TokenError):
            _run(dict(self.ENV, GCIP_API_KEY=""), _Recorder())

    def test_api_error_code_is_surfaced(self):
        # ADMIN_ONLY_OPERATION = the SA has no Identity Platform user yet and
        # sign-up is disabled: the rollout checklist's ordering exists for this.
        with self.assertRaises(iap_token.TokenError) as cm:
            _run(dict(self.ENV), _Recorder(idp_error="ADMIN_ONLY_OPERATION"))
        self.assertIn("HTTP 400 ADMIN_ONLY_OPERATION", str(cm.exception))
        self.assertIn("identitytoolkit.googleapis.com", str(cm.exception))
        self.assertNotIn("AIzaKEY", str(cm.exception))   # host only, not the URL

    def test_unknown_mode_fails(self):
        with self.assertRaises(iap_token.TokenError):
            _run(dict(BASE_ENV, IAP_AUTH_MODE="oauth"), _Recorder())


class TestMainOutput(unittest.TestCase):
    def test_success_prints_only_the_token(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(iap_token.os.environ, BASE_ENV, clear=True), \
             mock.patch.object(iap_token.urllib.request, "urlopen", _Recorder()), \
             redirect_stdout(out), redirect_stderr(err):
            rc = iap_token.main()
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue(),
                         "google-oidc-for-iap-client.apps.googleusercontent.com\n")
        self.assertEqual(err.getvalue(), "")

    def test_failure_exits_1_and_prints_no_token(self):
        env = dict(BASE_ENV, IAP_AUTH_MODE="gcip", GCIP_API_KEY="AIzaKEY")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(iap_token.os.environ, env, clear=True), \
             mock.patch.object(iap_token.urllib.request, "urlopen",
                               _Recorder(idp_error="INVALID_IDP_RESPONSE")), \
             redirect_stdout(out), redirect_stderr(err):
            rc = iap_token.main()
        self.assertEqual(rc, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertNotIn("google-oidc", err.getvalue())   # the minted token never leaks


class TestWorkflowWiring(unittest.TestCase):
    """Both IAP-authenticated deploy steps mint through the one script, with
    iam as the default so nothing changes until IAP_AUTH_MODE is set."""

    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    def test_warm_and_smoke_use_the_script(self):
        self.assertEqual(self.text.count("python ops/iap_token.py"), 2)
        self.assertNotIn("generateIdToken", self.text)   # no inline copy left behind

    def test_mode_defaults_to_iam(self):
        modes = re.findall(r"IAP_AUTH_MODE:\s*(.+)", self.text)
        self.assertEqual(len(modes), 2)
        for m in modes:
            self.assertEqual(m.strip(), "${{ vars.IAP_AUTH_MODE || 'iam' }}")


if __name__ == "__main__":
    unittest.main()
