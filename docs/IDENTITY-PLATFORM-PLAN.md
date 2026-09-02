# Customer auth: Identity Platform + second IAP door — plan (2026-09-01)

Status: **DESIGNED, NOT STARTED** — owner deferred the Identity Platform
enable click (Marketplace agreement) on 2026-09-01. Everything below is
ready to execute once the owner says go. Owner decisions already made:
customer hostname = **clients.kskinvestor.com**; per-human accounts even
for company customers (companies are a billing grouping, not a login).

## Why this shape

- IAP today accepts only Google identities → non-Google customers can't
  sign in. Identity Platform (GCIP) behind IAP adds email+password for any
  address (free tier 50k MAU).
- **Do NOT flip the existing backend to GCIP mode.** The blocking deploy
  smoke authenticates as github-deployer with a Google-signed SA ID token
  (deploy.yml, `generateIdToken` → `tests/smoke_live.py`); a GCIP-mode IAP
  rejects Google SA tokens and every deploy's gate would fail. Staff SSO
  would also degrade.
- Therefore: **two doors, one app** —
  - `dashboard.kskinvestor.com` (existing backend): Google-IAP, unchanged —
    staff + CI smoke.
  - `clients.kskinvestor.com` (NEW backend service on the same URL map →
    same serverless NEG / Cloud Run service): IAP in external-identities
    (GCIP) mode — customers, email+password, admin-created accounts,
    self-signup disabled.

## Execution steps (in order)

1. Enable Identity Platform (Marketplace — OWNER approval; the deferred
   click).
2. Configure providers: Email/Password (primary). Optionally Google as a
   second provider later; not needed for door #2 since staff use door #1.
3. Sign-in page: use the IAP/GCIP wizard's hosted option if offered,
   else the documented FirebaseUI page (small static page; can live on
   Firebase Hosting or a tiny bucket site).
4. LB plumbing (no risk to the live door): new backend service on
   `bank-dashboard-urlmap` pointing at the existing serverless NEG; host
   rule `clients.kskinvestor.com` → new backend; extend/add the managed
   cert for the new hostname.
5. DNS: owner adds an A record for `clients` at the registrar (same IP as
   `dashboard` — the LB's static IP). Cloud DNS is NOT used in this
   project.
6. Enable IAP on the new backend in external-identities mode, wired to the
   GCIP tenant/providers. The existing backend's IAP is untouched.
7. Create the first test user in Identity Platform; verify sign-in,
   password reset, and revocation end-to-end on `clients.`.
8. App seam check (expected no-op): `ui/access_gate.py::
   _parse_iap_email_header` already takes the substring after the last
   `:`, which handles both `accounts.google.com:email` and the GCIP
   header form. Verify with a real request; add a unit test pinning the
   GCIP header format.
9. Decide the password-gate interaction: GCIP users already hold a
   per-user password, so the shared `external-access-password` gate stays
   dormant/redundant for door #2 (it keys off non-kskinvestors.com
   emails — revisit if it is ever armed).

## Onboarding flow once live

Add user in Identity Platform console (email; send password-set link) →
user signs in at clients.kskinvestor.com. Revoke = disable the user.
Company mapping: email→company table in Postgres when billing needs it
(Identity Platform multi-tenancy is overkill until many customers).

## Cost

Identity Platform: $0 at this scale. Extra backend service/host rule on
the existing LB: negligible. One managed cert: $0.
