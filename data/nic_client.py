"""
Fed NIC (National Information Center) organizational-hierarchy client.

Powers the Corporate Structure sub-tab (docs/SNL-BUILD-PLAN.md §12): the
parent → subsidiaries tree with ownership percentages, like SNL's
Banner Corp → Banner Bank → statutory trusts view. The Fed's NIC bulk
download is the primary source for who-owns-whom across every regulated
banking organization.

Bulk files (CSV-in-zip, verified live 2026-06-11 from the download page
https://www.ffiec.gov/npw/FinancialReport/DataDownload):

  ATTRIBUTES-ACTIVE  /npw/FinancialReport/ReturnAttributesActiveZipFileCSV
                     one row per active entity: #ID_RSSD, NM_LGL,
                     ENTITY_TYPE, CITY, STATE_ABBR_NM, CNTRY_NM, ...
  RELATIONSHIPS      /npw/FinancialReport/ReturnRelationshipsZipFileCSV
                     one row per parent→offspring relationship spell:
                     #ID_RSSD_PARENT, ID_RSSD_OFFSPRING, PCT_EQUITY,
                     CTRL_IND, DT_END (99991231 = still active), ...

The files are tens of MB uncompressed (~140k entities / ~290k relationship
rows), so they are downloaded at most once per 30 days to a local bulk dir
and ALWAYS parsed with pandas chunking + usecols — the full file is never
held in memory per request. The parsed per-RSSD slices (one tree / one
parent lookup) are cached in data.cache for 30 days via the shared
freshness check. Note the cache backend's own global TTL (24h) expires
entries first locally; worst case is a daily re-parse from the on-disk
bulk file (no re-download), and the 30d stamp stays correct if the
backend TTL is ever raised.

Functions (RSSD ids come from FDIC institutions FED_RSSD — callers
already have them via the fdic mapping):

  get_org_hierarchy(rssd_id) — tree DOWN from the given RSSD (the holdco):
      {entity: {name, rssd, type, type_code, location},
       children: [same shape + {ownership_pct, relationship}]}
      Depth-limited to MAX_DEPTH (4) generations below the root.
      None when the RSSD is unknown / data unavailable.

  get_parent(rssd_id)       — immediate parent entity dict or None.

Download path: ffiec.gov sits behind Cloudflare bot management, which
403s Python's TLS fingerprint (requests AND urllib, any headers) while
allowing curl's. So the fetch tries the shared requests policy first
(in case the rules relax) and falls back to a curl subprocess — curl is
in the deploy image (Dockerfile installs it for health checks) and ships
with Windows 10+.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd

NIC_BASE = "https://www.ffiec.gov/npw/FinancialReport"
BULK_URLS = {
    "attributes_active": f"{NIC_BASE}/ReturnAttributesActiveZipFileCSV",
    "relationships": f"{NIC_BASE}/ReturnRelationshipsZipFileCSV",
}
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")

BULK_TTL_SECONDS = 30 * 86400   # re-download the bulk files monthly
CACHE_TTL_SECONDS = 30 * 86400  # per-RSSD parsed slices in data.cache
CHUNK_ROWS = 100_000            # pandas chunksize — never the whole file
MAX_DEPTH = 4                   # generations below the root
ACTIVE_DT_END = 99991231        # NIC sentinel: relationship still open

_BULK_DIR = Path(tempfile.gettempdir()) / "nic_bulk"

REL_COLS = ["#ID_RSSD_PARENT", "ID_RSSD_OFFSPRING", "DT_END",
            "PCT_EQUITY", "CTRL_IND"]
ATTR_COLS = ["#ID_RSSD", "NM_LGL", "ENTITY_TYPE", "CITY",
             "STATE_ABBR_NM", "CNTRY_NM"]

# NIC ENTITY_TYPE codes → display names (NIC data dictionary). Unknown
# codes fall back to the raw code — never a guessed label.
ENTITY_TYPES = {
    "BHC": "Bank Holding Company",
    "FHD": "Financial Holding Company",
    "FHF": "Financial Holding Company (Foreign)",
    "SLHC": "Savings & Loan Holding Company",
    "IHC": "Intermediate Holding Company",
    "NAT": "National Bank",
    "SMB": "State Member Bank",
    "NMB": "State Non-member Bank",
    "SB": "Savings Bank",
    "SSB": "State Savings Bank",
    "FSB": "Federal Savings Bank",
    "SAL": "Savings & Loan Association",
    "CPB": "Cooperative Bank",
    "DEO": "Domestic Entity (Other)",
    "FEO": "Foreign Entity (Other)",
    "FNC": "Finance Company",
    "SBD": "Securities Broker/Dealer",
    "MTC": "Non-deposit Trust Company (Member)",
    "NTC": "Non-deposit Trust Company (Non-member)",
    "DBR": "Domestic Branch",
    "IBR": "Foreign Branch of U.S. Bank",
    "EDB": "Edge Corporation (Banking)",
    "EDI": "Edge Corporation (Investment)",
    "AGB": "Agreement Corporation (Banking)",
    "AGI": "Agreement Corporation (Investment)",
    "FBK": "Foreign Bank",
    "FBO": "Foreign Banking Organization",
    "FBH": "Foreign Banking Organization as BHC",
    "IFB": "Insured Federal Branch of FBO",
    "ISB": "Insured State Branch of FBO",
    "FCU": "Federal Credit Union",
    "SCU": "State Credit Union",
    "INB": "International Non-bank Subsidiary",
    "NYI": "New York Investment Company",
    "DPS": "Data Processing Servicer",
    "CSA": "Covered Savings Association",
}


# Shared freshness check (data/freshness) bound to this module's TTL.
def _is_fresh(cached: dict | None) -> bool:
    from data.freshness import is_fresh
    return is_fresh(cached, CACHE_TTL_SECONDS)


# ──────────────────────────────────────────────────────────────────────────
# Bulk file download (once per 30 days, to disk — never into the cache DB)
# ──────────────────────────────────────────────────────────────────────────

def _bulk_path(name: str) -> Path | None:
    """
    Local path of one bulk zip, downloading it when missing or older than
    BULK_TTL_SECONDS. A stale file on disk is the fallback when the
    download fails (same philosophy as the universe snapshot). None only
    when there is no file at all.
    """
    path = _BULK_DIR / f"{name}.zip"
    try:
        if path.exists() and path.stat().st_size > 0 \
                and time.time() - path.stat().st_mtime < BULK_TTL_SECONDS:
            return path
    except OSError:
        pass

    content = _download(BULK_URLS[name], name)
    if content is None:
        return path if path.exists() else None  # stale file beats nothing
    try:
        _BULK_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(content)
        tmp.replace(path)
        return path
    except OSError as e:
        print(f"[nic] {name} write error: {type(e).__name__}: {e}")
        return path if path.exists() else None


def _download(url: str, name: str) -> bytes | None:
    """Fetch one bulk zip. requests first (shared retry policy), curl
    subprocess as the Cloudflare-fingerprint fallback (see module doc).
    Returns validated zip bytes or None — HTML error pages are never
    saved as data."""
    try:
        from data.http import get_with_retry
        resp = get_with_retry(url, headers={"User-Agent": USER_AGENT},
                              timeout=120)
        if resp is not None and resp.content.startswith(b"PK"):
            return resp.content
        if resp is not None:
            print(f"[nic] {name}: requests got non-zip "
                  f"({resp.content[:40]!r}) — trying curl")
    except Exception as e:
        print(f"[nic] {name}: requests failed ({type(e).__name__}: {e})"
              " — trying curl")

    curl = shutil.which("curl")
    if not curl:
        print(f"[nic] {name}: no curl on PATH — download unavailable")
        return None
    try:
        proc = subprocess.run(
            [curl, "-sS", "--fail", "-L", "-A", USER_AGENT,
             "--max-time", "240", url],
            capture_output=True, timeout=300)
        if proc.returncode == 0 and proc.stdout.startswith(b"PK"):
            return proc.stdout
        print(f"[nic] {name}: curl failed (rc={proc.returncode}, "
              f"{(proc.stderr or proc.stdout)[:80]!r})")
    except Exception as e:
        print(f"[nic] {name} curl error: {type(e).__name__}: {e}")
    return None


# ──────────────────────────────────────────────────────────────────────────
# Chunked scans — usecols + chunksize; the full file never sits in memory
# ──────────────────────────────────────────────────────────────────────────

def _read_chunks(path: Path, usecols: list[str]):
    """Chunked CSV reader (pandas reads inside the single-file zip).
    Everything comes back as strings; numeric parsing is explicit."""
    return pd.read_csv(path, usecols=usecols, chunksize=CHUNK_ROWS,
                       dtype=str, keep_default_na=False)

def _int(raw) -> int | None:
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None

def _pct(raw) -> float | None:
    """PCT_EQUITY → float. 0 means not-reported / control-via-other-basis
    in NIC — that becomes None, never a plausible-wrong 0%."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _active_edges(chunk: pd.DataFrame) -> pd.DataFrame:
    """Filter one RELATIONSHIPS chunk to active rows (DT_END sentinel),
    with parsed numeric parent/offspring columns attached."""
    sub = chunk.assign(
        _parent=pd.to_numeric(chunk["#ID_RSSD_PARENT"], errors="coerce"),
        _child=pd.to_numeric(chunk["ID_RSSD_OFFSPRING"], errors="coerce"),
        _dt_end=pd.to_numeric(chunk["DT_END"], errors="coerce"),
    )
    return sub[(sub["_dt_end"] == ACTIVE_DT_END)
               & sub["_parent"].notna() & sub["_child"].notna()]


def _edge(row) -> dict:
    return {
        "rssd": int(row["_child"]),
        "parent_rssd": int(row["_parent"]),
        "ownership_pct": _pct(row["PCT_EQUITY"]),
        "controlled": _int(row["CTRL_IND"]) == 1,
    }


def _scan_children(parents: set[int], rel_path: Path) -> dict[int, list[dict]]:
    """One chunked pass over RELATIONSHIPS: active edges whose parent is
    in `parents`. Returns {parent_rssd: [edge, ...]}."""
    out: dict[int, list[dict]] = {}
    with _read_chunks(rel_path, REL_COLS) as reader:
        for chunk in reader:
            act = _active_edges(chunk)
            for _, row in act[act["_parent"].isin(parents)].iterrows():
                e = _edge(row)
                out.setdefault(e["parent_rssd"], []).append(e)
    return out


def _scan_parent_edges(rssd: int, rel_path: Path) -> list[dict]:
    """One chunked pass over RELATIONSHIPS: active edges whose offspring
    is `rssd`. Each edge's `rssd` is rewritten to the PARENT side."""
    out: list[dict] = []
    with _read_chunks(rel_path, REL_COLS) as reader:
        for chunk in reader:
            act = _active_edges(chunk)
            for _, row in act[act["_child"] == rssd].iterrows():
                e = _edge(row)
                e["rssd"] = e["parent_rssd"]
                out.append(e)
    return out


def _load_attributes(rssds: set[int], attr_path: Path) -> dict[int, dict]:
    """One chunked pass over ATTRIBUTES-ACTIVE for just the RSSDs we need.
    Returns {rssd: entity dict} (see _entity for the shape)."""
    out: dict[int, dict] = {}
    with _read_chunks(attr_path, ATTR_COLS) as reader:
        for chunk in reader:
            ids = pd.to_numeric(chunk["#ID_RSSD"], errors="coerce")
            for _, row in chunk[ids.isin(rssds)].iterrows():
                rssd = _int(row["#ID_RSSD"])
                out[rssd] = _entity(rssd, row)
            if len(out) == len(rssds):
                break  # found everything — stop reading
    return out


def _entity(rssd: int, row) -> dict:
    """Shape one ATTRIBUTES row into the entity dict."""
    name = (row["NM_LGL"] or "").strip()
    type_code = (row["ENTITY_TYPE"] or "").strip()
    city = (row["CITY"] or "").strip().title()
    state = (row["STATE_ABBR_NM"] or "").strip()
    country = (row["CNTRY_NM"] or "").strip()
    if city and state and state != "0":
        location = f"{city}, {state}"
    elif city and country and country.upper() != "UNITED STATES":
        location = f"{city}, {country.title()}"
    else:
        location = city or None
    return {
        "name": name or None,
        "rssd": rssd,
        "type": ENTITY_TYPES.get(type_code, type_code or None),
        "type_code": type_code or None,
        "location": location,
    }


def _unknown_entity(rssd: int) -> dict:
    """A child present in RELATIONSHIPS but absent from ATTRIBUTES-ACTIVE
    (e.g. just closed). Honest unknowns — never invented fields."""
    return {"name": None, "rssd": rssd, "type": None,
            "type_code": None, "location": None}


# ──────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────

def get_org_hierarchy(rssd_id: int) -> dict | None:
    """
    Organizational tree DOWN from `rssd_id` (give it the holdco RSSD):

      {entity: {name, rssd, type, type_code, location},
       children: [{entity: {...}, ownership_pct, relationship,
                   children: [...]}, ...],
       as_of, cached_at}

    Children are sorted by ownership desc, then name. Depth-limited to
    MAX_DEPTH generations. None when the RSSD isn't an active NIC entity
    or the bulk data is unavailable.
    """
    rssd_id = _int(rssd_id)
    if rssd_id is None:
        return None

    from data import cache
    key = f"nic:tree:{rssd_id}"
    cached = cache.get(key)
    if _is_fresh(cached):
        return cached

    rel_path = _bulk_path("relationships")
    attr_path = _bulk_path("attributes_active")
    if rel_path is None or attr_path is None:
        print(f"[nic] tree {rssd_id}: bulk files unavailable")
        return None

    try:
        # BFS, one chunked relationships scan per generation (≤ MAX_DEPTH).
        edges_by_parent: dict[int, list[dict]] = {}
        visited = {rssd_id}
        frontier = {rssd_id}
        for _depth in range(MAX_DEPTH):
            if not frontier:
                break
            found = _scan_children(frontier, rel_path)
            edges_by_parent.update(found)
            next_frontier = set()
            for edges in found.values():
                for e in edges:
                    if e["rssd"] not in visited:
                        visited.add(e["rssd"])
                        next_frontier.add(e["rssd"])
            frontier = next_frontier

        # One chunked attributes scan for every entity in the tree.
        attrs = _load_attributes(visited, attr_path)
        if rssd_id not in attrs:
            print(f"[nic] tree {rssd_id}: RSSD not in ATTRIBUTES-ACTIVE")
            return None

        def build(rssd: int, path: frozenset[int]) -> list[dict]:
            children = []
            for e in sorted(edges_by_parent.get(rssd, []),
                            key=lambda e: (-(e["ownership_pct"] or 0),
                                           str(e["rssd"]))):
                child = e["rssd"]
                node = {
                    "entity": attrs.get(child) or _unknown_entity(child),
                    "ownership_pct": e["ownership_pct"],
                    "relationship": "Controlled" if e["controlled"]
                                    else "Non-controlled",
                    # cycle guard: never recurse into an ancestor
                    "children": [] if child in path
                                else build(child, path | {child}),
                }
                children.append(node)
            return children

        tree = {
            "entity": attrs[rssd_id],
            "children": build(rssd_id, frozenset({rssd_id})),
            "as_of": datetime.now().strftime("%Y-%m-%d"),
            "cached_at": datetime.now().isoformat(),
        }
    except Exception as e:
        print(f"[nic] tree {rssd_id} error: {type(e).__name__}: {e}")
        return None

    cache.put(key, tree)
    return tree


def get_parent(rssd_id: int) -> dict | None:
    """
    Immediate parent entity of `rssd_id` (e.g. Banner Bank → Banner
    Corporation): {name, rssd, type, type_code, location, ownership_pct,
    relationship} or None when the entity is top-of-chain / unknown.
    When several active parents exist, the controlling one with the
    highest equity stake wins.
    """
    rssd_id = _int(rssd_id)
    if rssd_id is None:
        return None

    from data import cache
    key = f"nic:parent:{rssd_id}"
    cached = cache.get(key)
    if _is_fresh(cached):
        return cached.get("parent")

    rel_path = _bulk_path("relationships")
    attr_path = _bulk_path("attributes_active")
    if rel_path is None or attr_path is None:
        print(f"[nic] parent {rssd_id}: bulk files unavailable")
        return None

    try:
        edges = _scan_parent_edges(rssd_id, rel_path)
        parent = None
        if edges:
            best = max(edges, key=lambda e: (e["controlled"],
                                             e["ownership_pct"] or 0))
            attrs = _load_attributes({best["rssd"]}, attr_path)
            entity = attrs.get(best["rssd"]) or _unknown_entity(best["rssd"])
            parent = {**entity,
                      "ownership_pct": best["ownership_pct"],
                      "relationship": "Controlled" if best["controlled"]
                                      else "Non-controlled"}
    except Exception as e:
        print(f"[nic] parent {rssd_id} error: {type(e).__name__}: {e}")
        return None

    # Cache "has no parent" too — top-of-chain holdcos are the common case.
    cache.put(key, {"parent": parent,
                    "cached_at": datetime.now().isoformat()})
    return parent
