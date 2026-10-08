# Dashboard access runbook

How people get into the dashboard, and the one-time move from "Google accounts
in our Workspace, gated by IAM" to "accounts we create in Identity Platform"
(IAP external identities). Owner directive 2026-10-07: users beyond Google
accounts, created by us only, no self-registration.

Every Console step here is done by the owner. Claude verifies read-only
afterwards. Nothing in Phases 1-3 changes who can reach the dashboard: until
the Phase 4 toggle, IAP only accepts Google identities authorized by IAM.

Console pages (project `ace-beanbag-486220-a8`):

- Identity Platform providers: https://console.cloud.google.com/customer-identity/providers?project=ace-beanbag-486220-a8
- Identity Platform users: https://console.cloud.google.com/customer-identity/users?project=ace-beanbag-486220-a8
- Identity Platform settings: https://console.cloud.google.com/customer-identity/settings?project=ace-beanbag-486220-a8
- IAP: https://console.cloud.google.com/security/iap?project=ace-beanbag-486220-a8
- OAuth clients: https://console.cloud.google.com/apis/credentials?project=ace-beanbag-486220-a8

UI labels below follow Google's docs as of 2026-10. If a label differs on
screen, stop and tell Claude rather than guessing.

## How it works after the switch

- **Who gets in:** anyone who can sign in to the project's Identity Platform.
  Sign-up is disabled, so that means exactly the users listed on the
  Identity Platform users page. IAM roles (`IAP-secured Web App User`) no
  longer gate access; Google's docs say IAM does not apply to external
  identities.
- **Sign-in methods:** email and password, Google, and Microsoft if enabled.
- **Sign-in page:** IAP sends unauthenticated visitors to the sign-in page,
  then back to the dashboard.
- **Identity reaching the app:** IAP's `X-Goog-Authenticated-User-Email`
  header becomes `securetoken.google.com/ace-beanbag-486220-a8:<email>`.
  `ui/access_gate.py` keeps the text after the last colon, so it still reads
  the email (pinned in `tests/test_access_gate.py`).
- **Deploy smoke and warm-up:** the deployer service account mints a Google
  OIDC token and exchanges it for an Identity Platform token
  (`accounts:signInWithIdp`). That is Google's documented service-account
  path. It is keyless, so there is no password secret. The repo variable
  `IAP_AUTH_MODE` selects the path, and it must flip together with the IAP
  toggle. See `ops/iap_token.py`.

### Rules that keep this safe

1. **Never enable sign-up.** With sign-up on, anyone could register. An
   email/password registration could also claim an `@kskinvestors.com`
   address without proving it, and the app's access gate trusts that domain.
2. **The Identity Platform API key is public by design.** It identifies the
   project and does not authorize anything. Restrict it to the Identity
   Toolkit and Token Service APIs. Do not add an HTTP-referrer restriction:
   the deploy smoke calls the API from GitHub Actions, which sends no referrer.
3. **Keep the old IAM grants until Phase 5.** They are inert after the
   switch, but they make rollback instant.

## One-time migration

### Phase 0 — owner decisions

1. **Sign-in page.** Recommended: the IAP-hosted page ("Create a sign-in page
   for me"). Google builds and maintains it as a Cloud Run service in this
   project. It supports a title, logo and a "sign-up disabled" message via its
   `/admin` page. The alternative is our own page built on Google's
   `gcip-iap` library (version 2.0.1 at time of writing), deployed as an
   `iap-signin` service. That is more code for us to maintain, for branding
   control we probably don't need.
2. **Microsoft sign-in, yes or no.** It needs an app registration in
   Microsoft Entra, which provides a client ID and secret. Can be added any
   time later without re-doing anything.

### Phase 1 — set up Identity Platform (no effect on access)

1. Open the Identity Platform providers page. If prompted, click
   **Enable Identity Platform**.
2. **Add a provider → Email / Password.** Turn it on. Leave
   "Allow passwordless login" off. Save.
3. Create a dedicated OAuth client for Google sign-in. On the OAuth clients
   page, choose **Create credentials → OAuth client ID → Web application**.
   Name it `Identity Platform Google sign-in`. Under **Authorized redirect
   URIs**, add `https://ace-beanbag-486220-a8.firebaseapp.com/__/auth/handler`.
   Create it, and copy the client ID and secret (the secret is shown once).
   A dedicated client keeps Google sign-in independent of IAP's own OAuth
   client.
4. **Add a provider → Google.** Paste that client ID and secret. Save.
5. On the providers page, open **Application setup details** and copy the
   `apiKey`. Send it and the step-3 client ID to Claude. Both are public
   identifiers, not secrets. Claude sets the repo variables `GCIP_API_KEY`
   and `GCIP_GOOGLE_CLIENT_ID`.
6. Restrict that API key. On the OAuth clients page, open the key, choose
   **API restrictions → Restrict key**, and select Identity Toolkit API and
   Token Service API. Leave **Application restrictions** at None (rule 2).
7. On the settings page, under **Authorized domains**, confirm `localhost`
   is listed. It is there by default, and the Phase 3 probe needs it.
8. **Leave sign-up enabled for now.** Nothing trusts Identity Platform yet,
   and step 9 needs it.

### Phase 2 — create the accounts

9. Claude runs the **IAP auth probe** workflow in `gcip` mode. The deployer
   service account's first exchange creates its Identity Platform user. The
   expected result is "Minted a gcip token" and **HTTP 401**: IAP still wants
   a Google token at this point.
10. On the users page, click **Add user** and create:
    - `chris@kskinvestors.com` with a strong password. This is the
      email/password fallback login.
    - A test user with an address the owner controls outside
      `kskinvestors.com`. This stands in for a future external user.
11. On the settings page, under **User actions**, turn off
    **Enable create (sign-up)** and **Enable delete**. Save.
12. Claude re-runs the probe in `gcip` mode. Existing users are not affected
    by the sign-up setting, so it must still mint a token.

### Phase 3 — pre-switch acceptance test (owner signs in, Claude reads results)

Claude starts the probe page (`tools/gcip_signin_probe.html`, launch config
`gcip-signin-probe`). The owner opens
`http://localhost:8765/gcip_signin_probe.html?key=<apiKey>` and types every
credential themselves. Each line must match before Phase 4:

| # | Attempt | Must show |
|---|---------|-----------|
| A | Google as `chris@kskinvestors.com` | SIGNED IN |
| B | Email/password as `chris@kskinvestors.com` | SIGNED IN |
| C | Email/password as the test user | SIGNED IN |
| D | Google with an account that was **not** added, such as a personal Gmail | REFUSED, `auth/admin-restricted-operation` |
| E | Email/password with an address that was not added | REFUSED |

- **If D signs in, stop.** Any Google account would get into the dashboard.
  Do not switch. Delete the user D created, then investigate.
- **If A is refused** with `auth/account-exists-with-different-credential`
  or `auth/admin-restricted-operation`, Google sign-in does not attach to an
  admin-created account. Do not switch yet. Workspace users would have to use
  email and password, or a different provisioning step is needed. Claude
  resolves this before Phase 4.
- **The "linked" line from A** records how Google users are provisioned. It
  decides the "Add a user" procedure below.

### Phase 4 — the switch (quiet hours, Chris and Claude both online)

13. On the IAP page, open **Applications**. Select the backend service that
    fronts `bank-dashboard`. In the side panel, click **Start** under
    **Use external identities for authorization**, and confirm.
14. Sign-in page: **Create a sign-in page for me** (Phase 0 option A).
    Providers: **Project providers**. Check Email/Password and Google, plus
    Microsoft if chosen. Click **Save**.
15. Verify immediately, in two fresh incognito windows:
    - Open the dashboard URL and sign in with Google as Chris. The dashboard
      must load.
    - Open the dashboard URL and sign in with the test user's email and
      password. The dashboard must load.
16. Claude runs the probe in `gcip` mode, expecting **HTTP 200** and the
    Streamlit shell. Then Claude sets the repo variable `IAP_AUTH_MODE=gcip`
    and re-runs the deploy to confirm the smoke is green.
17. **Removal latency.** On the users page, disable the test user. Note how
    long the open session keeps working (Google's docs don't state it). Then
    re-enable the user. Record the result here.
18. Branding, optional. Open `<sign-in page URL>/admin`, signed in as the
    owner. Set the title to KSK Investors, add the logo, and set
    `disableSignUp` to `{"status": true, "adminEmail": "chris@kskinvestors.com"}`.

**If anything in step 15 or 16 fails, roll back.**

### Rollback (about one minute, the Console is never locked)

The Google Cloud Console signs in with Google directly, not through IAP, so it
always stays reachable.

1. On the IAP page, select the `bank-dashboard` backend service.
2. In the Identity Platform panel, click **Use IAM to manage this resource**
   and confirm. Google's docs note this clears the sign-in URL and provider
   choices, so they must be re-entered on a retry.
3. Access is back to Google accounts plus IAM immediately, because the IAM
   grants were never removed.
4. Claude sets `IAP_AUTH_MODE` back to `iam`, or deletes the variable.
5. A deploy that ran its smoke between the switch and the variable change
   shows red. That is a signal, not an outage: the revision keeps serving.

### Phase 5 — cleanup (after a week of normal use)

- Remove the now-inert `IAP-secured Web App User` grants on the IAP page.
- Decide whether to retire the shared external-access password. Per-user
  accounts replace it, and the secret `external-access-password` arms it in
  `ui/access_gate.py`.
- Update this runbook with the Phase 3 provisioning result, and update the
  architecture line in `CLAUDE.md`.

## Operations (after the switch)

Final click paths get confirmed during Phase 3 and 4 and are then written in
here. Until then, treat these as the expected shape.

- **Add a user.** On the users page, click **Add user** and enter their email
  and a strong temporary password. Send the password out of band, and ask
  them to change it with the sign-in page's password-reset link. For Google
  or Microsoft sign-in, the Phase 3 result decides whether the same entry is
  enough.
- **Remove a user.** On the users page, open the user and **Disable** them.
  Disable rather than delete: it is reversible and keeps the record. Check the
  Phase 4 step 17 latency before assuming access ends instantly.
- **Reset a password.** The user clicks the reset link on the sign-in page and
  gets Google's reset email. An admin can also set a new password on the
  user's page.
- **Add a provider.** Add it on the providers page, then tick it in the IAP
  side panel for the backend service. If we built our own sign-in page, it
  also needs the provider.
- **Costs.** Identity Platform is free up to 50,000 monthly active users for
  email and social sign-in. The hosted sign-in page is one small Cloud Run
  service, about $0-2 a month.
