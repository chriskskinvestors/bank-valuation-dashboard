"""mitmproxy addon for tests/smoke_live.py.

Injects the IAP bearer token on EVERY request — including the Streamlit
WebSocket upgrade (GET /_stcore/stream with Connection: Upgrade). Playwright/
Chromium's context-level ``extra_http_headers`` are NOT applied to the
WebSocket handshake, so behind IAP that wss:// upgrade arrived unauthenticated,
IAP rejected it, and the app sat at ``connection-state=CONNECTING`` forever
(never hydrating). A network-layer proxy is the reliable place to attach the
credential to all traffic.

The token comes from $IAP_TOKEN (same value smoke_live.py mints in CI). The
``request`` hook fires for the WS upgrade too, since to mitmproxy the upgrade
is an ordinary HTTP request.
"""
import os

_TOKEN = os.environ.get("IAP_TOKEN", "").strip()


def request(flow):  # noqa: D401 — mitmproxy event hook
    if _TOKEN:
        flow.request.headers["Authorization"] = "Bearer " + _TOKEN
