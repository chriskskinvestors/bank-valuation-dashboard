"""Corporate Structure sub-tab (Overview section) — SNL plan §12.

Parent → subsidiaries tree from the Fed NIC organizational hierarchy
(data/nic_client bulk files): the SNL "Corporate Structure" view. The page
starts from the bank subsidiary's RSSD (FDIC FED_RSSD), climbs to the top
regulated holder, then renders the whole organization downward with
ownership percentages and control flags. EX-21 (the company's own
subsidiaries exhibit) is the cross-check, one click away under
News & Filings ▸ Key Exhibits.
"""
from __future__ import annotations

import html as _h

import pandas as pd
import streamlit as st

from data.bank_mapping import get_name, get_fdic_cert
from ui.chrome import title_bar, table_export

_MAX_CLIMB = 4  # regulated chains are shallow; guard against loops


def _top_holder_rssd(bank_rssd: int) -> tuple[int, list[dict]]:
    """Climb parents from the bank to the top-of-chain entity.
    Returns (top_rssd, chain) where chain lists each parent climbed
    (immediate → top). Cycle-safe and depth-capped."""
    from data.nic_client import get_parent

    chain: list[dict] = []
    seen = {int(bank_rssd)}
    cur = int(bank_rssd)
    for _ in range(_MAX_CLIMB):
        parent = get_parent(cur)
        if not parent or not parent.get("rssd") or int(parent["rssd"]) in seen:
            break
        chain.append(parent)
        cur = int(parent["rssd"])
        seen.add(cur)
    return cur, chain


def _flatten(tree: dict, subject_rssd: int | None) -> list[dict]:
    """Tree → rows [{depth, name, type, location, ownership_pct,
    relationship, is_subject}] in display (pre-order) order."""
    rows: list[dict] = []

    def visit(node: dict, depth: int, pct, rel):
        e = node.get("entity") or {}
        rows.append({
            "depth": depth,
            "name": e.get("name"),
            "type": e.get("type"),
            "location": e.get("location"),
            "ownership_pct": pct,
            "relationship": rel,
            "is_subject": subject_rssd is not None and e.get("rssd") == subject_rssd,
        })
        for c in node.get("children", []):
            visit(c, depth + 1, c.get("ownership_pct"), c.get("relationship"))

    visit(tree, 0, None, None)
    return rows


def render_corporate_structure(ticker: str):
    from data.fdic_client import get_rssd_for_cert
    from data.nic_client import get_org_hierarchy

    title_bar(f"{get_name(ticker) or ticker} ({ticker})", "Corporate Structure")

    cert = get_fdic_cert(ticker)
    rssd = get_rssd_for_cert(cert) if cert else None
    if not rssd:
        st.info("No Fed NIC hierarchy is available for this company (no FDIC "
                "bank-subsidiary mapping to key the lookup). The company's own "
                "EX-21 subsidiaries exhibit under News & Filings ▸ Key Exhibits "
                "is the alternative source.")
        return

    with st.spinner("Loading the Fed NIC organizational hierarchy "
                    "(first load parses the bulk structure files)…"):
        top_rssd, chain = _top_holder_rssd(rssd)
        tree = get_org_hierarchy(top_rssd)
    if not tree:
        st.info("The Fed NIC bulk hierarchy is unavailable right now — "
                "try again shortly.")
        return

    rows = _flatten(tree, subject_rssd=rssd)

    body = ""
    for r in rows:
        pad = r["depth"] * 22
        name = _h.escape(r["name"] or "(unnamed entity)")
        if r["depth"] == 0:
            name = f"<strong>{name}</strong>"
        elif r["is_subject"]:
            name = f'<span style="font-weight:600;">{name}</span>'
        pct = f'{r["ownership_pct"]:.0f}%' if r["ownership_pct"] is not None else "—"
        body += ("<tr>"
                 f'<td style="text-align:left;padding-left:{pad + 8}px;">{name}</td>'
                 f'<td style="text-align:left;">{_h.escape(r["type"] or "n/a")}</td>'
                 f'<td style="text-align:left;">{_h.escape(r["location"] or "n/a")}</td>'
                 f'<td style="text-align:right;">{pct}</td>'
                 f'<td style="text-align:left;">{_h.escape(r["relationship"] or "—")}</td>'
                 "</tr>")
    st.markdown(
        '<div class="ksk-grid"><table><thead><tr>'
        '<th style="text-align:left;">Entity</th>'
        '<th style="text-align:left;">Type</th>'
        '<th style="text-align:left;">Location</th>'
        '<th style="text-align:right;">Ownership</th>'
        '<th style="text-align:left;">Control</th>'
        f"</tr></thead><tbody>{body}</tbody></table></div>",
        unsafe_allow_html=True)

    notes = [f"Source: Federal Reserve NIC organizational hierarchy, "
             f"as of {tree.get('as_of')}. Ownership is NIC PCT_EQUITY as "
             "reported to the Fed; regulated entities only — non-regulated "
             "subsidiaries appear in the company's EX-21 exhibit "
             "(News & Filings ▸ Key Exhibits)."]
    if chain and len(chain) > 1:
        path = " → ".join(_h.escape(p.get("name") or "?") for p in reversed(chain))
        notes.insert(0, f"Ownership chain above the bank: {path}.")
    st.caption(" ".join(notes))

    df = pd.DataFrame([{
        "Entity": ("  " * r["depth"]) + (r["name"] or ""),
        "Type": r["type"], "Location": r["location"],
        "Ownership %": r["ownership_pct"], "Control": r["relationship"],
    } for r in rows])
    table_export(df, f"{ticker}_corporate_structure", key=f"exp_struct_{ticker}")
