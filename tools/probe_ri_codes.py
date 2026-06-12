"""
One-off: discover RI/RI-E MDRM codes by value-matching Banner Bank's
12/31/2025 call report against the SNL FY-2025 Income Statement screenshot.
Creds are read from the gitignored verify tool at runtime — no secrets here.

Run: python -m tools.probe_ri_codes
"""
from __future__ import annotations

import os
import re
import sys
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
warnings.filterwarnings("ignore")

# Pull FFIEC creds out of the gitignored e2e tool without executing it.
src = (REPO_ROOT / "tools" / "verify_ffiec_e2e.py").read_text(encoding="utf-8")
user = re.search(r'FFIEC_USERNAME", "([^"]+)"', src).group(1)
jwt = "".join(re.findall(r'"(ey[^"]+|[A-Za-z0-9+/=_.-]{20,})"\s*\n', src))
# simpler: grab the setdefault block's string parts
m = re.search(r'FFIEC_JWT_TOKEN",\n((?:\s+"[^"]+"\n)+)', src)
jwt = "".join(re.findall(r'"([^"]+)"', m.group(1)))
os.environ.setdefault("FFIEC_USERNAME", user)
os.environ.setdefault("FFIEC_JWT_TOKEN", jwt)

import pandas as pd  # noqa: E402
import requests  # noqa: E402

# Banner Bank RSSD via FDIC institutions
r = requests.get(
    "https://banks.data.fdic.gov/api/institutions",
    params={"filters": "CERT:28489", "fields": "CERT,NAME,FED_RSSD"},
    timeout=30,
)
rssd = int(r.json()["data"][0]["data"]["FED_RSSD"])
print(f"Banner Bank RSSD: {rssd}")

from data.ffiec_client import fetch_call_report  # noqa: E402

df = fetch_call_report(rssd, "12/31/2025")
print(f"call report rows: {len(df)}")
if df.empty:
    sys.exit(1)

# SNL FY-2025 targets ($000, YTD = FY in the 12/31 report). HoldCo values —
# bank-sub may differ on some lines; exact matches confirm the mapping.
TARGETS = {
    "gain_on_sale_loans": 9_108,
    "boli_income": 10_152,
    "marketing": 4_748,
    "professional_fees": 9_492,
    "tech_comms": 33_067,
    "foreclosure_repo": -365,
    "amort_intang_gw": 1_567,
    "provision_loans": 11_637,
    "provision_unfunded": 1_408,
    "provision_total": 13_045,
    "trading_income": -1_384,
    "loan_fees": 4_136,
    "insurance_rev": 763,
    "inv_banking": 1_833,
    "fte_adjustment": 13_590,  # 601,509 - 587,919
    "interest_income": 804_955,
    "interest_expense": 217_036,
    "service_charges": 25_433,
    "comp_benefits": 243_487,
    "occupancy": 48_723,
    "other_expense": 65_745,
    "securities_gain": 374,
    "net_income": 195_382,
    "pretax_income": 238_914,
    "taxes": 43_532,
}

# Build value -> [codes] index over RIAD rows
vals: dict[float, list[str]] = {}
for _, row in df.iterrows():
    code = str(row.get("mdrm", "")).upper()
    if not code.startswith("RIAD"):
        continue
    dt = str(row.get("data_type", "")).lower()
    v = row.get("int_data") if dt == "int" else row.get("float_data") if dt == "float" else None
    if v is None or pd.isna(v):
        continue
    vals.setdefault(float(v), []).append(code)

for name, target in TARGETS.items():
    exact = vals.get(float(target), [])
    # FY totals can land within rounding of the holdco number
    near = [c for v, cs in vals.items() if abs(v - target) <= 3 and v != target for c in cs]
    print(f"{name:22} {target:>10,} -> exact: {exact}  near: {near[:4]}")
