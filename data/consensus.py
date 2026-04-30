"""
Consensus estimate parsing, storage, and comparison.

Supports PDF (via Anthropic SDK) and Excel/CSV uploads.
Stores parsed consensus as JSON in the consensus/ directory.
"""

import json
import os
import re
from pathlib import Path

import pandas as pd
import streamlit as st

from data.cloud_storage import save_json, load_json, list_files

CONSENSUS_DIR = Path(__file__).parent.parent / "consensus"
CONSENSUS_DIR.mkdir(exist_ok=True)

CONSENSUS_PREFIX = "consensus"

# ── Metric key mapping ───────────────────────────────────────────────────
# Maps common consensus metric names to our internal metric keys.
# The Anthropic parser will try to match these; Excel columns get matched too.
METRIC_ALIASES = {
    # EPS
    "eps": "eps", "earnings per share": "eps", "diluted eps": "eps",
    "diluted earnings per share": "eps",
    # NIM
    "nim": "nim", "net interest margin": "nim",
    # Efficiency
    "efficiency": "efficiency_ratio", "efficiency ratio": "efficiency_ratio",
    # ROAA
    "roaa": "roaa", "return on average assets": "roaa", "roa": "roaa",
    # ROATCE
    "roatce": "roatce", "return on average tangible common equity": "roatce",
    "rotce": "roatce", "roe": "roatce",
    # NPL
    "npl": "npl_ratio", "npl ratio": "npl_ratio",
    "non-performing loans": "npl_ratio", "npa ratio": "npl_ratio",
    # CET1
    "cet1": "cet1_ratio", "cet1 ratio": "cet1_ratio",
    "common equity tier 1": "cet1_ratio",
    # Net income
    "net income": "netinc", "net income ($m)": "netinc",
    "net income ($000)": "netinc",
    # Revenue
    "revenue": "revenue", "total revenue": "revenue",
    "net revenue": "revenue", "net interest income": "nii",
    # Total assets
    "total assets": "total_assets",
    # Deposits
    "total deposits": "dep", "deposits": "dep",
    # Loans
    "total loans": "lnlsnet", "loans": "lnlsnet",
    # TBV
    "tbv": "tbvps", "tangible book value": "tbvps",
    "tbv per share": "tbvps", "tangible book value per share": "tbvps",
    # Provision
    "provision": "provision", "provision for credit losses": "provision",
    "pcl": "provision",
    # Noninterest income
    "noninterest income": "nonii", "non-interest income": "nonii",
    "fee income": "nonii",
    # Noninterest expense
    "noninterest expense": "nonix", "non-interest expense": "nonix",
    # Dividend
    "dividend": "dps", "dividend per share": "dps", "dps": "dps",
    # Loan growth
    "loan growth": "loan_growth",
    # Deposit growth
    "deposit growth": "deposit_growth",
    # Net charge-offs
    "nco": "nco_ratio", "net charge-offs": "nco_ratio",
    "net charge-off ratio": "nco_ratio",
    # Cost of deposits
    "cost of deposits": "cost_of_deposits",
    # Yield on loans
    "yield on loans": "loan_yield", "loan yield": "loan_yield",
}

# Display names for metrics
METRIC_DISPLAY = {
    "eps": "Earnings Per Share",
    "nim": "Net Interest Margin",
    "efficiency_ratio": "Efficiency Ratio",
    "roaa": "ROAA",
    "roatce": "ROATCE",
    "npl_ratio": "NPL Ratio",
    "cet1_ratio": "CET1 Ratio",
    "netinc": "Net Income",
    "revenue": "Revenue",
    "nii": "Net Interest Income",
    "total_assets": "Total Assets",
    "dep": "Total Deposits",
    "lnlsnet": "Total Loans",
    "tbvps": "TBV Per Share",
    "provision": "Provision for Credit Losses",
    "nonii": "Noninterest Income",
    "nonix": "Noninterest Expense",
    "dps": "Dividend Per Share",
    "loan_growth": "Loan Growth",
    "deposit_growth": "Deposit Growth",
    "nco_ratio": "Net Charge-Off Ratio",
    "cost_of_deposits": "Cost of Deposits",
    "loan_yield": "Yield on Loans",
}

# Units for formatting
METRIC_UNITS = {
    "eps": "$", "nim": "%", "efficiency_ratio": "%", "roaa": "%",
    "roatce": "%", "npl_ratio": "%", "cet1_ratio": "%",
    "netinc": "$M", "revenue": "$M", "nii": "$M",
    "total_assets": "$B", "dep": "$M", "lnlsnet": "$M",
    "tbvps": "$", "provision": "$M", "nonii": "$M", "nonix": "$M",
    "dps": "$", "loan_growth": "%", "deposit_growth": "%",
    "nco_ratio": "%", "cost_of_deposits": "%", "loan_yield": "%",
}


def _normalize_key(name: str) -> str | None:
    """Map a metric name string to our internal key."""
    name_lower = name.lower().strip()
    return METRIC_ALIASES.get(name_lower)


# ── PDF Parsing (Anthropic SDK) ──────────────────────────────────────────

def parse_consensus_pdf(file_bytes: bytes, ticker: str, period: str) -> dict:
    """
    Parse a consensus PDF using Anthropic Claude to extract structured metrics.

    Returns: {ticker, period, source: "pdf", metrics: [{name, key, value, unit}]}
    """
    try:
        import anthropic
        import base64

        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", st.secrets.get("ANTHROPIC_API_KEY", "")))

        b64_pdf = base64.standard_b64encode(file_bytes).decode("utf-8")

        prompt = """Extract ALL consensus estimate metrics from this document.
For each metric, provide:
- name: the metric name exactly as written
- value: the numeric consensus/estimate value
- unit: the unit (%, $, $M, $B, bps, x, or blank)

Return ONLY a JSON array like:
[
  {"name": "EPS", "value": 1.25, "unit": "$"},
  {"name": "Net Interest Margin", "value": 3.45, "unit": "%"},
  {"name": "Efficiency Ratio", "value": 58.0, "unit": "%"}
]

Extract every metric you can find — EPS, revenue, NIM, efficiency, ROAA, ROATCE, NPL, CET1, net income, provision, deposits, loans, TBV, dividends, charge-offs, yields, costs, growth rates, etc.

Return ONLY the JSON array, no other text."""

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": b64_pdf,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )

        # Parse the response
        text = response.content[0].text.strip()
        # Extract JSON from response (might have markdown code blocks)
        json_match = re.search(r'\[.*\]', text, re.DOTALL)
        if json_match:
            raw_metrics = json.loads(json_match.group())
        else:
            raw_metrics = json.loads(text)

        # Map to our internal keys
        metrics = []
        for m in raw_metrics:
            key = _normalize_key(m["name"])
            metrics.append({
                "name": m["name"],
                "key": key,
                "value": float(m["value"]) if m["value"] is not None else None,
                "unit": m.get("unit", ""),
            })

        return {
            "ticker": ticker.upper(),
            "period": period,
            "source": "pdf",
            "metrics": metrics,
        }

    except Exception as e:
        return {
            "ticker": ticker.upper(),
            "period": period,
            "source": "pdf",
            "metrics": [],
            "error": str(e),
        }


def parse_bulk_consensus_pdf(file_bytes: bytes, period: str) -> dict:
    """
    Parse a PDF containing consensus estimates for MULTIPLE banks.

    Uses Anthropic Claude to extract structured data grouped by ticker.
    Works with broker research reports, sector summaries, multi-bank consensus docs.

    Returns same format as parse_bulk_consensus():
    {
        "results": [{"ticker": "JPM", "period": "2026Q1", "metrics_count": 8, "status": "saved"}, ...],
        "total_banks": 5,
        "total_metrics": 42,
        "errors": ["..."]
    }
    """
    try:
        import anthropic
        import base64

        client = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY", st.secrets.get("ANTHROPIC_API_KEY", ""))
        )

        b64_pdf = base64.standard_b64encode(file_bytes).decode("utf-8")

        prompt = """This document contains consensus estimates for MULTIPLE banks/companies.

For EACH bank/company mentioned, extract:
- ticker: the stock ticker symbol (e.g. "JPM", "BAC", "WFC")
- metrics: all consensus estimate metrics you can find

Return a JSON object grouped by ticker like this:
{
  "JPM": [
    {"name": "EPS", "value": 5.44, "unit": "$"},
    {"name": "Net Interest Margin", "value": 2.75, "unit": "%"},
    {"name": "Efficiency Ratio", "value": 55.2, "unit": "%"}
  ],
  "BAC": [
    {"name": "EPS", "value": 0.82, "unit": "$"},
    {"name": "NIM", "value": 1.95, "unit": "%"}
  ]
}

Rules:
- Use standard US stock ticker symbols (JPM not "JP Morgan")
- Extract every metric you can find per bank: EPS, revenue, NIM, efficiency, ROAA, ROATCE, ROE, NPL, CET1, net income, provision, deposits, loans, TBV, dividends, charge-offs, yields, costs, growth rates, noninterest income/expense, etc.
- If a bank name is mentioned but you're not sure of the ticker, use your best guess
- Values should be numeric (no commas or dollar signs in the value field)
- Unit should be one of: %, $, $M, $B, bps, x, or blank

Return ONLY the JSON object, no other text."""

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=8192,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": b64_pdf,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )

        # Parse response
        text = response.content[0].text.strip()
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            bank_data = json.loads(json_match.group())
        else:
            bank_data = json.loads(text)

        # Save each bank's consensus
        results = []
        total_metrics = 0
        errors = []

        for ticker, raw_metrics in bank_data.items():
            ticker = ticker.strip().upper()
            if not ticker or not raw_metrics:
                continue

            metrics = []
            for m in raw_metrics:
                key = _normalize_key(m.get("name", ""))
                try:
                    val = float(m["value"]) if m.get("value") is not None else None
                except (ValueError, TypeError):
                    continue

                metrics.append({
                    "name": m.get("name", ""),
                    "key": key,
                    "value": val,
                    "unit": m.get("unit", METRIC_UNITS.get(key, "")),
                })

            if metrics:
                data = {
                    "ticker": ticker,
                    "period": period,
                    "source": "bulk_pdf",
                    "metrics": metrics,
                }
                try:
                    save_consensus(data)
                    results.append({
                        "ticker": ticker,
                        "period": period,
                        "metrics_count": len(metrics),
                        "status": "saved",
                    })
                    total_metrics += len(metrics)
                except Exception as e:
                    errors.append(f"{ticker}: {e}")

        return {
            "results": results,
            "total_banks": len(results),
            "total_metrics": total_metrics,
            "errors": errors,
        }

    except Exception as e:
        return {
            "results": [],
            "total_banks": 0,
            "total_metrics": 0,
            "errors": [f"PDF parsing error: {e}"],
        }


# ── Excel/CSV Parsing ────────────────────────────────────────────────────

def parse_consensus_excel(file_bytes: bytes, ticker: str, period: str, filename: str = "") -> dict:
    """
    Parse a consensus Excel/CSV file.

    Expects columns like: Metric, Estimate/Consensus, Unit
    Or: rows with metric names and numeric values.
    """
    try:
        if filename.endswith(".csv"):
            df = pd.read_csv(pd.io.common.BytesIO(file_bytes))
        else:
            df = pd.read_excel(pd.io.common.BytesIO(file_bytes))

        metrics = []

        # Strategy 1: Look for columns named Metric + Value/Estimate/Consensus
        cols_lower = {c.lower().strip(): c for c in df.columns}
        metric_col = None
        value_col = None

        for name in ["metric", "item", "line item", "description", "name"]:
            if name in cols_lower:
                metric_col = cols_lower[name]
                break

        for name in ["estimate", "consensus", "value", "est", "mean", "median"]:
            if name in cols_lower:
                value_col = cols_lower[name]
                break

        if metric_col and value_col:
            for _, row in df.iterrows():
                name = str(row[metric_col]).strip()
                val = row[value_col]
                if pd.notna(val) and name:
                    key = _normalize_key(name)
                    try:
                        val = float(val)
                    except (ValueError, TypeError):
                        continue
                    unit = METRIC_UNITS.get(key, "")
                    metrics.append({"name": name, "key": key, "value": val, "unit": unit})
        else:
            # Strategy 2: First column = metric names, second = values
            if len(df.columns) >= 2:
                for _, row in df.iterrows():
                    name = str(row.iloc[0]).strip()
                    val = row.iloc[1]
                    if pd.notna(val) and name and name != "nan":
                        key = _normalize_key(name)
                        try:
                            val = float(val)
                        except (ValueError, TypeError):
                            continue
                        unit = METRIC_UNITS.get(key, "")
                        metrics.append({"name": name, "key": key, "value": val, "unit": unit})

        return {
            "ticker": ticker.upper(),
            "period": period,
            "source": "excel",
            "metrics": metrics,
        }

    except Exception as e:
        return {
            "ticker": ticker.upper(),
            "period": period,
            "source": "excel",
            "metrics": [],
            "error": str(e),
        }


# ── Bulk Multi-Bank Parsing ─────────────────────────────────────────────

def parse_bulk_consensus(file_bytes: bytes, period: str, filename: str = "") -> dict:
    """
    Parse a file with consensus estimates for MULTIPLE banks.

    Supports two formats:

    Wide format (one row per bank, metrics as columns):
        Ticker | EPS | NIM | Efficiency | ROATCE | ...
        JPM    | 5.44| 2.75| 55.2       | 18.5   | ...
        BAC    | 0.82| 1.95| 62.1       | 12.3   | ...

    Long format (one metric per row):
        Ticker | Metric    | Value
        JPM    | EPS       | 5.44
        JPM    | NIM       | 2.75
        BAC    | EPS       | 0.82

    Also supports multi-sheet Excel where each sheet = one bank (sheet name = ticker).

    Returns:
    {
        "results": [
            {"ticker": "JPM", "period": "2026Q1", "metrics_count": 8, "status": "saved"},
            ...
        ],
        "total_banks": 5,
        "total_metrics": 42,
        "errors": ["..."]
    }
    """
    results = []
    errors = []
    total_metrics = 0

    try:
        if filename.endswith(".csv"):
            dfs = {"Sheet1": pd.read_csv(pd.io.common.BytesIO(file_bytes))}
        else:
            # Try reading all sheets
            xls = pd.ExcelFile(pd.io.common.BytesIO(file_bytes))
            sheet_names = xls.sheet_names
            dfs = {name: pd.read_excel(xls, sheet_name=name) for name in sheet_names}
    except Exception as e:
        return {"results": [], "total_banks": 0, "total_metrics": 0,
                "errors": [f"Could not read file: {e}"]}

    for sheet_name, df in dfs.items():
        if df.empty:
            continue

        cols_lower = {c.lower().strip(): c for c in df.columns}

        # Detect if this sheet has a Ticker column
        ticker_col = None
        for name in ["ticker", "symbol", "bank", "company"]:
            if name in cols_lower:
                ticker_col = cols_lower[name]
                break

        if ticker_col:
            # Has a ticker column — could be wide or long format
            parsed = _parse_multi_bank_sheet(df, ticker_col, period, cols_lower)
            results.extend(parsed["results"])
            errors.extend(parsed["errors"])
            total_metrics += parsed["total_metrics"]
        elif len(dfs) > 1:
            # Multi-sheet mode: sheet name = ticker
            ticker = sheet_name.strip().upper()
            if len(ticker) <= 6 and ticker.isalpha():
                parsed = _parse_single_bank_sheet(df, ticker, period)
                if parsed:
                    results.append(parsed)
                    total_metrics += parsed["metrics_count"]
        else:
            errors.append(f"No 'Ticker' column found in sheet '{sheet_name}'. "
                         "Expected a column named Ticker, Symbol, Bank, or Company.")

    return {
        "results": results,
        "total_banks": len(results),
        "total_metrics": total_metrics,
        "errors": errors,
    }


def _parse_multi_bank_sheet(df: pd.DataFrame, ticker_col: str, period: str,
                             cols_lower: dict) -> dict:
    """Parse a sheet with multiple banks (has a Ticker column)."""
    results = []
    errors = []
    total_metrics = 0

    # Check if this is long format (has Metric + Value columns)
    metric_col = None
    value_col = None
    for name in ["metric", "item", "line item", "description", "name", "measure"]:
        if name in cols_lower:
            metric_col = cols_lower[name]
            break
    for name in ["value", "estimate", "consensus", "est", "mean", "median"]:
        if name in cols_lower:
            value_col = cols_lower[name]
            break

    if metric_col and value_col:
        # ── Long format: Ticker | Metric | Value ──
        bank_metrics = {}
        for _, row in df.iterrows():
            ticker = str(row[ticker_col]).strip().upper()
            metric_name = str(row[metric_col]).strip()
            val = row[value_col]

            if not ticker or ticker == "NAN" or not metric_name or metric_name == "NAN":
                continue
            if pd.isna(val):
                continue

            try:
                val = float(val)
            except (ValueError, TypeError):
                continue

            if ticker not in bank_metrics:
                bank_metrics[ticker] = []

            key = _normalize_key(metric_name)
            unit = METRIC_UNITS.get(key, "")
            bank_metrics[ticker].append({
                "name": metric_name,
                "key": key,
                "value": val,
                "unit": unit,
            })

        for ticker, metrics in bank_metrics.items():
            data = {
                "ticker": ticker,
                "period": period,
                "source": "bulk_upload",
                "metrics": metrics,
            }
            try:
                save_consensus(data)
                results.append({
                    "ticker": ticker, "period": period,
                    "metrics_count": len(metrics), "status": "saved",
                })
                total_metrics += len(metrics)
            except Exception as e:
                errors.append(f"{ticker}: {e}")

    else:
        # ── Wide format: Ticker | EPS | NIM | Efficiency | ... ──
        # All non-ticker columns are treated as metric names
        metric_columns = []
        for col in df.columns:
            if col == ticker_col:
                continue
            key = _normalize_key(col)
            metric_columns.append((col, key))

        for _, row in df.iterrows():
            ticker = str(row[ticker_col]).strip().upper()
            if not ticker or ticker == "NAN":
                continue

            metrics = []
            for col, key in metric_columns:
                val = row[col]
                if pd.isna(val):
                    continue
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    continue

                unit = METRIC_UNITS.get(key, "")
                metrics.append({
                    "name": col,
                    "key": key,
                    "value": val,
                    "unit": unit,
                })

            if metrics:
                data = {
                    "ticker": ticker,
                    "period": period,
                    "source": "bulk_upload",
                    "metrics": metrics,
                }
                try:
                    save_consensus(data)
                    results.append({
                        "ticker": ticker, "period": period,
                        "metrics_count": len(metrics), "status": "saved",
                    })
                    total_metrics += len(metrics)
                except Exception as e:
                    errors.append(f"{ticker}: {e}")

    return {"results": results, "total_metrics": total_metrics, "errors": errors}


def _parse_single_bank_sheet(df: pd.DataFrame, ticker: str, period: str) -> dict | None:
    """Parse a single-bank sheet (no ticker column, sheet name = ticker)."""
    cols_lower = {c.lower().strip(): c for c in df.columns}

    # Try to find metric + value columns
    metric_col = None
    value_col = None
    for name in ["metric", "item", "line item", "description", "name"]:
        if name in cols_lower:
            metric_col = cols_lower[name]
            break
    for name in ["estimate", "consensus", "value", "est", "mean", "median"]:
        if name in cols_lower:
            value_col = cols_lower[name]
            break

    metrics = []

    if metric_col and value_col:
        for _, row in df.iterrows():
            name = str(row[metric_col]).strip()
            val = row[value_col]
            if pd.notna(val) and name and name != "nan":
                key = _normalize_key(name)
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    continue
                unit = METRIC_UNITS.get(key, "")
                metrics.append({"name": name, "key": key, "value": val, "unit": unit})
    elif len(df.columns) >= 2:
        for _, row in df.iterrows():
            name = str(row.iloc[0]).strip()
            val = row.iloc[1]
            if pd.notna(val) and name and name != "nan":
                key = _normalize_key(name)
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    continue
                unit = METRIC_UNITS.get(key, "")
                metrics.append({"name": name, "key": key, "value": val, "unit": unit})

    if not metrics:
        return None

    data = {
        "ticker": ticker,
        "period": period,
        "source": "bulk_upload",
        "metrics": metrics,
    }
    save_consensus(data)

    return {
        "ticker": ticker, "period": period,
        "metrics_count": len(metrics), "status": "saved",
    }


# ── Storage ──────────────────────────────────────────────────────────────

def save_consensus(data: dict) -> Path:
    """Save parsed consensus to JSON (local + GCS). Returns the local file path."""
    ticker = data["ticker"].upper()
    period = data["period"].replace("/", "-").replace(" ", "_")
    filename = f"{ticker}_{period}.json"

    # Save to both local and GCS
    save_json(CONSENSUS_PREFIX, filename, data)

    return CONSENSUS_DIR / filename


def save_manual_consensus(ticker: str, period: str, metrics_dict: dict) -> Path:
    """
    Save manually entered consensus estimates.

    Args:
        ticker: Bank ticker
        period: Period string (e.g. "2026Q1")
        metrics_dict: {metric_key: value} e.g. {"eps": 1.25, "nim": 3.45}

    Returns the file path.
    """
    metrics = []
    for key, value in metrics_dict.items():
        if value is not None:
            metrics.append({
                "name": METRIC_DISPLAY.get(key, key),
                "key": key,
                "value": float(value),
                "unit": METRIC_UNITS.get(key, ""),
            })

    data = {
        "ticker": ticker.upper(),
        "period": period,
        "source": "manual",
        "metrics": metrics,
    }
    return save_consensus(data)


def load_consensus(ticker: str, period: str | None = None) -> dict | None:
    """Load consensus for a ticker. If period is None, loads the latest."""
    ticker = ticker.upper()

    # Get all files for this ticker (from GCS + local)
    all_files = sorted(
        [f for f in list_files(CONSENSUS_PREFIX) if f.startswith(f"{ticker}_")],
        reverse=True,
    )

    if period:
        period_clean = period.replace("/", "-").replace(" ", "_")
        for f in all_files:
            if period_clean in f.replace(".json", ""):
                return load_json(CONSENSUS_PREFIX, f)
    elif all_files:
        return load_json(CONSENSUS_PREFIX, all_files[0])
    return None


def list_consensus(ticker: str) -> list[dict]:
    """List all consensus periods for a ticker."""
    ticker = ticker.upper()
    results = []

    all_files = sorted(
        [f for f in list_files(CONSENSUS_PREFIX) if f.startswith(f"{ticker}_")],
        reverse=True,
    )

    for filename in all_files:
        data = load_json(CONSENSUS_PREFIX, filename)
        if data:
            results.append({
                "period": data.get("period", ""),
                "source": data.get("source", ""),
                "metric_count": len(data.get("metrics", [])),
                "file": filename,
            })
    return results


def list_all_consensus() -> dict[str, list[dict]]:
    """List consensus data for all tickers. Returns {ticker: [periods]}."""
    result = {}

    for filename in list_files(CONSENSUS_PREFIX):
        data = load_json(CONSENSUS_PREFIX, filename)
        if not data:
            continue
        ticker = data.get("ticker", filename.split("_")[0])
        if ticker not in result:
            result[ticker] = []
        result[ticker].append({
            "period": data.get("period", ""),
            "source": data.get("source", ""),
            "metric_count": len(data.get("metrics", [])),
        })
    return result


# ── Comparison ───────────────────────────────────────────────────────────

def compare_consensus_to_actual(
    consensus: dict,
    actual_metrics: dict,
) -> list[dict]:
    """
    Compare consensus estimates to actual reported metrics.

    Returns list of dicts:
    [{metric_name, key, consensus, actual, delta, delta_pct, beat_miss, unit}]

    beat_miss: "beat", "miss", "inline" (within 1%), or "n/a"
    """
    results = []

    for m in consensus.get("metrics", []):
        key = m.get("key")
        consensus_val = m.get("value")
        name = m.get("name", "")
        unit = m.get("unit", METRIC_UNITS.get(key, ""))

        if consensus_val is None or key is None:
            results.append({
                "metric_name": name,
                "key": key,
                "consensus": consensus_val,
                "actual": None,
                "delta": None,
                "delta_pct": None,
                "beat_miss": "n/a",
                "unit": unit,
            })
            continue

        actual_val = actual_metrics.get(key)

        if actual_val is None:
            results.append({
                "metric_name": METRIC_DISPLAY.get(key, name),
                "key": key,
                "consensus": consensus_val,
                "actual": None,
                "delta": None,
                "delta_pct": None,
                "beat_miss": "n/a",
                "unit": unit,
            })
            continue

        delta = actual_val - consensus_val
        delta_pct = (delta / abs(consensus_val) * 100) if consensus_val != 0 else 0

        # Determine beat/miss
        # For most metrics, higher = beat. For efficiency/NPL/NCO, lower = beat.
        lower_is_better = key in ("efficiency_ratio", "npl_ratio", "nco_ratio",
                                   "nonix", "cost_of_deposits")

        if abs(delta_pct) <= 1.0:
            beat_miss = "inline"
        elif lower_is_better:
            beat_miss = "beat" if delta < 0 else "miss"
        else:
            beat_miss = "beat" if delta > 0 else "miss"

        results.append({
            "metric_name": METRIC_DISPLAY.get(key, name),
            "key": key,
            "consensus": consensus_val,
            "actual": actual_val,
            "delta": delta,
            "delta_pct": delta_pct,
            "beat_miss": beat_miss,
            "unit": unit,
        })

    return results
