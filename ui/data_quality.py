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

from data.bank_mapping import get_cik, get_fdic_cert, get_name
from data.fdic_client import fetch_financials, build_fdic_provenance
from data import sec_client
from data.validation import validate_bank_metrics, summary as _validation_summary, Finding
from data.provenance import Source


def render_data_quality(ticker: str):
    """Render the Data Quality panel for a bank."""

    st.subheader("🔍 Data Quality & Provenance")
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
            'padding: 12px 16px; border-radius: 6px; font-size: 0.88rem;">'
            "✅ <strong>All checks passed</strong> — no range violations or "
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
            f'padding: 12px 16px; border-radius: 6px; font-size: 0.88rem;">'
            f"⚠️ <strong>{summary['errors']} errors, {summary['warnings']} warnings</strong> — "
            f"review the Findings table below."
            f"</div>",
            unsafe_allow_html=True,
        )

    st.markdown("---")

    tab_findings, tab_provenance = st.tabs([
        f"⚠️ Validation Findings ({len(findings)})",
        "📋 Source Traceability",
    ])

    # ── Findings tab ───────────────────────────────────────────────────
    with tab_findings:
        if not findings:
            st.info("No issues detected. Every value is within expected ranges and sources check out.")
        else:
            rows = []
            for f in findings:
                icon = {"error": "🔴", "warning": "🟡", "info": "🔵"}.get(f.severity, "—")
                rows.append({
                    "": icon,
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
    with tab_provenance:
        st.markdown("##### SEC HoldCo Sources")
        if sec_with_prov:
            sec_rows = []
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
            else:
                st.caption("No SEC data available.")
        else:
            st.caption("No SEC data available.")

        st.markdown("---")

        st.markdown("##### FDIC Call Report Source")
        if fdic_data and fdic_repdte:
            from data.fdic_client import FDIC_FINANCIALS_URL
            as_of = fdic_repdte.strftime("%Y-%m-%d") if hasattr(fdic_repdte, "strftime") else str(fdic_repdte)[:10]
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
            rows = []
            for field_name, label, unit in key_fields:
                v = fdic_data.get(field_name)
                if v is None:
                    continue
                if unit.startswith("$"):
                    display = f"${v:,.0f}" if abs(v) < 1000 else f"${v*1000/1e9:.2f}B"
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
        else:
            st.caption("No FDIC data available.")


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
