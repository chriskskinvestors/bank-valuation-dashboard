"""
Fresh-instance verification of the NIC bulk read path (Corporate Structure).

Runs INSIDE a Cloud Run job container (the nic-mirror-verify job that
.github/workflows/refresh-nic-bulk.yml deploys from the live service image
and executes after every mirror refresh). NPW 403s Cloud Run egress, so a
fresh instance can only get the bulk files from the GCS mirror — this
wipes the local bulk dir to simulate a brand-new instance's empty /tmp,
then proves the full chain: GCS mirror → download → unzip → chunked parse.

Exits non-zero (failing the workflow loudly) when a fresh production
instance could not serve the tab. Deliberately touches no data.cache /
DATABASE_URL — the bulk path is filesystem + GCS only.
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
