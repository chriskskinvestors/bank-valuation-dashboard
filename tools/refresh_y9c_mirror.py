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
largest first. Fetches are curl-only and spaced (back-to-back NPW hits
trip bot scoring even on allowed networks), skip objects already
mirrored, and a holdco that misses _MAX_MISS_ATTEMPTS times for a period
(sub-floor consolidated assets → files FR Y-9SP, or a foreign parent with
no IHC) is recorded in y9c/_manifest.json and never retried for that
period — misses must not burn spaced hits every run forever. If the GCS
credential dies mid-run, a consecutive-upload-failure circuit breaker
aborts rather than burning bot score on fetches it can't persist.
"""
from __future__ import annotations

import json
import os
import sys
import time

from data.cloud_storage import (is_gcs_enabled, list_files, load_bytes,
                                save_bytes)
from data.ffiec_client import latest_reporting_period
from data.http import get_with_retry
from data.nic_client import NIC_BASE, _curl_fetch, y9c_mirror_name
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
# Permanent-skip threshold. A "miss" is any non-PDF result, but curl can't
# cleanly tell a genuine "this holdco doesn't file Y-9C" from a Cloudflare
# 403/challenge page (both are non-PDF; observed 2026-07-14 the dev-box
# misses were ALL Cloudflare challenge bodies, not real absences). Since
# essentially every holdco above the $3B floor MUST file Y-9C, true
# non-filers are rare and misses are Cloudflare-dominated. The two are
# separable by frequency: a true non-filer misses on EVERY attempt (reaches
# any cap deterministically), while a transiently-challenged real filer
# misses only ~25% of the time — so a high cap lets persistent non-filers
# stop after a handful of runs while a real filer almost never accumulates
# enough consecutive block-misses to be wrongly dropped (dropping one =
# silently serving only the "View on NIC" fallback for a bank that does
# file). Paired with the misses>fetched run-guard below, false-skips are
# negligible. Cost of a higher cap: a few wasted 30s fetches on each rare
# true non-filer, spread across runs.
_MAX_MISS_ATTEMPTS = 6
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


def main() -> int:
    if not is_gcs_enabled():
        print("GCS_BUCKET is not set — nothing to mirror to.")
        return 1

    period = _latest_y9c_period()
    targets = _fetch_fdic_records()
    targets = _holdco_targets_from_records(targets)
    existing = set(list_files(_GCS_PREFIX, "*.pdf"))
    manifest = _load_manifest()
    print(f"[y9c] period {period}: {len(targets)} holdcos above the Y-9C "
          f"floor, {len(existing)} facsimiles already mirrored")

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
        print(f"[y9c] skipping {skipped_misses} holdcos that already missed "
              f"{_MAX_MISS_ATTEMPTS}x for {period} (Y-9SP filers?)")
    cap = _max_fetches()
    if len(todo) > cap:
        print(f"[y9c] fetch cap {cap}: deferring {len(todo) - cap} smaller "
              "holdcos to the next run")
        todo = todo[:cap]
    if not todo:
        print("[y9c] mirror already current — nothing to fetch")
        return 0

    fetched = misses = upload_failures = consec_upload_fails = 0
    aborted = False
    for i, (rssd, name) in enumerate(todo):
        if i:
            time.sleep(_SPACING_SECONDS)
        url = (f"{NIC_BASE}/ReturnFinancialReportPDF?rpt=FRY9C"
               f"&id={rssd}&dt={period}")
        content = _curl_fetch(url, f"y9c_{rssd}_{period}", magic=b"%PDF-")
        if content is None or len(content) < _MIN_PDF_BYTES:
            if content is not None:
                print(f"[y9c] {name}: only {len(content)} bytes — not a "
                      "facsimile")
            manifest[name] = manifest.get(name, 0) + 1
            misses += 1
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
          f"{misses} misses, {upload_failures} upload failures, "
          f"{i + 1}/{len(todo)} attempted")
    if aborted:
        return 1  # don't persist miss counts on a half-run; loud failure

    if misses > fetched:
        # A miss looks the same whether the holdco doesn't file Y-9C or
        # Cloudflare started blocking THIS network (possibly mid-run), so
        # when misses dominate, don't persist the incremented counts —
        # they'd poison real filers' manifest entries for the period.
        # True non-filers just get retried next run (30s each, bounded).
        print("[y9c] misses outnumber successes — NOT persisting miss "
              "counts (Cloudflare block?)")
        if fetched == 0 and len(todo) >= 20:
            return 1  # a big run yielding nothing at all is a blocked network
        return 1 if upload_failures else 0
    _save_manifest(manifest)
    return 1 if upload_failures else 0


if __name__ == "__main__":
    sys.exit(main())
