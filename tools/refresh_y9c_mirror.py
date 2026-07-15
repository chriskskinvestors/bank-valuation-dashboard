"""
Mirror the latest filed quarter of FR Y-9C facsimile PDFs to GCS.

NPW's Cloudflare bot management 403s Cloud Run egress IPs AND GitHub-hosted
runner egress outright (prod logs + workflow run 29357551545, 2026-07-14),
so neither production nor CI can fetch the facsimiles behind Recent
Documents → Regulatory Filings → "Download via dashboard".
data/nic_client.fetch_y9c_pdf serves the y9c/ mirror this script fills.
The ONLY proven-unblocked egress is the dev box (residential IP):

    gcloud.cmd auth application-default login   # ADC; expires ~hourly
    $env:GCS_BUCKET='ksk-bank-dashboard-data'
    $env:Y9C_MAX_FETCHES='50'                   # bounded batch (see below)
    python -m tools.refresh_y9c_mirror

A full quarter is ~366 holdcos × 30s spacing ≈ 3h, but two limits bite a
single long run (both hit 2026-07-14): the Workspace-managed ADC token the
GCS client uses expires ~hourly, and a residential IP gets Cloudflare-
challenged under a multi-hour sustained session. So fill the mirror in
bounded sub-hour batches via Y9C_MAX_FETCHES; runs are resumable (mirrored
objects are skipped) and largest-first, so a handful of ~50-holdco batches
converge and the most-viewed banks land in the first batch. The workflow
.github/workflows/refresh-nic-bulk.yml is kept dispatch-only purely for
the day Cloudflare unblocks GitHub egress.

Targets: FDIC ACTIVE institutions grouped by regulatory high holder
(RSSDHCR — the exact field ui/recent_documents keys its Y-9C rows on, so
mirror names always match what the UI requests), keeping holdcos whose
summed bank-subsidiary assets clear the Fed's $3B FR Y-9C filing floor,
largest first. FR Y-9C is a US top-tier report, so foreign parents (NIC
type FHF/FBH — TD, BMO, HSBC, UBS...) are dropped up front via the NIC
attributes: they file FR Y-7 and NPW returns onError for them.

Every remaining fetch is CLASSIFIED (see _fetch_y9c_classified), which is
what lets a clean run reach a true zero miss rate: an NPW onError redirect
means "this RSSD has no Y-9C" (a small/exempt SLHC or trust that files
FR Y-9SP) — recorded to y9c/_manifest.json after _MAX_MISS_ATTEMPTS and
never retried; a Cloudflare 403/challenge is a transient block of a real
filer — retried next run, NEVER recorded, and forces a non-zero exit so a
batch with any blocks clearly signals "re-run". Fetches are curl-only and
spaced (back-to-back NPW hits trip bot scoring even on allowed networks)
and skip objects already mirrored. If the GCS credential dies mid-run, a
consecutive-upload-failure circuit breaker aborts rather than burning bot
score on fetches it can't persist.

Not covered: foreign-owned US banks (TD Bank, BMO Harris, HSBC USA...) DO
file Y-9C, but under a US intermediate holding company whose RSSD differs
from RSSDHCR. Mirroring + serving those needs IHC resolution (a separate
change that also touches the UI's holdco lookup); until then they fall
back to the "Open PDF on NIC" link.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

from data import nic_client
from data.cloud_storage import (is_gcs_enabled, list_files, load_bytes,
                                save_bytes)
from data.ffiec_client import latest_reporting_period
from data.http import get_with_retry
from data.nic_client import NIC_BASE, USER_AGENT, y9c_mirror_name
from tools.refresh_nic_bulk import _SPACING_SECONDS

_GCS_PREFIX = "y9c"
_MANIFEST = "_manifest.json"    # {object_name: miss_attempts}
# data/fdic_client.py has the same URL but imports streamlit at module
# level — this tool must stay runnable on the workflow's minimal dep set.
_FDIC_INSTITUTIONS_URL = "https://banks.data.fdic.gov/api/institutions"
# Fed FR Y-9C filing floor: top-tier holdcos with ≥ $3B total consolidated
# assets (raised from $1B in 2018). FDIC ASSET is $thousands; summing bank
# subs per holdco under-counts consolidated assets (nonbank ops excluded),
# so a floor-straddling holdco can be missed — it heals via the dev-fetch
# mirror write in fetch_y9c_pdf, and the UI's "View on NIC" link always works.
_Y9C_ASSET_FLOOR_THOUSANDS = 3_000_000
# Permanent-skip threshold for CONFIRMED non-filers. _fetch_y9c_classified
# separates a genuine "no Y-9C for this RSSD" (NPW 302 → /npw/Home/onError —
# a foreign parent that slipped the type filter, or a small/exempt SLHC that
# files FR Y-9SP) from a transient Cloudflare block. Only a confirmed-absent
# result increments the manifest, and onError is deterministic, so a low cap
# is safe; the >1 guards against a one-off NPW app hiccup being read as a
# permanent absence. Cloudflare blocks NEVER touch the manifest, so a real
# filer can't be wrongly skipped.
_MAX_MISS_ATTEMPTS = 3
# Runaway guard: ~3.4h at 30s spacing — above the ~366 holdcos clearing the
# floor today (live probe 2026-07-14), so a full run covers every filer.
# Override with Y9C_MAX_FETCHES to fill the mirror in bounded sub-hour
# batches: the dev box's only viable egress is a Workspace-managed ADC that
# the reauth policy expires ~hourly (< a full 3h run), and a residential IP
# gets Cloudflare-challenged under a multi-hour sustained session. Runs are
# resumable (skip-existing), so `Y9C_MAX_FETCHES=50` across a few sessions
# converges without either limit biting. Largest holdcos go first, so even
# one small batch covers the most-viewed banks.
_MAX_FETCHES_PER_RUN = 400
_MIN_PDF_BYTES = 20_000     # facsimiles are ~1MB+; an error PDF is not
# Circuit breaker: consecutive GCS upload failures mean the credential died
# mid-run (ADC reauth). Keep fetching and every NPW hit is unsaveable — pure
# bot-score burn that got the dev IP Cloudflare-challenged 2026-07-14. Abort
# instead so a dead token stops the run cleanly.
_MAX_CONSEC_UPLOAD_FAILS = 3


def _max_fetches() -> int:
    raw = (os.environ.get("Y9C_MAX_FETCHES") or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return _MAX_FETCHES_PER_RUN


def _latest_y9c_period(as_of=None) -> str:
    """Latest quarter-end (YYYYMMDD) whose FR Y-9C should be filed AND
    posted. Reuses the call-report 45-day availability rule — the Y-9C
    deadline is 40 days after quarter-end (45 at year-end) plus a few
    days of NPW posting lag, so the same window fits."""
    m, d, y = latest_reporting_period(as_of).split("/")
    return f"{y}{m}{d}"


def _holdco_targets_from_records(records: list[dict]) -> list[tuple[int, int]]:
    """[(holdco_rssd, summed_bank_assets_thousands)] largest first, for
    holdcos clearing the Y-9C floor. Assets are summed across all bank
    subsidiaries sharing an RSSDHCR so multi-bank holdcos aren't dropped
    when no single sub clears the floor."""
    assets_by_holdco: dict[int, int] = {}
    for d in records:
        raw = str(d.get("RSSDHCR") or "").strip()
        if not raw.isdigit() or int(raw) <= 0:  # no holding company
            continue
        rssd = int(raw)
        assets_by_holdco[rssd] = (assets_by_holdco.get(rssd, 0)
                                  + int(d.get("ASSET") or 0))
    targets = [(r, a) for r, a in assets_by_holdco.items()
               if a >= _Y9C_ASSET_FLOOR_THOUSANDS]
    targets.sort(key=lambda t: (-t[1], t[0]))
    return targets


# NIC entity types whose top holder does NOT file an FR Y-9C at its own RSSD.
# FR Y-9C is a US top-tier holding-company report (BHC/FHD/SLHC/IHC file it);
# a foreign parent files FR Y-7 / Y-9LP instead, so NPW returns onError for
# its RSSD. FDIC RSSDHCR points at the ULTIMATE high holder, which for a
# foreign-owned US bank is that foreign parent — TD, BMO, HSBC, UBS, RBC,
# Santander, Barclays, Deutsche Bank, Mizuho... (verified 2026-07-14, all
# NIC type FHF/FBH). Being the largest holdcos, they sorted to the front of
# every batch and were the bulk of the "misses". Their US operations DO file
# Y-9C, but under a US intermediate holding company — a different RSSD than
# RSSDHCR, whose resolution is a separate enhancement (foreign-owned US banks
# still fall back to the "Open PDF on NIC" link meanwhile).
_FOREIGN_HOLDCO_TYPES = {"FHF", "FBH", "FBK", "FBO", "FEO", "IFB", "ISB"}


def _drop_foreign_holdcos(
        targets: list[tuple[int, int]]) -> tuple[list[tuple[int, int]], int]:
    """Filter out foreign parents (they file FR Y-7, not Y-9C — a guaranteed
    NPW onError). Uses the mirrored NIC ATTRIBUTES file. Best-effort: if that
    data can't be loaded, returns targets unchanged so the run still works."""
    if not targets:
        return targets, 0
    try:
        attr_path = nic_client._bulk_path("attributes_active")
        if attr_path is None:
            print("[y9c] foreign-filter skipped — NIC attributes unavailable")
            return targets, 0
        attrs = nic_client._load_attributes({r for r, _ in targets}, attr_path)
    except Exception as e:  # noqa: BLE001 — never let the filter break the run
        print(f"[y9c] foreign-filter skipped ({type(e).__name__}: {e})")
        return targets, 0
    kept: list[tuple[int, int]] = []
    dropped = 0
    for rssd, assets in targets:
        ent = attrs.get(rssd)
        # Keep unknowns (not in NIC) — attempt them; the miss-manifest catches
        # any that genuinely don't file. Only a KNOWN foreign type is dropped.
        if ent and ent.get("type_code") in _FOREIGN_HOLDCO_TYPES:
            dropped += 1
            continue
        kept.append((rssd, assets))
    return kept, dropped


def _fetch_fdic_records() -> list[dict]:
    """Every ACTIVE FDIC institution's {CERT, RSSDHCR, ASSET}. Paginated —
    a capped single page would silently truncate to the largest banks.
    Raises on failure/empty: a partial target list must never look done."""
    records: list[dict] = []
    offset = 0
    while True:
        resp = get_with_retry(_FDIC_INSTITUTIONS_URL, params={
            "filters": "ACTIVE:1",
            "fields": "CERT,RSSDHCR,ASSET",
            "limit": 1000,
            "offset": offset,
        }, timeout=30)
        if resp is None:
            raise RuntimeError("FDIC institutions endpoint kept 429ing")
        data = resp.json()
        page = data.get("data", [])
        if not page:
            break
        records.extend(r.get("data", {}) for r in page)
        offset += len(page)
        if offset >= data.get("totals", {}).get("count", 0):
            break
        time.sleep(0.05)
    if not records:
        raise RuntimeError("FDIC institutions endpoint returned no "
                           "active institutions")
    return records


def _load_manifest() -> dict[str, int]:
    got = load_bytes(_GCS_PREFIX, _MANIFEST)
    if got is None:
        return {}
    try:
        raw = json.loads(got[0].decode("utf-8"))
        return {str(k): int(v) for k, v in raw.items()}
    except Exception as e:  # noqa: BLE001 — any bad manifest starts fresh
        print(f"[y9c] manifest unreadable ({type(e).__name__}: {e}) "
              "— starting fresh")
        return {}


def _save_manifest(manifest: dict[str, int]) -> None:
    body = json.dumps(manifest, sort_keys=True).encode("utf-8")
    if not save_bytes(_GCS_PREFIX, _MANIFEST, body, "application/json"):
        print("[y9c] WARNING: manifest upload failed — misses will be "
              "retried next run")


_OK, _ABSENT, _BLOCKED = "ok", "absent", "blocked"
# curl -w trailer sentinel: separates the appended status line from the body.
# Must NOT start with '@' — curl reads a -w value beginning with '@' as a
# filename ("-w: error encountered when reading a file"). Underscores are safe
# and won't appear in a PDF/HTML body.
_META = b"__Y9CMETA__"


def _fetch_y9c_classified(rssd: int, period: str) -> tuple[str, bytes | None]:
    """Fetch one FR Y-9C facsimile and CLASSIFY the outcome — the difference
    between a non-filer and a block is what lets the run reach a true zero
    miss rate:

      (_OK, pdf_bytes) — a real facsimile.
      (_ABSENT, None)  — NPW redirected to /npw/Home/onError: this RSSD has no
                         FR Y-9C for the period (foreign parent past the type
                         filter, or a small/exempt SLHC filing FR Y-9SP).
                         Deterministic → recorded so it isn't retried forever.
      (_BLOCKED, None) — Cloudflare 403/challenge or any transport error: a
                         TRANSIENT block of a (presumed) real filer. Retried;
                         never recorded as a non-filer.

    Keeps curl -L so the success path matches nic_client._curl_fetch; the HTTP
    status + effective URL are captured via -w and classified after the fact.
    The %PDF magic + size gate still guards against an error body slipping
    through as a facsimile."""
    curl = shutil.which("curl")
    if not curl:
        print("[y9c] no curl on PATH — cannot fetch")
        return _BLOCKED, None
    url = (f"{NIC_BASE}/ReturnFinancialReportPDF?rpt=FRY9C"
           f"&id={rssd}&dt={period}")
    try:
        proc = subprocess.run(
            [curl, "-sS", "-L", "-A", USER_AGENT, "--max-time", "240",
             "-w", _META.decode() + "%{http_code} %{url_effective}", url],
            capture_output=True, timeout=300)
    except Exception as e:  # noqa: BLE001 — any curl failure is just a block
        print(f"[y9c] {rssd}: curl error ({type(e).__name__}: {e})")
        return _BLOCKED, None
    out = proc.stdout
    idx = out.rfind(_META)
    body = out[:idx] if idx >= 0 else out
    trailer = out[idx + len(_META):].decode("latin-1", "replace") if idx >= 0 else ""
    if body.startswith(b"%PDF-") and len(body) >= _MIN_PDF_BYTES:
        return _OK, body
    code, _, effurl = trailer.partition(" ")
    if "/npw/Home/onError" in effurl or code in ("302", "404"):
        return _ABSENT, None
    return _BLOCKED, None


def main() -> int:
    if not is_gcs_enabled():
        print("GCS_BUCKET is not set — nothing to mirror to.")
        return 1

    period = _latest_y9c_period()
    targets = _fetch_fdic_records()
    targets = _holdco_targets_from_records(targets)
    targets, dropped_foreign = _drop_foreign_holdcos(targets)
    existing = set(list_files(_GCS_PREFIX, "*.pdf"))
    manifest = _load_manifest()
    print(f"[y9c] period {period}: {len(targets)} US Y-9C-filing holdcos "
          f"above the floor ({dropped_foreign} foreign parents excluded — "
          f"they file FR Y-7), {len(existing)} facsimiles already mirrored")

    todo: list[tuple[int, str]] = []
    skipped_misses = 0
    for rssd, _assets in targets:
        name = y9c_mirror_name(rssd, period)
        if name in existing:
            continue
        if manifest.get(name, 0) >= _MAX_MISS_ATTEMPTS:
            skipped_misses += 1
            continue
        todo.append((rssd, name))
    if skipped_misses:
        print(f"[y9c] skipping {skipped_misses} holdcos confirmed to have no "
              f"FR Y-9C for {period} ({_MAX_MISS_ATTEMPTS}x onError — Y-9SP "
              "filers / foreign parents)")
    cap = _max_fetches()
    if len(todo) > cap:
        print(f"[y9c] fetch cap {cap}: deferring {len(todo) - cap} smaller "
              "holdcos to the next run")
        todo = todo[:cap]
    if not todo:
        print("[y9c] mirror already current — nothing to fetch")
        return 0

    fetched = absent = blocked = upload_failures = consec_upload_fails = 0
    aborted = False
    for i, (rssd, name) in enumerate(todo):
        if i:
            time.sleep(_SPACING_SECONDS)
        status, content = _fetch_y9c_classified(rssd, period)
        if status == _ABSENT:
            # Confirmed no Y-9C for this RSSD (NPW onError) — record toward the
            # permanent skip so it isn't retried forever. Trustworthy because
            # it's an application-level answer, not a network block.
            manifest[name] = manifest.get(name, 0) + 1
            absent += 1
            continue
        if status == _BLOCKED:
            # Cloudflare block of a (presumed) real filer — retry next run,
            # never record. This is the only "real" miss: a clean run has zero.
            blocked += 1
            continue
        if save_bytes(_GCS_PREFIX, name, content, "application/pdf"):
            manifest.pop(name, None)
            fetched += 1
            consec_upload_fails = 0
        else:
            print(f"FAIL {name}: GCS upload failed")
            upload_failures += 1
            consec_upload_fails += 1
            if consec_upload_fails >= _MAX_CONSEC_UPLOAD_FAILS:
                # Credential died mid-run (ADC reauth). Every further NPW
                # fetch is unsaveable bot-score burn — stop now. Re-auth
                # (gcloud auth application-default login) and re-run; the
                # already-mirrored objects are skipped.
                print(f"[y9c] aborting: {consec_upload_fails} consecutive "
                      "upload failures — GCS credential expired? Re-auth ADC "
                      "and re-run (mirrored objects are skipped).")
                aborted = True
                break

    print(f"[y9c] {'ABORTED' if aborted else 'done'}: {fetched} mirrored, "
          f"{absent} absent (no Y-9C filed), {blocked} blocked (Cloudflare — "
          f"will retry), {upload_failures} upload failures, "
          f"{i + 1}/{len(todo)} attempted")
    if aborted:
        return 1  # half-run on a dead credential — loud failure

    # Only confirmed-absent (deterministic onError) entries were written to
    # the manifest — Cloudflare blocks never touch it — so it's always safe to
    # persist; no misses>fetched guard needed. A clean batch has zero blocks;
    # a non-zero exit means "re-run to retry blocked filers" (or fix uploads).
    _save_manifest(manifest)
    return 1 if (upload_failures or blocked) else 0


if __name__ == "__main__":
    sys.exit(main())
