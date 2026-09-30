"""
Canonical HTML atoms for the dashboard (P3 design system,
docs/AUDIT-2026-06-11.md §D).

These were first extracted to de-duplicate the home page. The home page has
since been rebuilt as the bespoke dense `_AF` above-the-fold grid (ui/home.py),
which has its own self-contained markup, so it no longer calls these atoms.
They remain the CANONICAL atom library for every OTHER page (Screen & Compare,
detail blocks, etc.) — reach for these instead of hand-rolling a pill or
section header, so the design system stays in one place.

One implementation per visual atom; variants come from parameters
(label, value, accent, href) — never from copies. Rules:

  * Font sizes only from the styles.py :root type scale — var(--fs-2xs)
    through var(--fs-xl). No ad-hoc rem values.
  * Colors only from the styles.py :root tokens — no raw hexes here.
  * Feed-/user-derived text (headlines, summaries, bank names, tickers)
    is html.escape()d INSIDE the component, so callers can't forget.
    Parameters named *_html are trusted markup the caller already built
    (from escaped parts where needed) and are interpolated as-is.
"""

import html as _html

import streamlit as st


# ──────────────────────────────────────────────────────────────────────
# Section header — the divider every home-page block starts with
# ──────────────────────────────────────────────────────────────────────

def section_header(emoji: str, title: str, subtitle_html: str = ""):
    """Consistent section divider: bold title left, muted subtitle right."""
    sub = (f'<span style="font-size:var(--fs-sm); color:var(--text-muted); '
           f'font-weight:500; margin-left:auto;">{subtitle_html}</span>') if subtitle_html else ""
    st.markdown(
        '<div style="display:flex; align-items:baseline; gap:9px; margin:22px 0 9px; '
        'padding-bottom:6px; border-bottom:2px solid var(--border-default);">'
        f'<span style="font-size:var(--fs-md); font-weight:700; color:var(--text-primary); '
        f'letter-spacing:-0.01em;">{emoji} {title}</span>{sub}</div>',
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────────────────────────────
# Stat pill — label-over-value chip (rates, risk, ETF links, medians)
# ──────────────────────────────────────────────────────────────────────

# accent → (background, border, label color, value color, value size, padding)
_PILL_ACCENTS = {
    "neutral": ("var(--bg-surface)", "var(--border-default)",
                "var(--text-muted)", "var(--text-primary)",
                "var(--fs-base)", "3px 11px"),
    "brand":   ("var(--brand-soft)", "var(--brand-border)",
                "var(--text-secondary)", "var(--brand-primary)",
                "var(--fs-md)", "4px 13px"),
}


def stat_pill(label: str, value_html: str, delta_html: str = "",
              accent: str = "neutral", href: str = None,
              hover_title: str = None, selected: bool = False,
              foot_html: str = "") -> str:
    """Dense label-over-value pill.

    Variants via parameters: `accent` picks the palette, `delta_html`
    sits inline after the value, `foot_html` adds a tiny
    context line, `href` wraps the pill in a same-tab link, `selected`
    highlights the border (e.g. the active benchmark).
    """
    bg, border, label_col, value_col, value_fs, pad = _PILL_ACCENTS[accent]
    if selected:
        border = "var(--brand-primary)"
    val = f"{value_html} {delta_html}".rstrip()
    foot = (f'<span style="font-size:var(--fs-2xs); margin-top:1px;">{foot_html}</span>'
            if foot_html else "")
    pill = (
        '<span style="display:inline-flex; flex-direction:column; '
        f'padding:{pad}; border-radius:0; background:{bg}; '
        f'border:1px solid {border}; line-height:1.25;'
        + ("cursor:pointer;" if href else "") + '">'
        f'<span style="font-size:var(--fs-2xs); color:{label_col}; font-weight:600; '
        f'letter-spacing:0.04em;">{label}</span>'
        f'<span style="font-size:{value_fs}; font-weight:700; color:{value_col}; '
        f'white-space:nowrap;">{val}</span>{foot}</span>'
    )
    if href:
        title_attr = (f' title="{_html.escape(hover_title, quote=True)}"'
                      if hover_title else "")
        return (f'<a href="{_html.escape(href, quote=True)}" target="_self"{title_attr} '
                f'style="text-decoration:none; color:inherit;">{pill}</a>')
    return pill


def pill_row(pills, margin: str = "0 0 6px", gap: int = 6):
    """Render a wrapping flex row of pills via st.markdown."""
    st.markdown(
        f'<div style="display:flex; gap:{gap}px; flex-wrap:wrap; margin:{margin};">'
        + "".join(pills) + "</div>",
        unsafe_allow_html=True,
    )
