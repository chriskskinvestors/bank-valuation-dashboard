"""
Data Quality panel for Company Analysis.

Shows:
1. Overall quality score (errors/warnings count)
2. Findings table with severity + message
3. Source traceability table — every metric with its primary source
4. Staleness indicators
"""

import streamlit as st
import pandas as pd
from datetime import datetime

from ui.chrome import lazy_tabs, table_export

from data.bank_mapping import get_cik, get_fdic_cert, get_name

# SEC provenance unit → ui.export FORMATS key (row format for the Value cell).
_SEC_UNIT_FMT = {"USD": "usd", "USD/shares": "usd2", "shares": "int", "pure": "num"}
from data.fdic_client import fetch_financials, build_fdic_provenance
from data import sec_client
from data.validation import validate_bank_metrics, summary as _validation_summary, Finding
from data.provenance import Source


def render_data_quality(ticker: str):
    """Render the Data Quality panel for a bank."""

    st.subheader("Data Quality & Provenance")
    st.caption(
        "Traceability for every number shown on this bank's pages. Every cell "
        "below links back to its primary SEC filing or FDIC Call Report. "
        "Validation checks flag anomalies before you trust a number."
    )

    cert = get_fdic_cert(ticker)
    cik = get_cik(ticker)

    if not cik and not cert:
        st.error("No SEC CIK or FDIC Cert mapping for this ticker.")
        return

    # ── Fetch data with provenance ─────────────────────────────────────
    with st.spinner("Fetching data and validating..."):
        sec_with_prov = {}
        if cik:
            try:
                sec_with_prov = sec_client.get_fundamentals_with_provenance(cik)
            except Exception as e:
                st.error(f"SEC fetch error: {e}")
                sec_with_prov = {}

        fdic_data = {}
        fdic_repdte = None
        if cert:
            try:
                df = fetch_financials(cert, limit=1)
                if not df.empty:
                    fdic_data = df.iloc[0].to_dict()
                    fdic_repdte = fdic_data.get("REPDTE")
            except Exception as e:
                st.error(f"FDIC fetch error: {e}")

        # Get flat metrics for validation
        from data.cache import get as cache_get
        metrics_list = cache_get("watchlist_metrics_last") or []
        bank_metrics = next((m for m in metrics_list if m.get("ticker") == ticker), {})

    # FDIC report date as ISO text (export filenames + provenance), None when
    # there is no FDIC row.
    as_of = None
    if fdic_repdte is not None:
        as_of = (fdic_repdte.strftime("%Y-%m-%d") if hasattr(fdic_repdte, "strftime")
                 else str(fdic_repdte)[:10])
    name = get_name(ticker) or ticker

    # ── Validation findings ────────────────────────────────────────────
    # Convert provenance dict to flat scalar dict for validation
    sec_flat = {k: v.get("value") if isinstance(v, dict) else v for k, v in sec_with_prov.items()}
    findings = validate_bank_metrics(bank_metrics or sec_flat, sec_data=sec_flat, fdic_data=fdic_data)
    summary = _validation_summary(findings)

    # Overall quality banner
    if summary["errors"] == 0 and summary["warnings"] == 0:
        st.markdown(
            '<div style="background: rgba(5, 150, 105, 0.08); color: #065f46; '
            'border: 1px solid rgba(5, 150, 105, 0.22); border-left: 3px solid #059669; '
            'padding: 12px 16px; border-radius:0; font-size: 0.88rem;">'
            "<strong>All checks passed</strong> — no range violations or "
            "reconciliation issues detected."
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        color = "#dc2626" if summary["errors"] > 0 else "#d97706"
        bg = "rgba(220, 38, 38, 0.06)" if summary["errors"] > 0 else "rgba(217, 119, 6, 0.06)"
        border = "rgba(220, 38, 38, 0.22)" if summary["errors"] > 0 else "rgba(217, 119, 6, 0.22)"
        text_color = "#991b1b" if summary["errors"] > 0 else "#92400e"
        st.markdown(
            f'<div style="background: {bg}; color: {text_color}; '
            f'border: 1px solid {border}; border-left: 3px solid {color}; '
            f'padding: 12px 16px; border-radius:0; font-size: 0.88rem;">'
            f"<strong>{summary['errors']} errors, {summary['warnings']} warnings</strong> — "
            f"review the Findings table below."
            f"</div>",
            unsafe_allow_html=True,
        )

    st.markdown("---")

    _dq_tabs = [f"Validation Findings ({len(findings)})", "Source Traceability"]
    _dq_sel = lazy_tabs(_dq_tabs, key="dataquality")

    # ── Findings tab ───────────────────────────────────────────────────
    if _dq_sel == _dq_tabs[0]:
        if not findings:
            st.info("No issues detected. Every value is within expected ranges and sources check out.")
        else:
            rows = []
            for f in findings:
                rows.append({
                    "Severity": f.severity.title(),
                    "Field": f.field,
                    "Issue": f.message,
                    "Value": f"{f.value:.4f}" if isinstance(f.value, (int, float)) else (str(f.value) if f.value else "—"),
                })
            df = pd.DataFrame(rows)

            def _color_row(row):
                s = row["Severity"].lower()
                if s == "error":
                    return ["background-color: rgba(220,38,38,0.10); color:#991b1b;"] * len(row)
                if s == "warning":
                    return ["background-color: rgba(217,119,6,0.08); color:#92400e;"] * len(row)
                return [""] * len(row)

            styled = df.style.apply(_color_row, axis=1).set_properties(
                **{"font-size": "0.85rem", "padding": "4px 8px"}
            )
            st.dataframe(styled, use_container_width=True, hide_index=True,
                          height=min(500, 40 + 35 * len(df)))
            # Export the RAW finding value (a number where the check had one;
            # its unit is named in the Issue text), never the "0.1234" string.
            table_export(
                pd.DataFrame([{"Severity": f.severity.title(), "Field": f.field,
                               "Issue": f.message,
                               "Value": (f.value if isinstance(f.value, (int, float))
                                         and not isinstance(f.value, bool)
                                         else (str(f.value) if f.value else None)),
                               "Data source": f.source or None}
                              for f in findings]),
                f"data_quality_findings_{ticker}_{as_of or datetime.now().date()}",
                key=f"exp_dq_findings_{ticker}", sheet="Validation Findings",
                formats={"Value": "num"},
                provenance={
                    "Page": "Company Analysis · Data Quality — Validation Findings",
                    "Ticker": ticker, "Company": name,
                    "FDIC cert": cert, "SEC CIK": cik,
                    "Source": "data/validation.py range bands, cross-source "
                              "reconciliation and staleness checks over SEC "
                              "companyfacts (holding company) and the FDIC Call "
                              "Report (bank subsidiary)",
                    "Report date": as_of,
                    "Checks": f"{summary['errors']} errors, {summary['warnings']} warnings",
                    "Value units": "Each Value is in the unit named in its Issue text "
                                   "(percent units for ratios, whole dollars for SEC "
                                   "amounts, share counts for shares).",
                })

        st.markdown("---")
        with st.expander("What each check validates"):
            st.markdown("""
            **Range checks** — every metric has a sanity window (e.g., CET1 between 3-25%,
            share count between 1M-20B). Values outside the window are flagged.

            **Cross-source reconciliation** — SEC HoldCo vs FDIC sub-bank equity/NI must
            be consistent (HoldCo ≥ sub-bank). If the sub-bank is reporting more equity
            than the HoldCo, something is wrong with the CIK mapping or filings.

            **Staleness** — XBRL concepts can go dormant (e.g., Citi stopped reporting
            `CommonStockSharesOutstanding` in 2010). We detect when the latest available
            filing is older than reasonable and fall back to alternative concepts.

            **Internal consistency** — loans-to-deposits between 20-180%, and similar
            logical bounds.
            """)

    # ── Provenance / Source traceability tab ────────────────────────────
    elif _dq_sel == _dq_tabs[1]:
        st.markdown("##### SEC HoldCo Sources")
        if sec_with_prov:
            sec_rows, sec_raw, sec_row_fmt = [], [], {}
            for short_name, entry in sec_with_prov.items():
                if not isinstance(entry, dict) or entry.get("value") is None:
                    continue
                src = entry.get("source")
                if not isinstance(src, Source):
                    continue
                val = entry["value"]
                fmt_val = _fmt_for_display(val, src.unit)
                age_days = src.age_days()
                age_str = f"{age_days}d" if age_days is not None else "—"
                sec_rows.append({
                    "Metric": short_name,
                    "Value": fmt_val,
                    "XBRL Concept": src.concept,
                    "As Of": src.as_of or "—",
                    "Age": age_str,
                    "Form": src.form or "—",
                    "Unit": src.unit,
                    "Notes": src.notes or "—",
                })
                # Export row: raw value, formatted per the concept's XBRL unit.
                sec_raw.append({
                    "Metric": short_name, "Value": val, "XBRL Concept": src.concept,
                    "As Of": src.as_of or None, "Age (days)": age_days,
                    "Form": src.form or None, "Unit": src.unit,
                    "Notes": src.notes or None,
                })
                if src.unit in _SEC_UNIT_FMT:
                    sec_row_fmt[short_name] = _SEC_UNIT_FMT[src.unit]

            if sec_rows:
                sec_df = pd.DataFrame(sec_rows)

                def _highlight_stale(row):
                    age = row.get("Age", "")
                    try:
                        days = int(age.replace("d", ""))
                        if days > 180:
                            return ["background-color: rgba(220,38,38,0.06);"] * len(row)
                        if days > 120:
                            return ["background-color: rgba(217,119,6,0.06);"] * len(row)
                    except Exception:
                        pass
                    return [""] * len(row)

                styled = sec_df.style.apply(_highlight_stale, axis=1).set_properties(
                    **{"font-size": "0.82rem", "padding": "4px 8px"}
                )
                st.dataframe(styled, use_container_width=True, hide_index=True,
                              height=min(500, 50 + 32 * len(sec_df)))
                sec_latest = max((r["As Of"] for r in sec_raw if r["As Of"]), default=None)
                table_export(
                    pd.DataFrame(sec_raw),
                    f"data_quality_sec_sources_{ticker}_{sec_latest or datetime.now().date()}",
                    key=f"exp_dq_sec_{ticker}", sheet="SEC HoldCo Sources",
                    formats={"As Of": "date", "Age (days)": "int"},
                    row_formats=sec_row_fmt,
                    provenance={
                        "Page": "Company Analysis · Data Quality — Source Traceability "
                                "(SEC HoldCo Sources)",
                        "Ticker": ticker, "Company": name, "SEC CIK": cik,
                        "Source": "SEC companyfacts (holding company)",
                        "Data as of": sec_latest,
                        "Value units": "Per row, in the Unit column: USD = whole "
                                       "dollars, USD/shares = dollars per share, "
                                       "shares = share count, pure = ratio.",
                    })
            else:
                st.caption("No SEC data available.")
        else:
            st.caption("No SEC data available.")

        st.markdown("---")

        st.markdown("##### FDIC Call Report Source")
        if fdic_data and fdic_repdte:
            from data.fdic_client import FDIC_FINANCIALS_URL
            st.markdown(f"""
            - **Institution**: FDIC Cert `{cert}` ({fdic_data.get('REPNM','—')})
            - **Report Date**: {as_of}
            - **Source**: FDIC BankFind Call Report API
            - **Endpoint**: `{FDIC_FINANCIALS_URL}`
            """)

            # Key fields table
            key_fields = [
                ("ASSET", "Total Assets", "$thousands"),
                ("DEP", "Total Deposits", "$thousands"),
                ("LNLSNET", "Net Loans", "$thousands"),
                ("EQTOT", "Total Equity", "$thousands"),
                ("NETINC", "Net Income (YTD)", "$thousands"),
                ("NIMY", "NIM (annualized)", "%"),
                ("ROA", "ROA (annualized)", "%"),
                ("IDT1CER", "CET1 Ratio", "%"),
                ("NCLNLSR", "NPL Ratio", "%"),
            ]
            rows, raw_rows, raw_row_fmt = [], [], {}
            for field_name, label, unit in key_fields:
                v = fdic_data.get(field_name)
                if v is None or (isinstance(v, float) and v != v):
                    # Not reported: say so — never skip silently or show $nan.
                    xlabel = f"{label} ($K)" if unit.startswith("$") else f"{label} (%)"
                    raw_rows.append({"Label": xlabel, "FDIC Field": field_name,
                                     "Value": None, "Unit": unit, "As Of": as_of})
                    rows.append({"FDIC Field": field_name, "Label": label,
                                 "Value": "n/a", "Unit": unit, "As Of": as_of})
                    continue
                # Export row: the FDIC value as reported — $thousands stay
                # unscaled under a "($K)" label; ratios are percent units.
                xlabel = f"{label} ($K)" if unit.startswith("$") else f"{label} (%)"
                raw_rows.append({"Label": xlabel, "FDIC Field": field_name, "Value": v,
                                 "Unit": unit, "As Of": as_of})
                raw_row_fmt[xlabel] = "usd_k" if unit.startswith("$") else "pct"
                if unit.startswith("$"):
                    # FDIC dollar fields are reported in $thousands. Scale to
                    # actual dollars, then format adaptively so small banks and
                    # negative net income render correctly (the old < 1000
                    # threshold mislabeled any sub-$1M line item as raw dollars).
                    dollars = v * 1000
                    a = abs(dollars)
                    if a >= 1e9:
                        display = f"${dollars/1e9:.2f}B"
                    elif a >= 1e6:
                        display = f"${dollars/1e6:.1f}M"
                    elif a >= 1e3:
                        display = f"${dollars/1e3:.0f}K"
                    else:
                        display = f"${dollars:,.0f}"
                else:
                    display = f"{v:.2f}%"
                rows.append({
                    "FDIC Field": field_name,
                    "Label": label,
                    "Value": display,
                    "Unit": unit,
                    "As Of": as_of,
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            table_export(
                pd.DataFrame(raw_rows, columns=["Label", "FDIC Field", "Value", "Unit", "As Of"]),
                f"data_quality_fdic_fields_{ticker}_{as_of}",
                key=f"exp_dq_fdic_{ticker}", sheet="FDIC Call Report Fields",
                formats={"As Of": "date"}, row_formats=raw_row_fmt,
                provenance={
                    "Page": "Company Analysis · Data Quality — Source Traceability "
                            "(FDIC Call Report Source)",
                    "Ticker": ticker, "Company": name, "FDIC cert": cert,
                    "Institution": fdic_data.get("REPNM"),
                    "Source": "FDIC Call Report (bank subsidiary)",
                    "Endpoint": FDIC_FINANCIALS_URL,
                    "Report date": as_of,
                })
        else:
            st.caption("No FDIC data available.")

        st.markdown("---")

        st.markdown("##### FFIEC Call Report Ladder (NIM repricing)")
        st.caption(
            "Drives this bank's phased asset-repricing pace in the Rate "
            "Sensitivity model. When absent, NIM falls back to the generic "
            "~29%/yr repricing assumption."
        )
        _render_ffiec_status(cert, ticker)


def _render_ffiec_status(cert, ticker=None):
    """Token health + this bank's stored securities/loan repricing ladder."""
    from data.ffiec_client import health_check, is_configured

    if not is_configured():
        st.caption("FFIEC not configured in this environment (no token mounted).")
        return

    hc = health_check()
    days = hc.get("days_until_expiry")
    if not hc.get("ok"):
        st.warning(
            f"FFIEC token problem: {hc.get('reason', 'unknown')}. "
            "Quarterly ladder refresh will fail until it's fixed."
        )
    elif days is not None and days < 14:
        st.error(
            f"FFIEC JWT expires in {days:.0f} days — rotate it now "
            "(`gcloud secrets versions add ffiec-jwt-token`) or the next "
            "quarterly refresh will fail."
        )
    elif days is not None and days < 30:
        st.warning(
            f"FFIEC JWT expires in {days:.0f} days — plan to rotate it soon "
            "via Secret Manager (`ffiec-jwt-token`)."
        )
    elif days is not None:
        st.caption(f"FFIEC token healthy — {days:.0f} days until expiry.")
    else:
        st.caption("FFIEC token configured.")

    ladder = None
    if cert:
        try:
            from data.call_report_store import get_latest_ladder
            ladder = get_latest_ladder(int(cert))
        except Exception:
            ladder = None

    if not ladder:
        st.caption(
            "No stored ladder for this bank — NIM uses the generic "
            "~29%/yr repricing assumption."
        )
        return

    fls = ladder.get("floating_loan_share")
    dur_raw = ladder.get("weighted_avg_duration_years")
    rows = [
        {"Field": "Reporting period", "Value": ladder.get("reporting_period", "—")},
        {"Field": "Securities duration (wtd-avg)",
         "Value": f"{dur_raw:.2f} yrs" if dur_raw is not None else "— (not reported)"},
        {"Field": "Floating-loan share (RC-C Memo 2)",
         "Value": f"{fls * 100:.1f}%" if fls is not None else "— (not reported)"},
        {"Field": "Source", "Value": ladder.get("source", "ffiec")},
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    # Export: raw duration (years) and the share as percent units; an
    # unreported value is n/a, not the 0.00 the display falls back to.
    period = ladder.get("reporting_period")
    table_export(
        pd.DataFrame([
            {"Field": "Reporting period", "Value": period},
            {"Field": "Securities duration (wtd-avg, years)", "Value": dur_raw},
            {"Field": "Floating-loan share (RC-C Memo 2, %)",
             "Value": fls * 100 if fls is not None else None},
            {"Field": "Source", "Value": ladder.get("source", "ffiec")},
        ]),
        f"ffiec_ladder_{ticker or cert}_{period or 'latest'}",
        key=f"exp_dq_ffiec_{ticker or cert}", sheet="FFIEC Ladder",
        row_formats={"Securities duration (wtd-avg, years)": "num",
                     "Floating-loan share (RC-C Memo 2, %)": "pct"},
        provenance={
            "Page": "Company Analysis · Data Quality — FFIEC Call Report Ladder",
            "Ticker": ticker, "FDIC cert": cert,
            "Source": "FFIEC Call Report (bank subsidiary) — stored securities "
                      "maturity/repricing ladder and RC-C Memo 2 floating-rate loan share",
            "Report date": period,
        })


def _fmt_for_display(val, unit: str) -> str:
    """Format a raw value for display based on unit hint."""
    if val is None:
        return "—"
    try:
        v = float(val)
    except (TypeError, ValueError):
        return str(val)
    if unit == "USD":
        if abs(v) >= 1e12:
            return f"${v/1e12:.2f}T"
        if abs(v) >= 1e9:
            return f"${v/1e9:.2f}B"
        if abs(v) >= 1e6:
            return f"${v/1e6:.2f}M"
        return f"${v:,.2f}"
    if unit == "USD/shares":
        return f"${v:.2f}"
    if unit == "shares":
        return f"{v:,.0f}"
    if unit == "pure":
        return f"{v:,.4f}"
    return f"{v:,.2f}"
