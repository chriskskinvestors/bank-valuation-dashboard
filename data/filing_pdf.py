"""
Filing → PDF conversion via headless Chromium (Recent Documents "Download PDF").

EDGAR serves filings as HTML; a native PDF exists only when the filer attached
one. CapIQ-style "Download PDF for anything" therefore means rendering the
HTML to PDF ourselves. Chromium's print engine is the only renderer that
handles arbitrary filing HTML (nested fixed-width tables) faithfully —
prototyped 2026-07-08 on PB's 10-K/10-Q/EX-99.1 and approved by the owner.

Two SEC gotchas, both prototype-verified:
  • sec.gov serves its "Undeclared Automated Tool" block page to Chromium's
    default headless UA — so we fetch the HTML ourselves with the declared
    SEC UA (same shared retry policy as every other EDGAR fetch) and print
    from a local temp file, never from the URL.
  • EDGAR's widest tables (10-K yield analysis) slightly overflow letter
    width and wrap digits mid-number. The injected print CSS (0.85 zoom,
    0.35in margins) fits them; verified against the exact page that wrapped.

The chromium binary comes from $CHROMIUM_BIN (set in the Dockerfile), falling
back to PATH lookups and the standard Windows install paths for local dev.
No Streamlit imports here — callers cache the bytes.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from data.http import get_with_retry
from data.sec_client import HEADERS

# Print CSS injected into every document: letter paper, tight margins, and a
# 0.85 zoom so EDGAR's widest fixed-width tables fit without digit-wrapping.
_PRINT_CSS = ("<style>@page { size: letter; margin: 0.35in 0.35in; } "
              "body { zoom: 0.85; }</style>")

_WINDOWS_BROWSERS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def chromium_path() -> str | None:
    """The chromium/chrome binary to print with, or None if none is present."""
    env = os.environ.get("CHROMIUM_BIN")
    if env and Path(env).exists():
        return env
    for name in ("chromium", "chromium-browser", "google-chrome"):
        hit = shutil.which(name)
        if hit:
            return hit
    for p in _WINDOWS_BROWSERS:
        if Path(p).exists():
            return p
    return None


def html_to_pdf_bytes(html: str, base_url: str | None = None,
                      timeout: int = 120) -> bytes | None:
    """Print an HTML string to PDF bytes. None on any failure (no chromium,
    conversion error) — callers show an honest error, never a broken file."""
    binary = chromium_path()
    if not binary:
        print("[filing_pdf] no chromium binary found (set CHROMIUM_BIN)")
        return None

    inject = _PRINT_CSS
    if base_url:
        # Relative image/css refs resolve against the EDGAR archive folder.
        inject = f'<base href="{base_url}">' + inject
    lowered = html.lower()
    head_at = lowered.find("<head")
    if head_at != -1:
        head_end = html.find(">", head_at)
        html = html[:head_end + 1] + inject + html[head_end + 1:]
    else:
        html = inject + html

    tmpdir = tempfile.mkdtemp(prefix="filing_pdf_")
    try:
        src = Path(tmpdir) / "doc.htm"
        out = Path(tmpdir) / "doc.pdf"
        src.write_text(html, encoding="utf-8")
        cmd = [
            binary, "--headless=new", "--disable-gpu",
            # Cloud Run runs the container as root with a small /dev/shm;
            # both flags are required there and harmless locally.
            "--no-sandbox", "--disable-dev-shm-usage",
            "--no-pdf-header-footer",
            f"--user-agent={HEADERS['User-Agent']}",
            f"--user-data-dir={Path(tmpdir) / 'profile'}",
            f"--print-to-pdf={out}",
            src.as_uri(),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if not out.exists() or out.stat().st_size == 0:
            err = (proc.stderr or b"")[-500:].decode("utf-8", errors="replace")
            print(f"[filing_pdf] chromium produced no output (rc={proc.returncode}): {err}")
            return None
        return out.read_bytes()
    except subprocess.TimeoutExpired:
        print(f"[filing_pdf] chromium timed out after {timeout}s")
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def filing_url_to_pdf_bytes(url: str) -> bytes | None:
    """Fetch an EDGAR document (declared UA) and print it to PDF bytes.
    A natively-PDF document is returned as-is — no re-rendering."""
    try:
        resp = get_with_retry(url, headers=HEADERS, timeout=30)
    except Exception as e:
        print(f"[filing_pdf] fetch failed for {url}: {type(e).__name__}: {e}")
        return None
    if resp is None:
        return None
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if url.lower().endswith(".pdf") or "application/pdf" in ctype:
        return resp.content
    base_url = url.rsplit("/", 1)[0] + "/"
    return html_to_pdf_bytes(resp.text, base_url=base_url)
