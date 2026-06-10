"""
Reusable "click any number to see its calculation + source document" component.

This generalises the Financial Highlights drill-down so any surface (Overview
key-stats, Valuation, Credit, Capital, Deposits) can render a grid of metric
cards where each value is traceable: clicking it opens a popup with the
formula, the underlying source numbers, the as-of date, and a link to the
exact filing (SEC 10-K/10-Q index or FFIEC Call Report facsimile).

The low-level provenance helpers live in ui.financial_highlights and are
reused here (no duplication): _fdic_doc, _sec_doc, _sec_prov_map,
_shares_prov_map, _sec_map, _disp_date, _thou, _num.
"""
from __future__ import annotations
import json
from datetime import datetime

import streamlit as st
import streamlit.components.v1 as components

from ui.financial_highlights import (
    _fdic_doc, _sec_doc, _sec_prov_map, _sec_map, _disp_date, _thou, _num,
)


def make_calc(metric, value, *, entity, source, asof, unit, ref, definition,
              terms, op=None, reported=False, link=None):
    """Build a drill-down payload (same shape the modal renders)."""
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


# Short card grids can't host a position:fixed modal (a Streamlit components
# iframe has a fixed height, which would clip the popup). So the drill-down
# renders into an inline panel BELOW the grid that updates on click — no
# clipping, and the reserved space reads as a purposeful "details" area.
_CARD_CSS = """
* { box-sizing:border-box; }
body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  color:#1e293b; background:transparent; }
.grid { display:grid; gap:8px; }
.card { background:rgba(148,163,184,0.06); border:1px solid rgba(148,163,184,0.18);
  border-radius:10px; padding:9px 13px; }
.card.click { cursor:pointer; }
.card.click:hover { background:rgba(37,99,235,0.08); border-color:rgba(37,99,235,0.35); }
.card.sel { background:rgba(37,99,235,0.10); border-color:rgba(37,99,235,0.5); }
.card .lbl { font-size:0.62rem; color:#64748b; font-weight:600; text-transform:uppercase;
  letter-spacing:0.03em; }
.card .lbl .arr { color:#2563eb; }
.card .val { font-size:1.1rem; font-weight:700; line-height:1.35; }
#panel { margin-top:12px; border:1px solid rgba(148,163,184,0.25); border-radius:10px;
  background:#fff; overflow:hidden; }
.hint { padding:16px; font-size:12.5px; color:#94a3b8; text-align:center; }
.hd { padding:11px 16px; border-bottom:1px solid rgba(148,163,184,0.18); }
.ttl { display:flex; justify-content:space-between; align-items:baseline;
  font-size:15px; font-weight:700; color:#0f172a; }
.ent { font-size:12px; color:#475569; margin-top:2px; }
.meta { font-size:11.5px; color:#64748b; margin-top:2px; }
.def { font-size:12px; color:#475569; padding:9px 16px; line-height:1.5;
  border-bottom:1px solid rgba(148,163,184,0.12); }
.def b { color:#2563eb; font-weight:600; letter-spacing:0.03em; font-size:10.5px; }
.calc { padding:10px 16px 14px; }
.term { display:flex; justify-content:space-between; padding:4px 0 4px 14px;
  font-size:12.5px; color:#334155; border-top:1px dashed rgba(148,163,184,0.25); }
.term .tv { color:#1d4ed8; font-variant-numeric:tabular-nums; }
.sub { font-size:11px; color:#94a3b8; padding:0 0 2px 14px; }
.doc { font-size:11px; color:#94a3b8; padding:0 0 5px 14px; }
.doc a { color:#2563eb; text-decoration:none; }
.doc a:hover { text-decoration:underline; }
.op { font-size:12px; color:#64748b; text-align:center; padding:8px 0 2px; font-style:italic; }
.rep { font-size:11.5px; color:#475569; padding:8px 0 2px; }
.src { display:inline-block; margin-top:8px; font-size:12px; color:#2563eb; text-decoration:none; }
"""

_PANEL_JS = """
const panel=document.getElementById("panel");
function esc(s){return (s==null?"":String(s)).replace(/&/g,"&amp;").replace(/</g,"&lt;");}
function showCalc(c,el){
  document.querySelectorAll(".card.sel").forEach(x=>x.classList.remove("sel"));
  if(el) el.classList.add("sel");
  let terms=(c.terms||[]).map(t=>
    `<div class="term"><span>${esc(t.label)}</span><span class="tv">${esc(t.val)}</span></div>`
    +(t.sub?`<div class="sub">${esc(t.sub)}</div>`:"")
    +(t.doc?`<div class="doc"><i>Source document available</i> — `
       +`<a href="${esc(t.doc.url)}" target="_blank">view ${esc(t.doc.label)} →</a></div>`:"")
  ).join("");
  let opline=c.op?`<div class="op">${esc(c.op)}</div>`:"";
  let rep=c.reported?`<div class="rep">Reported directly by ${esc(c.source)}.</div>`:"";
  let srclink=c.link?`<a class="src" href="${esc(c.link)}" target="_blank">View source →</a>`:"";
  panel.innerHTML=`<div class="hd"><div class="ttl"><span>${esc(c.metric)}</span><span>${esc(c.value)}</span></div>`
    +`<div class="ent">${esc(c.entity)}</div>`
    +`<div class="meta">${esc(c.source)} &nbsp;|&nbsp; ${esc(c.asof)} &nbsp;|&nbsp; ${esc(c.unit)} &nbsp;|&nbsp; ${esc(c.ref)}</div></div>`
    +(c.definition?`<div class="def"><b>DEFINITION</b> &nbsp; ${esc(c.definition)}</div>`:"")
    +`<div class="calc">${terms}${opline}${rep}${srclink}</div>`;
}
document.querySelectorAll(".card.click[data-cid]").forEach(el=>
  el.addEventListener("click",()=>{const c=CELLS[el.dataset.cid];if(c)showCalc(c,el);}));
"""


def render_traceable_cards(cards, key, columns=7, height=None):
    """cards: list of {label, value, accent?, calc?}. Renders a responsive grid
    of metric cards; clicking any card with a calc shows its calculation +
    source documents in an inline panel below the grid."""
    cells = {}
    html_cards = []
    for i, c in enumerate(cards):
        accent = c.get("accent") or "inherit"
        calc = c.get("calc")
        arr = ' <span class="arr">↗</span>' if calc else ""
        attrs = ""
        cls = "card"
        if calc:
            cells[str(i)] = calc
            cls = "card click"
            attrs = f' data-cid="{i}"'
        html_cards.append(
            f'<div class="{cls}"{attrs}><div class="lbl">{c["label"]}{arr}</div>'
            f'<div class="val" style="color:{accent};">{c["value"]}</div></div>')

    rows = -(-len(cards) // columns)
    if height is None:
        height = 18 + rows * 64 + 240  # grid + inline detail panel
    data = json.dumps(cells)
    html = (f'<!doctype html><html><head><meta charset="utf-8"><style>{_CARD_CSS}'
            f'.grid{{grid-template-columns:repeat({columns},minmax(0,1fr));}}</style></head>'
            f'<body><div class="grid">{"".join(html_cards)}</div>'
            f'<div id="panel"><div class="hint">Click any metric above to see its '
            f'calculation and source documents.</div></div>'
            f'<script>const CELLS={data};{_PANEL_JS}</script></body></html>')
    components.html(html, height=height, scrolling=False)
