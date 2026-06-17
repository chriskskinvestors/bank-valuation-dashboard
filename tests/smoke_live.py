"""Gap A (production-readiness audit) — post-deploy LIVE smoke check.

Drives the deployed, IAP-gated dashboard with a real headless browser and asserts
it actually renders — catching render-time failures (a page that shows a Python
traceback while the server's health endpoint still returns 200) that no pre-build
check can see. Cloud Run is `--ingress=internal-and-cloud-load-balancing` +
`--no-allow-unauthenticated`, so the only reachable path is the IAP load balancer;
we authenticate with an IAP OIDC token (audience = the IAP OAuth client ID), minted
in CI as the github-deployer service account.

Env:
  APP_URL    — the public (IAP) URL of the dashboard, e.g. https://dashboard.example.com
  IAP_TOKEN  — an OIDC ID token whose audience is the IAP client ID
               (gcloud auth print-identity-token --audiences=<IAP_CLIENT_ID>)

Exit 0 = healthy; non-zero (with a message) = broken deploy. Run locally against a
non-IAP URL by leaving IAP_TOKEN unset.
"""
import os
import sys

from playwright.sync_api import sync_playwright

URL = os.environ.get("APP_URL", "").rstrip("/")
TOKEN = os.environ.get("IAP_TOKEN", "").strip()
# Sidebar section labels that must be present once the app has rendered — proves
# the nav built, not just that an HTML shell loaded. Kept loose (any one is enough)
# so a label rename doesn't false-fail the smoke.
EXPECTED_ANY = ["Markets & Rates", "Market & Macro", "Company Analysis",
                "Home", "Bank Sector", "Economic Data"]


def main() -> int:
    if not URL:
        print("smoke_live: APP_URL not set — nothing to check", file=sys.stderr)
        return 0
    headers = {}
    if TOKEN:
        # IAP accepts the OIDC token in Authorization; Streamlit doesn't use that
        # header itself, so there's no conflict.
        headers["Authorization"] = f"Bearer {TOKEN}"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(extra_http_headers=headers)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        resp = page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
        if resp is not None and resp.status >= 400:
            print(f"smoke_live: FAIL — {URL} returned HTTP {resp.status}", file=sys.stderr)
            return 1
        # Streamlit hydrates over a websocket after the shell loads — wait for any
        # expected nav label to appear (up to 90s for a cold start), then settle.
        try:
            sel = " , ".join(f"text={lbl}" for lbl in EXPECTED_ANY)
            page.wait_for_selector(sel, timeout=90_000)
        except Exception:
            print("smoke_live: FAIL — no nav section rendered within 90s "
                  "(app did not hydrate)", file=sys.stderr)
            print(page.content()[:2000], file=sys.stderr)
            return 1
        page.wait_for_timeout(2_000)
        body = page.content()
        if "Traceback (most recent call last)" in body:
            print("smoke_live: FAIL — Python traceback rendered on the live page",
                  file=sys.stderr)
            idx = body.find("Traceback (most recent call last)")
            print(body[idx:idx + 1500], file=sys.stderr)
            return 1
        if errors:
            print("smoke_live: FAIL — uncaught JS page errors:", file=sys.stderr)
            for e in errors[:5]:
                print("  " + e, file=sys.stderr)
            return 1
        present = [lbl for lbl in EXPECTED_ANY if lbl in body]
        print(f"smoke_live: OK — live page rendered (nav sections seen: {present})")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
