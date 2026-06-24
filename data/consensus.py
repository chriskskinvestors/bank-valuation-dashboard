"""
Consensus estimate parsing, storage, and comparison.

Supports PDF (via Anthropic SDK) and Excel/CSV uploads.
Stores parsed consensus as JSON in the consensus/ directory.
"""

import json
import math
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


# Consensus metric key → the build_bank_metrics key that carries the ACTUAL value.
# These differ because the consensus aliases/manual form predate the metrics
# config (e.g. consensus "netinc" vs the actual "net_income"). Without this map
# the comparison silently returned n/a for 8 metrics. None = no actual exists, so
# the comparison stays n/a honestly (revenue has no single actual; the actuals
# carry total dividends, not per-share). See data/consensus tests + AUDIT doc.
CONSENSUS_ACTUAL_KEY = {
    "netinc": "net_income",
    "nii": "net_interest_income",
    "nonii": "nonint_income",
    "nonix": "nonint_expense",
    "dep": "total_deposits",
    "lnlsnet": "total_loans",
    "revenue": None,
    "dps": None,
}

# Unit string → multiplier to raw dollars. Actuals store $-amounts in RAW dollars
# (analysis/metrics.py converts FDIC thousands ×1000), while consensus is entered
# in a display magnitude ($M/$B). Both sides are converted to the metric's
# canonical magnitude before comparison so we never subtract $M from raw $ (which
# produced ×10^6–10^9-wrong beat/miss verdicts — the cardinal-rule violation).
_UNIT_TO_RAW = {"$B": 1e9, "$M": 1e6, "$000": 1e3, "$K": 1e3}


def _normalize_key(name: str) -> str | None:
    """Map a metric name string to our internal key."""
    name_lower = name.lower().strip()
    return METRIC_ALIASES.get(name_lower)


def _finite_float(x) -> float | None:
    """Coerce to a finite float, or None — rejects NaN/inf and non-numerics so a
    misparsed value never becomes a fabricated consensus estimate."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _known_ticker(ticker: str) -> bool:
    """True if the ticker is a real bank in our universe — guards the PDF parser,
    which is told to extract tickers and could otherwise save consensus under a
    hallucinated/wrong symbol (a wrong-entity join). Fails OPEN (returns True) only
    if the universe itself can't be loaded, so a transient outage never silently
    drops every upload."""
    try:
        from data.bank_universe import get_universe
        return ticker.upper() in get_universe()
    except Exception:
        return True


# ── PDF Parsing (Anthropic SDK) ──────────────────────────────────────────

def _anthropic_client():
    """Anthropic client keyed from the environment (Cloud Run injects
    ANTHROPIC_API_KEY from Secret Manager) or local Streamlit secrets.

    st.secrets RAISES "No secrets found" when there is no secrets.toml (as on
    Cloud Run), so it must be read lazily and guarded — the old inline
    `os.environ.get(KEY, st.secrets.get(KEY))` evaluated the st.secrets default
    eagerly on every call and crashed PDF parsing even though the env var was
    set. Raises a clear error when no key is configured anywhere."""
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        try:
            key = st.secrets.get("ANTHROPIC_API_KEY")
        except Exception:
            key = None
    if not key:
        raise RuntimeError(
            "PDF parsing is unavailable — ANTHROPIC_API_KEY is not configured. "
            "Use Excel/CSV or Manual Entry instead.")
    return anthropic.Anthropic(api_key=key)


def parse_consensus_pdf(file_bytes: bytes, ticker: str, period: str) -> dict:
    """
    Parse a consensus PDF using Anthropic Claude to extract structured metrics.

    Returns: {ticker, period, source: "pdf", metrics: [{name, key, value, unit}]}
    """
    try:
        import base64

        client = _anthropic_client()

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
            model="claude-sonnet-4-6",
            max_tokens=8000,
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
        if response.stop_reason == "max_tokens":
            return {
                "ticker": ticker.upper(), "period": period, "source": "pdf",
                "metrics": [],
                "error": "The document is too large — the AI response was "
                         "truncated. Try a shorter excerpt or split the file.",
            }
        # Extract JSON from response (might have markdown code blocks)
        json_match = re.search(r'\[.*\]', text, re.DOTALL)
        if json_match:
            raw_metrics = json.loads(json_match.group())
        else:
            raw_metrics = json.loads(text)

        # Map to our internal keys — every field defensive (the model's JSON shape
        # varies); a non-numeric / NaN value is dropped, never fabricated.
        metrics = []
        for m in raw_metrics:
            if not isinstance(m, dict):
                continue
            mname = str(m.get("name", "")).strip()
            val = _finite_float(m.get("value"))
            if not mname or val is None:
                continue
            metrics.append({
                "name": mname,
                "key": _normalize_key(mname),
                "value": val,
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


def _clean_metric_list(raw_metrics) -> list[dict]:
    """Map/validate a raw metric list from the model — drop non-numeric / unnamed."""
    out = []
    for m in raw_metrics or []:
        if not isinstance(m, dict):
            continue
        mname = str(m.get("name", "")).strip()
        val = _finite_float(m.get("value"))
        if not mname or val is None:
            continue
        out.append({"name": mname, "key": _normalize_key(mname),
                    "value": val, "unit": m.get("unit", "")})
    return out


def detect_and_parse_pdf(file_bytes: bytes, filename: str = "") -> dict:
    """Read a single-firm research model and AUTO-DETECT the ticker, the firm and
    the firm's estimates for EACH forecast period (forward quarters + annual
    estimates), so the user doesn't retype them. Broker models are multi-period
    grids; each forecast column becomes its own (ticker, period, firm) record on
    save. Historical/actual columns are skipped — they're reported facts, not
    estimates. The ticker is validated against the bank universe and blanked if
    unknown (never a wrong-entity guess).

    Returns {detected_ticker, detected_firm, periods: [{period, metrics}], error?}.
    """
    try:
        import base64

        client = _anthropic_client()
        b64_pdf = base64.standard_b64encode(file_bytes).decode("utf-8")

        prompt = """This is an equity research MODEL from ONE sell-side firm covering ONE bank — a grid of metrics (rows) across many period columns (quarterly and annual).

Return ONLY a JSON object of this shape:
{ "ticker": "WTFC", "firm": "Brean Capital",
  "periods": [
    { "period": "2Q26", "metrics": [ {"name":"EPS","value":3.10,"unit":"$"},
                                     {"name":"Net Interest Margin","value":3.55,"unit":"%"} ] },
    { "period": "2026",  "metrics": [ {"name":"EPS","value":12.80,"unit":"$"} ] }
  ] }

- ticker: the official US stock ticker of the PRIMARY company. If unsure, use "".
- firm: the research firm/broker that PUBLISHED the model (e.g. "Brean Capital", "KBW"), from the cover/header/footer. NOT the covered bank's name. "" if unsure.
- periods: ONE entry per FORECAST / ESTIMATE period only — forward quarters (e.g. 2Q26, 3Q26, 4Q26, 1Q27) and annual estimates (e.g. 2026, 2027). These are the columns marked "E" (estimate). SKIP every historical/actual column (marked "A" or an already-reported quarter).
- period labels: quarters as "2Q26", annuals as "2026" — DROP any trailing E/A.
- metrics per period: every estimate in that column — EPS, NII, NIM, efficiency, ROAA, ROATCE, net income, provision, deposits, loans, TBV, dividends, charge-offs, yields, growth, etc.
- values numeric (no $/commas); unit one of: %, $, $M, $B, bps, x, or blank.

Return ONLY the JSON object, no other text."""

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=16000,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "document", "source": {
                        "type": "base64", "media_type": "application/pdf",
                        "data": b64_pdf}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )

        text = response.content[0].text.strip()
        if response.stop_reason == "max_tokens":
            return {"detected_ticker": "", "detected_firm": "", "periods": [],
                    "error": "The document is too large — the AI response was "
                             "truncated. Try a shorter excerpt or split the file."}

        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        data = json.loads(json_match.group() if json_match else text)

        periods = []
        seen = set()
        for p in data.get("periods", []) or []:
            if not isinstance(p, dict):
                continue
            per = normalize_period(str(p.get("period") or ""))
            if not per or per in seen:
                continue
            ms = _clean_metric_list(p.get("metrics"))
            if ms:
                periods.append({"period": per, "metrics": ms})
                seen.add(per)

        tkr = str(data.get("ticker") or "").strip().upper()
        if tkr and not _known_ticker(tkr):     # pre-fill only a verified ticker
            tkr = ""
        return {
            "detected_ticker": tkr,
            "detected_firm": str(data.get("firm") or "").strip(),
            "periods": periods,
        }

    except Exception as e:
        return {"detected_ticker": "", "detected_firm": "", "periods": [],
                "error": str(e)}


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
        import base64

        client = _anthropic_client()

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
- Use the OFFICIAL US ticker. If you cannot identify the exact ticker for a bank with confidence, OMIT that bank entirely — do not guess.
- Values should be numeric (no commas or dollar signs in the value field)
- Unit should be one of: %, $, $M, $B, bps, x, or blank

Return ONLY the JSON object, no other text."""

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=16000,
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
        if response.stop_reason == "max_tokens":
            return {
                "results": [], "total_banks": 0, "total_metrics": 0,
                "errors": ["The document is too large — the AI response was "
                           "truncated. Split it into fewer banks and retry."],
            }
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
            # Never save under a ticker the model invented — that's a wrong-entity
            # join. Validate against the bank universe and skip with a clear note.
            if not _known_ticker(ticker):
                errors.append(f"{ticker}: not a recognized bank ticker — skipped "
                              "(the AI may have guessed). Add it manually if real.")
                continue

            metrics = []
            for m in raw_metrics:
                if not isinstance(m, dict):
                    continue
                key = _normalize_key(m.get("name", ""))
                val = _finite_float(m.get("value"))
                if val is None:
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
            key = _normalize_key(str(col))
            if key is None:        # ignore junk columns (Notes, Date, Analyst…)
                continue
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

def _firm_slug(firm: str) -> str:
    """Filesystem-safe slug for a broker/firm name (lowercase alnum + dashes)."""
    s = re.sub(r"[^A-Za-z0-9]+", "-", (firm or "").strip()).strip("-").lower()
    return s or "unknown"


def normalize_period(s: str) -> str:
    """Canonicalize broker period labels so firms group together regardless of
    notation: quarters → "YYYYQn", years → "YYYY". Drops the E/A estimate-vs-
    actual marker. Falls back to the cleaned input when it doesn't match.
        2Q26 / 2Q26E / Q2'26 / 2026Q2 → 2026Q2 ;  2026E / FY26 / 26 → 2026
    """
    raw = (s or "").strip().upper().replace(" ", "").replace("'", "")
    raw = re.sub(r"[EA]$", "", raw)              # estimate / actual marker
    if not raw:
        return ""

    def _yr(y: str) -> int:
        n = int(y)
        return n + 2000 if n < 100 else n

    m = re.fullmatch(r"(\d{4})Q([1-4])", raw)            # 2026Q2
    if m:
        return f"{m.group(1)}Q{m.group(2)}"
    m = re.fullmatch(r"([1-4])Q(\d{2,4})", raw)          # 2Q26 / 2Q2026
    if m:
        return f"{_yr(m.group(2))}Q{m.group(1)}"
    m = re.fullmatch(r"Q([1-4])(\d{2,4})", raw)          # Q226 / Q22026
    if m:
        return f"{_yr(m.group(2))}Q{m.group(1)}"
    m = re.fullmatch(r"(?:FY)?(\d{2,4})", raw)           # FY26 / 2026 / 26
    if m:
        return str(_yr(m.group(1)))
    return (s or "").strip()


def save_consensus(data: dict) -> Path:
    """Save ONE firm's estimates to JSON (local + GCS). Returns the local path.

    A single sell-side note is NOT "consensus" — it is one firm's view, so each
    upload is stored per (ticker, period, FIRM) as {TICKER}_{period}__{firm}.json;
    compile_consensus() aggregates all firms for a (ticker, period) into the
    consensus. The firm defaults to the source label (manual / bulk) when none is
    given. Raises IOError when the durable (GCS) write fails — on Cloud Run the
    local copy is ephemeral, so silently continuing meant the upload could vanish
    on instance recycle while the user saw a success message."""
    ticker = data["ticker"].upper()
    period = (normalize_period(data["period"]) or data["period"]).replace("/", "-").replace(" ", "_")
    firm = (data.get("firm") or data.get("source") or "manual").strip()
    firm_slug = _firm_slug(firm)
    filename = f"{ticker}_{period}__{firm_slug}.json"

    payload = {**data, "ticker": ticker, "period": period, "firm": firm}
    if not save_json(CONSENSUS_PREFIX, filename, payload):
        raise IOError(
            f"Consensus for {ticker} {period} ({firm}) could not be written to "
            "durable storage (GCS). Not saved — please retry."
        )

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
        target = f"{ticker}_{period_clean}.json"     # exact, not substring
        for f in all_files:                          # ("2026Q1" must not hit "2026Q10")
            if f == target:
                return load_json(CONSENSUS_PREFIX, f)
    elif all_files:
        return load_json(CONSENSUS_PREFIX, all_files[0])
    return None


def list_consensus(ticker: str) -> list[dict]:
    """Consensus periods for a ticker, ONE entry per period (firms grouped).
    Each: {period, n_firms, firms, metric_count, source}. `source` is a firm
    summary kept for label back-compat."""
    ticker = ticker.upper()
    groups: dict = {}
    for filename in list_files(CONSENSUS_PREFIX):
        if not filename.startswith(f"{ticker}_"):
            continue
        data = load_json(CONSENSUS_PREFIX, filename)
        if not data:
            continue
        period = data.get("period", "")
        g = groups.setdefault(period, {"firms": set(), "keys": set()})
        g["firms"].add(data.get("firm") or data.get("source") or "?")
        for m in data.get("metrics", []):
            if m.get("key"):
                g["keys"].add(m["key"])

    out = []
    for period, g in groups.items():
        firms = sorted(g["firms"])
        out.append({
            "period": period,
            "n_firms": len(firms),
            "firms": firms,
            "source": f"{len(firms)} firm{'s' if len(firms) != 1 else ''}",
            "metric_count": len(g["keys"]),
        })
    out.sort(key=lambda x: x["period"], reverse=True)
    return out


def _to_canonical(value, stored_unit, key) -> float | None:
    """Convert a stored metric value to the metric's CANONICAL display magnitude
    (e.g. always $M for net income), honoring the unit it was entered/parsed in.
    This lets us average across firms that quote the same metric in different
    units ($B vs $M) without corrupting the mean. None if not finite."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    canon = METRIC_UNITS.get(key, (stored_unit or ""))
    entered = (stored_unit or "").strip() or canon
    raw = v * _UNIT_TO_RAW.get(entered, 1.0)
    return raw / _UNIT_TO_RAW.get(canon, 1.0)


def _firm_records(ticker: str, period: str) -> list[dict]:
    """All firms' stored estimates for one (ticker, period) — the firm-tagged
    files plus any legacy single file."""
    ticker = ticker.upper()
    period_clean = period.replace("/", "-").replace(" ", "_")
    prefix = f"{ticker}_{period_clean}__"
    legacy = f"{ticker}_{period_clean}.json"
    out = []
    for f in list_files(CONSENSUS_PREFIX):
        if f.startswith(prefix) or f == legacy:
            d = load_json(CONSENSUS_PREFIX, f)
            if d:
                out.append(d)
    return out


def compile_consensus(ticker: str, period: str) -> dict | None:
    """Aggregate every firm's estimates for (ticker, period) into the consensus:
    per metric the MEAN across firms, with the low/high range and firm count.
    Values are normalized to each metric's canonical unit before averaging.
    Returns None when no firm has estimates. Shape matches what
    compare_consensus_to_actual expects (metrics carry value = mean), with extra
    low/high/n_firms fields for display."""
    records = _firm_records(ticker, period)
    if not records:
        return None

    by_key: dict = {}
    for rec in records:
        for m in rec.get("metrics", []):
            key = m.get("key")
            if key is None:
                continue
            cv = _to_canonical(m.get("value"), m.get("unit"), key)
            if cv is None:
                continue
            slot = by_key.setdefault(key, {
                "name": METRIC_DISPLAY.get(key, m.get("name", key)),
                "unit": METRIC_UNITS.get(key, m.get("unit", "")),
                "values": [],
            })
            slot["values"].append(cv)

    metrics = []
    for key, slot in by_key.items():
        vals = slot["values"]
        metrics.append({
            "key": key, "name": slot["name"], "unit": slot["unit"],
            "value": sum(vals) / len(vals),
            "low": min(vals), "high": max(vals), "n_firms": len(vals),
        })

    firms = sorted({(r.get("firm") or r.get("source") or "?") for r in records})
    return {"ticker": ticker.upper(), "period": period, "n_firms": len(records),
            "firms": firms, "metrics": metrics}


def consensus_detail(ticker: str, period: str) -> dict | None:
    """Per-FIRM breakdown for (ticker, period) — the estimates browser. Each
    metric carries every firm's value (canonical units) plus mean/low/high/n, so
    the UI can show 'what each firm estimated'. None when no firm has estimates.
    Returns {ticker, period, firms: [...], metrics: [{key, name, unit, by_firm:
    {firm: value}, mean, low, high, n}]}."""
    records = _firm_records(ticker, period)
    if not records:
        return None
    firms = sorted({(r.get("firm") or r.get("source") or "?") for r in records})

    by_key: dict = {}
    for rec in records:
        firm = rec.get("firm") or rec.get("source") or "?"
        for m in rec.get("metrics", []):
            key = m.get("key")
            if key is None:
                continue
            cv = _to_canonical(m.get("value"), m.get("unit"), key)
            if cv is None:
                continue
            slot = by_key.setdefault(key, {
                "name": METRIC_DISPLAY.get(key, m.get("name", key)),
                "unit": METRIC_UNITS.get(key, m.get("unit", "")),
                "by_firm": {},
            })
            slot["by_firm"][firm] = cv

    metrics = []
    for key, slot in by_key.items():
        vals = list(slot["by_firm"].values())
        metrics.append({
            "key": key, "name": slot["name"], "unit": slot["unit"],
            "by_firm": slot["by_firm"],
            "mean": sum(vals) / len(vals), "low": min(vals), "high": max(vals),
            "n": len(vals),
        })
    return {"ticker": ticker.upper(), "period": period, "firms": firms,
            "metrics": metrics}


def list_all_consensus() -> dict[str, list[dict]]:
    """Consensus coverage for all tickers, GROUPED across firms.
    Returns {ticker: [{period, n_firms, firms, metric_count}]} (periods desc)."""
    groups: dict = {}
    for filename in list_files(CONSENSUS_PREFIX):
        data = load_json(CONSENSUS_PREFIX, filename)
        if not data:
            continue
        ticker = (data.get("ticker") or filename.split("_")[0]).upper()
        period = data.get("period", "")
        g = groups.setdefault((ticker, period), {"firms": set(), "keys": set()})
        g["firms"].add(data.get("firm") or data.get("source") or "?")
        for m in data.get("metrics", []):
            if m.get("key"):
                g["keys"].add(m["key"])

    result: dict = {}
    for (ticker, period), g in groups.items():
        result.setdefault(ticker, []).append({
            "period": period,
            "n_firms": len(g["firms"]),
            "firms": sorted(g["firms"]),
            "metric_count": len(g["keys"]),
        })
    for ticker in result:
        result[ticker].sort(key=lambda x: x["period"], reverse=True)
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
        # The metric KEY is the source of truth for the unit (NIM is always %,
        # provision always $M) — the per-metric unit string can be LLM noise.
        canon_unit = METRIC_UNITS.get(key) or (m.get("unit") or "")

        actual_key = CONSENSUS_ACTUAL_KEY.get(key, key) if key else None
        actual_raw = actual_metrics.get(actual_key) if actual_key else None

        def _na(disp_name):
            return {
                "metric_name": disp_name, "key": key,
                "consensus": consensus_val, "actual": None, "delta": None,
                "delta_pct": None, "beat_miss": "n/a", "unit": canon_unit,
            }

        if consensus_val is None or key is None:
            results.append(_na(name))
            continue
        if actual_raw is None:                       # no actual counterpart
            results.append(_na(METRIC_DISPLAY.get(key, name)))
            continue

        # Put consensus and actual on the SAME basis. Actuals are raw dollars for
        # $-amounts; consensus is entered in a display magnitude. Convert both to
        # the metric's canonical magnitude ($M/$B), honoring the entered unit so a
        # PDF that quotes $B instead of $M still lines up.
        entered_unit = (m.get("unit") or "").strip() or canon_unit
        cons_raw = consensus_val * _UNIT_TO_RAW.get(entered_unit, 1.0)
        disp_scale = _UNIT_TO_RAW.get(canon_unit, 1.0)
        consensus_disp = cons_raw / disp_scale
        actual_disp = actual_raw / disp_scale

        delta = actual_disp - consensus_disp
        delta_pct = (delta / abs(consensus_disp) * 100) if consensus_disp else 0

        # Higher = beat for most; for cost/risk metrics lower = beat. Provision and
        # noninterest expense are costs — a higher actual than expected is a miss.
        lower_is_better = key in ("efficiency_ratio", "npl_ratio", "nco_ratio",
                                  "nonix", "cost_of_deposits", "provision")

        if abs(delta_pct) <= 1.0:
            beat_miss = "inline"
        elif lower_is_better:
            beat_miss = "beat" if delta < 0 else "miss"
        else:
            beat_miss = "beat" if delta > 0 else "miss"

        results.append({
            "metric_name": METRIC_DISPLAY.get(key, name),
            "key": key,
            "consensus": consensus_disp,
            "actual": actual_disp,
            "delta": delta,
            "delta_pct": delta_pct,
            "beat_miss": beat_miss,
            "unit": canon_unit,
            # Carried through from compile_consensus for display (None for a
            # single-firm/manual consensus).
            "low": m.get("low"),
            "high": m.get("high"),
            "n_firms": m.get("n_firms"),
        })

    return results
