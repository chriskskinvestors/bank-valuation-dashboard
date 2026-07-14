"""
Fresh-instance verification of the NIC mirror read paths.

Runs INSIDE a Cloud Run job container (the nic-mirror-verify job that
.github/workflows/refresh-nic-bulk.yml deploys from the live service image
and executes after every mirror refresh). NPW 403s Cloud Run egress, so a
fresh instance can only get NIC data from the GCS mirrors — this proves
both prod paths end-to-end:

  1. Bulk zips (Corporate Structure): wipe the local bulk dir to simulate
     a brand-new instance's empty /tmp, then GCS mirror → download →
     unzip → chunked parse.
  2. FR Y-9C facsimiles (Recent Documents → Regulatory Filings): the y9c/
     mirror must hold at least one PDF and fetch_y9c_pdf must serve it —
     on Cloud Run (CLOUD_RUN_JOB is set here) it never falls through to
     NPW direct, so this exercises exactly the mirror-only prod ladder.

Exits non-zero (failing the workflow loudly) when a fresh production
instance could not serve either. Deliberately touches no data.cache /
DATABASE_URL — both paths are filesystem + GCS only.
"""
from __future__ import annotations

import shutil
import sys

from data import nic_client


def main() -> int:
    shutil.rmtree(nic_client._BULK_DIR, ignore_errors=True)  # fresh /tmp

    rel = nic_client._bulk_path("relationships")
    attr = nic_client._bulk_path("attributes_active")
    if rel is None or attr is None:
        print("FAIL: bulk files unavailable on a fresh instance "
              f"(relationships={rel}, attributes={attr})")
        return 1

    with nic_client._read_chunks(rel, nic_client.REL_COLS) as reader:
        active = len(nic_client._active_edges(next(reader)))
    with nic_client._read_chunks(attr, nic_client.ATTR_COLS) as reader:
        named = int((next(reader)["NM_LGL"].str.strip() != "").sum())

    # The real files have ~290k relationship rows / ~140k entities; the
    # first 100k-row chunk of each carries thousands of usable rows. A
    # near-empty parse means a truncated/garbled mirror object.
    if active < 1_000 or named < 1_000:
        print(f"FAIL: parse looks degenerate (active edges in first chunk: "
              f"{active}, named entities in first chunk: {named})")
        return 1

    print(f"OK: fresh instance served both bulk files "
          f"({active:,} active edges / {named:,} named entities in the "
          "first chunks)")

    from data.cloud_storage import list_files
    pdfs = [n for n in list_files("y9c", "*.pdf") if n.count("_") == 1]
    if not pdfs:
        print("FAIL: y9c/ mirror holds no facsimiles — Y-9C downloads "
              "cannot serve in prod (did tools/refresh_y9c_mirror run?)")
        return 1
    rssd, yyyymmdd = pdfs[0][:-len(".pdf")].split("_")
    got = nic_client.fetch_y9c_pdf(int(rssd), yyyymmdd)
    if not got or not got.startswith(b"%PDF-"):
        print(f"FAIL: fetch_y9c_pdf({rssd}, {yyyymmdd}) did not serve the "
              "mirrored facsimile on Cloud Run")
        return 1
    print(f"OK: Y-9C mirror served {pdfs[0]} ({len(got):,} bytes); "
          f"{len(pdfs)} facsimiles mirrored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
