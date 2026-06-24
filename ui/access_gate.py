"""External-access password gate — defense-in-depth on top of IAP.

IAP already authenticates every visitor by Google login and only lets authorized
identities reach the app. This adds a SECOND factor for outsiders:
  • @kskinvestors.com users        -> straight through (Google login is enough)
  • any other email (e.g. @gmail.com) -> must ALSO enter a shared password

The password is the env var ``EXTERNAL_ACCESS_PASSWORD`` (mounted from Secret
Manager secret ``external-access-password``). The gate is DORMANT whenever that
env var is unset, so this ships with zero behaviour change until the secret is
created; rotate the password by adding a new secret version + redeploying.

The visitor's identity comes from IAP's signed ``X-Goog-Authenticated-User-Email``
header, trustworthy here because the Cloud Run service only accepts traffic from
the IAP load balancer (ingress=internal-and-cloud-load-balancing) — a visitor
cannot reach the app to spoof that header.
"""
from __future__ import annotations

import hmac
import os

import streamlit as st

_INTERNAL_DOMAIN = "kskinvestors.com"
_SESSION_FLAG = "_ext_access_ok"


def _configured_password() -> str:
    return (os.environ.get("EXTERNAL_ACCESS_PASSWORD") or "").strip()


def _parse_iap_email_header(raw: str | None) -> str | None:
    """IAP sends 'accounts.google.com:user@domain'. Return the lowercased email,
    or None."""
    if not raw:
        return None
    return raw.split(":")[-1].strip().lower() or None


def _iap_email() -> str | None:
    """The IAP-verified email of the visitor, or None (e.g. local dev, no IAP)."""
    try:
        headers = st.context.headers or {}
    except Exception:
        return None
    return _parse_iap_email_header(headers.get("X-Goog-Authenticated-User-Email"))


def _is_trusted_identity(email: str | None) -> bool:
    """Internal humans (@kskinvestors.com) and trusted automation (GCP service
    accounts, e.g. the post-deploy smoke's deployer SA) skip the password —
    service accounts can only reach the app if already IAP-authorized."""
    if not email:
        return False
    e = email.strip().lower()
    return e.endswith("@" + _INTERNAL_DOMAIN) or e.endswith(".gserviceaccount.com")


def access_decision(email: str | None, configured_password: str,
                    provided: str | None) -> str:
    """Pure access policy (unit-tested):
      'allow'         -> let the visitor in
      'need_password' -> external visitor, needs to enter the password
      'bad_password'  -> external visitor, wrong password
    """
    if not configured_password:
        return "allow"                      # gate disabled (secret not set)
    if _is_trusted_identity(email):
        return "allow"                      # internal / service account
    if not provided:
        return "need_password"              # external — must enter the password
    if hmac.compare_digest(provided, configured_password):
        return "allow"
    return "bad_password"


def enforce_access_gate() -> None:
    """Call once at app entry (after st.set_page_config). For external visitors,
    when the gate is configured, halts the app with a password prompt until the
    correct shared password is entered."""
    pw = _configured_password()
    if not pw:
        return                              # dormant until the secret exists
    if st.session_state.get(_SESSION_FLAG):
        return                              # already cleared this session

    email = _iap_email()
    if access_decision(email, pw, None) == "allow":
        st.session_state[_SESSION_FLAG] = True
        return

    # External visitor — render the prompt and stop the rest of the app.
    st.markdown("## Access restricted")
    st.caption("This dashboard requires an access password for external accounts. "
               "Enter it to continue, or contact KSK Investors if you need access.")
    with st.form("ext_access_form", clear_on_submit=False):
        entered = st.text_input("Access password", type="password",
                                key="_ext_access_pw_input")
        submitted = st.form_submit_button("Enter")
    if submitted:
        if access_decision(email, pw, entered) == "allow":
            st.session_state[_SESSION_FLAG] = True
            st.rerun()
        st.error("Incorrect password.")
    st.stop()
