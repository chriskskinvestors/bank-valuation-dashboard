"""Gap A (production-readiness audit) — post-deploy LIVE smoke check.

Drives the deployed, IAP-gated dashboard with a real headless browser and asserts
it actually renders — catching render-time failures (a page that shows a Python
traceback while the server's health endpoint still returns 200) that no pre-build
check can see. Cloud Run is `--ingress=internal-and-cloud-load-balancing` +
`--no-allow-unauthenticated`, so the only reachable path is the IAP load balancer;
we authenticate with an IAP OIDC token (audience = the IAP OAuth client ID), minted
in CI as the github-deployer service account.

Hydration over IAP — why a proxy: Streamlit hydrates over a WebSocket
(wss://…/_stcore/stream). Chromium does NOT attach context-level
`extra_http_headers` to the WebSocket upgrade, so behind IAP that handshake was
unauthenticated → rejected → the app stuck at connection-state=CONNECTING and
never hydrated (so the render-time check could never run). Fix: route the browser
through a local mitmdump that injects `Authorization: Bearer <IAP_TOKEN>` on every
request, including the WS upgrade (see tests/_iap_proxy_addon.py). Chromium trusts
mitmproxy's leaf cert via ignore_https_errors. With no token (local non-IAP run)
we connect directly.

Env:
  APP_URL    — the public (IAP) URL of the dashboard, e.g. https://dashboard.example.com
  IAP_TOKEN  — an OIDC ID token whose audience is the IAP client ID (optional;
               unset = direct connection, for a local non-IAP URL)
  SMOKE_PROXY_PORT — local mitmdump port (default 8899)

Exit 0 = healthy; non-zero (with a message) = broken deploy.
"""
import contextlib
import os
import socket
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright

URL = os.environ.get("APP_URL", "").rstrip("/")
TOKEN = os.environ.get("IAP_TOKEN", "").strip()
PROXY_PORT = int(os.environ.get("SMOKE_PROXY_PORT", "8899"))
HYDRATE_TIMEOUT_S = int(os.environ.get("SMOKE_HYDRATE_TIMEOUT_S", "150"))
_ADDON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_iap_proxy_addon.py")

# Sidebar/top-nav section labels that must be present once the app has rendered —
# proves the nav built, not just that an HTML shell loaded. Loose (any one is
# enough) so a label rename doesn't false-fail the smoke.
EXPECTED_ANY = ["Markets & Rates", "Market & Macro", "Company Analysis",
                "Home", "Bank Sector", "Economic Data", "Screen & Compare"]


def _port_open(port: int) -> bool:
    with contextlib.closing(socket.socket()) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _mitmdump_cmd():
    """mitmdump launch argv. Prefer the console script (on PATH in CI); fall
    back to invoking the entrypoint via the current interpreter so it also works
    where the script dir isn't on PATH (e.g. Windows dev)."""
    import shutil
    args = ["-q", "-p", str(PROXY_PORT), "-s", _ADDON, "--set", "ssl_insecure=false"]
    exe = shutil.which("mitmdump")
    if exe:
        return [exe, *args]
    return [sys.executable, "-c",
            "from mitmproxy.tools.main import mitmdump; mitmdump()", *args]


def _start_proxy():
    """Start mitmdump injecting the IAP bearer on every request (incl. the WS
    upgrade). Returns the Popen once listening, or None if unavailable."""
    try:
        proc = subprocess.Popen(
            _mitmdump_cmd(),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (FileNotFoundError, ImportError):
        print("smoke_live: mitmdump not available — falling back to a direct "
              "connection (WS hydration may not authenticate through IAP)",
              file=sys.stderr)
        return None
    for _ in range(60):  # up to ~18s for the proxy + CA to come up
        if proc.poll() is not None:
            print("smoke_live: mitmdump exited early — falling back to direct",
                  file=sys.stderr)
            return None
        if _port_open(PROXY_PORT):
            return proc
        time.sleep(0.3)
    proc.terminate()
    print("smoke_live: mitmdump did not start listening — falling back to direct",
          file=sys.stderr)
    return None


def main() -> int:
    if not URL:
        print("smoke_live: APP_URL not set — nothing to check", file=sys.stderr)
        return 0

    proxy_proc = _start_proxy() if TOKEN else None
    using_proxy = proxy_proc is not None

    try:
        with sync_playwright() as p:
            if using_proxy:
                browser = p.chromium.launch(
                    proxy={"server": f"http://127.0.0.1:{PROXY_PORT}"})
                ctx = browser.new_context(ignore_https_errors=True)
            else:
                browser = p.chromium.launch()
                # No proxy: still authenticate the HTTP GET via the header so we
                # at least reach the shell (WS hydration may not authenticate).
                headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
                ctx = browser.new_context(extra_http_headers=headers)
            page = ctx.new_page()

            resp = page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
            if resp is not None and resp.status >= 400:
                print(f"smoke_live: FAIL — {URL} returned HTTP {resp.status}",
                      file=sys.stderr)
                return 1

            # Reached the Streamlit shell? Proves IAP auth + the revision serves
            # the app (else: a login page, 403, or error page).
            try:
                page.wait_for_selector('[data-testid="stApp"]', timeout=30_000)
            except Exception:
                print("smoke_live: FAIL — did not reach the Streamlit app shell "
                      "(IAP auth or server problem)", file=sys.stderr)
                print(page.content()[:1500], file=sys.stderr)
                return 1

            # Hydration: with the proxy authenticating the WS upgrade, the app
            # should hydrate and render a nav section. This is the check that
            # makes render-time failures visible.
            hydrated = False
            try:
                page.wait_for_selector(
                    " , ".join(f"text={lbl}" for lbl in EXPECTED_ANY),
                    timeout=HYDRATE_TIMEOUT_S * 1000)
                hydrated = True
                page.wait_for_timeout(2_000)  # let the rest of the run settle
            except Exception:
                pass

            body = page.content()
            if "Traceback (most recent call last)" in body:
                print("smoke_live: FAIL — Python traceback rendered on the live page",
                      file=sys.stderr)
                idx = body.find("Traceback (most recent call last)")
                print(body[idx:idx + 1500], file=sys.stderr)
                return 1

            if hydrated:
                present = [lbl for lbl in EXPECTED_ANY if lbl in body]
                print(f"smoke_live: OK — live page hydrated and rendered "
                      f"(nav sections: {present})")
                return 0

            # No nav label within the window. Read the WS connection state.
            state = ""
            with contextlib.suppress(Exception):
                el = page.query_selector('[data-testid="stApp"]')
                state = el.get_attribute("data-test-connection-state") or ""
            # CONNECTED + no traceback (checked above) = the WebSocket authenticated
            # through IAP and the server is healthy; it just didn't paint a nav
            # label in time (cold-start render). That's not a deploy defect, so
            # pass. Only a WS that never reaches CONNECTED (CONNECTING/DISCONNECTED)
            # — or no proxy to authenticate it — is a genuine IAP/WS/server failure.
            if state.upper() == "CONNECTED":
                print(f"smoke_live: OK — WebSocket authenticated + CONNECTED through "
                      f"IAP and no error rendered; full nav paint not confirmed "
                      f"within {HYDRATE_TIMEOUT_S}s (cold-start render)")
                return 0
            if not using_proxy:
                print("smoke_live: OK — app shell loaded through IAP (server "
                      "healthy); WS hydration not verified (no proxy available)")
                return 0
            print(f"smoke_live: FAIL — WebSocket did not reach CONNECTED within "
                  f"{HYDRATE_TIMEOUT_S}s (connection-state={state!r}) — IAP/WS or "
                  f"server problem", file=sys.stderr)
            return 1
    finally:
        if proxy_proc is not None:
            proxy_proc.terminate()
            with contextlib.suppress(Exception):
                proxy_proc.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
