"""
Reusable traceable metric-card grid.

Renders a dense, NATIVE grid of metric cards (no iframe) where each value
carries its provenance: hover a card for the calculation + sources, and click
the ↗ to open the exact filing (SEC 10-K/10-Q index or FFIEC Call Report
facsimile). Rendering natively means the grid flows with its content — no
fixed-height gaps. The card CSS (.mc-grid / .mc) lives in ui/styles.py.

The low-level provenance helpers are reused from ui.financial_highlights.
"""
from __future__ import annotations
import html as _html

import streamlit as st

from ui.financial_highlights import (
    _fdic_doc, _sec_doc, _sec_prov_map, _disp_date, _thou, _num,
)


def make_calc(metric, value, *, entity, source, asof, unit, ref, definition,
              terms, op=None, reported=False, link=None):
    """Build a provenance payload for a metric value."""
    return {
        "metric": metric, "value": value, "entity": entity, "source": source,
        "asof": asof, "unit": unit, "ref": ref, "definition": definition,
        "terms": terms, "op": op, "reported": reported, "link": link,
    }


def fdic_calc(metric, field, fdic_rec, cert, *, unit, definition, entity,
              value, reported=True, terms=None, op=None):
    """A value reported (or computed) from the latest FDIC Call Report, linked
    to that quarter's FFIEC facsimile."""
    doc = _fdic_doc(cert, fdic_rec.get("REPDTE")) if cert else None
    if terms is None:
        raw = _num(fdic_rec.get(field))
        shown = (_thou(raw) + (" ($000)" if unit.startswith("$") else "")) if unit.startswith("$") \
            else value
        terms = [{"label": metric + (" (as reported)" if reported else ""),
                  "val": shown, "doc": doc}]
    else:
        for t in terms:
            t.setdefault("doc", doc)
    return make_calc(metric, value, entity=entity, source="FDIC Call Report",
                     asof=_disp_date(fdic_rec.get("REPDTE")), unit=unit,
                     ref=(f"FDIC field {field}" if reported else "Computed from Call Report"),
                     definition=definition, terms=terms, op=op, reported=reported,
                     link=(doc or {}).get("url"))


def sec_doc_for(cik, facts, concept, *, instant, span="both", ns="us-gaap"):
    """Doc link for the most-recent reported value of a concept."""
    prov = _sec_prov_map(facts, concept, instant=instant, span=span, ns=ns)
    if not prov:
        return None
    latest = max(prov.keys())
    return _sec_doc(cik, prov[latest])


def fmp_calc(metric, value, *, entity, unit, definition, terms=None):
    """A value sourced from FMP's live market data (price/quote). No filing —
    labelled as a live quote so it's clearly distinguished from filing data."""
    return make_calc(metric, value, entity=entity, source="FMP (live market data)",
                     asof="latest quote", unit=unit, ref="FMP quote",
                     definition=definition,
                     terms=terms or [{"label": metric, "val": value}],
                     op=None, reported=True, link=None)


def _calc_tooltip(c) -> str:
    """Plain-text hover tooltip summarising a calc payload (newlines render as
    line breaks in the native title tooltip)."""
    if not c:
        return ""
    lines = []
    metric, val = c.get("metric"), c.get("value")
    if metric:
        lines.append(f"{metric} = {val}" if val else str(metric))
    meta = " · ".join(x for x in (c.get("source"), c.get("asof"), c.get("ref")) if x)
    if meta:
        lines.append(meta)
    if c.get("definition"):
        lines.append(c["definition"])
    if c.get("op"):
        lines.append(f"Formula:  {c['op']}")
    for t in (c.get("terms") or []):
        sub = f"   ({t['sub']})" if t.get("sub") else ""
        lines.append(f"• {t.get('label', '')}: {t.get('val', '')}{sub}")
    if c.get("reported") and c.get("source"):
        lines.append(f"Reported directly by {c['source']}.")
    if c.get("link"):
        lines.append("↗ click the arrow for the source document")
    return "\n".join(str(x) for x in lines if x)


def render_traceable_cards(cards, key=None, columns=7, height=None):
    """Render a native grid of metric cards.

    cards: list of {label, value, accent?, calc?}. Each card shows the label and
    value; cards with a `calc` get a hover tooltip (the calculation + sources)
    and an ↗ link to the source filing. Rendered as native HTML (no iframe), so
    the grid sizes to its content — no reserved gaps. `height` is accepted for
    backward compatibility and ignored.
    """
    cells = []
    for c in cards:
        label = c.get("label", "")
        value = c.get("value", "—")
        accent = c.get("accent") or "inherit"
        calc = c.get("calc")
        tip = _calc_tooltip(calc)
        link = (calc or {}).get("link")
        src = (f'<a class="mc-src" href="{_html.escape(str(link))}" target="_blank" '
               f'title="View source document">↗</a>') if link else ""
        ttl = f' title="{_html.escape(tip, quote=True)}"' if tip else ""
        cells.append(
            f'<div class="mc"{ttl}><div class="mc-l">{label}{src}</div>'
            f'<div class="mc-v" style="color:{accent};">{value}</div></div>')
    cols = max(1, int(columns or 1))
    grid = (f'<div class="mc-grid" style="grid-template-columns:repeat({cols},minmax(0,1fr));">'
            + "".join(cells) + "</div>")
    st.markdown(grid, unsafe_allow_html=True)
