"""Mint the bearer token the post-deploy warm/smoke sends through IAP.

Prints ONLY the token on stdout (the workflow captures it); diagnostics go to
stderr and any failure exits 1. The token itself is never logged.

Two modes, chosen by IAP_AUTH_MODE (repo variable; unset = "iam"):

  iam   IAP uses Google identities + IAM (today). The bearer is the deployer
        SA's Google OIDC ID token, audience = the IAP OAuth client ID, minted
        through the IAM Credentials API (under Workload Identity Federation
        `gcloud auth print-identity-token --audiences` is rejected).

  gcip  IAP uses external identities (Identity Platform). IAP no longer takes
        a raw Google SA token, and Identity Platform *user* tokens are not
        supported for programmatic access at all. The documented service-account
        path (docs.cloud.google.com/iap/docs/service-accounts-external-identities)
        mints the same kind of Google OIDC token, audience = the client ID of
        the Google provider configured in Identity Platform, and exchanges it
        via accounts:signInWithIdp (providerId=google.com) for an Identity
        Platform ID token. THAT is the bearer. Keyless: no password secret.

Env:
  ACCESS_TOKEN           OAuth access token of the deployer SA (gcloud auth print-access-token)
  SA                     deployer SA email
  IAP_CLIENT_ID          IAP OAuth client ID (iam audience; gcip fallback audience)
  IAP_AUTH_MODE          "iam" (default) or "gcip"
  GCIP_API_KEY           Identity Platform web API key (gcip; public by design)
  GCIP_GOOGLE_CLIENT_ID  Google provider client ID in Identity Platform (gcip;
                         defaults to IAP_CLIENT_ID, as in Google's doc)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

_GENERATE_ID_TOKEN = ("https://iamcredentials.googleapis.com/v1/projects/-/"
                      "serviceAccounts/{sa}:generateIdToken")
_SIGN_IN_WITH_IDP = ("https://identitytoolkit.googleapis.com/v1/"
                     "accounts:signInWithIdp?key={key}")


class TokenError(Exception):
    pass


def _post_json(url: str, body: dict, headers: dict | None = None) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        # Surface the API's own error code (e.g. ADMIN_ONLY_OPERATION when the
        # SA has no Identity Platform user yet and sign-up is disabled) — the
        # error body never contains a token.
        try:
            msg = json.loads(e.read().decode()).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        host = url.split("/")[2]
        raise TokenError(f"{host} returned HTTP {e.code} {msg}".rstrip()) from None


def google_id_token(access_token: str, sa: str, audience: str) -> str:
    """The deployer SA's Google OIDC ID token for `audience` (email claim
    included — IAP rejects SA tokens without it)."""
    res = _post_json(_GENERATE_ID_TOKEN.format(sa=sa),
                     {"audience": audience, "includeEmail": True},
                     {"Authorization": f"Bearer {access_token}"})
    tok = res.get("token")
    if not tok:
        raise TokenError("generateIdToken returned no token")
    return tok


def gcip_id_token(api_key: str, google_token: str) -> str:
    """Exchange a Google OIDC ID token for an Identity Platform ID token.
    Request shape is Google's documented REST example (project-level
    providers, so no tenantId)."""
    res = _post_json(_SIGN_IN_WITH_IDP.format(key=api_key), {
        "postBody": f"id_token={google_token}&providerId=google.com",
        "requestUri": "http://localhost",
        "returnIdpCredential": True,
        "returnSecureToken": True,
    })
    tok = res.get("idToken")
    if not tok:
        raise TokenError("signInWithIdp returned no idToken")
    return tok


def mint(env: dict) -> str:
    mode = (env.get("IAP_AUTH_MODE") or "iam").strip().lower()
    access_token = (env.get("ACCESS_TOKEN") or "").strip()
    sa = (env.get("SA") or "").strip()
    iap_client = (env.get("IAP_CLIENT_ID") or "").strip()
    if not access_token or not sa:
        raise TokenError("ACCESS_TOKEN and SA are required")
    if mode == "iam":
        if not iap_client:
            raise TokenError("IAP_CLIENT_ID is required in iam mode")
        return google_id_token(access_token, sa, iap_client)
    if mode == "gcip":
        api_key = (env.get("GCIP_API_KEY") or "").strip()
        audience = (env.get("GCIP_GOOGLE_CLIENT_ID") or "").strip() or iap_client
        if not api_key or not audience:
            raise TokenError("gcip mode needs GCIP_API_KEY and "
                             "GCIP_GOOGLE_CLIENT_ID (or IAP_CLIENT_ID)")
        return gcip_id_token(api_key, google_id_token(access_token, sa, audience))
    raise TokenError(f"unknown IAP_AUTH_MODE {mode!r} (expected iam or gcip)")


def main() -> int:
    try:
        token = mint(dict(os.environ))
    except TokenError as e:
        print(f"iap_token: {e}", file=sys.stderr)
        return 1
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
