"""
Canonical HTML atoms for the dashboard (P3 design system,
docs/AUDIT-2026-06-11.md §D).

These were first extracted to de-duplicate the home page. The home page has
since been rebuilt as the bespoke dense `_AF` above-the-fold grid (ui/home.py),
which has its own self-contained markup, so it no longer calls these atoms.
They remain the CANONICAL atom library for every OTHER page (Screen & Compare,
detail blocks, etc.) — reach for these instead of hand-rolling a pill / card /
deep-link row, so the design system stays in one place.

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
# Delta chip — tiny colored ± change marker inside a stat pill
# ──────────────────────────────────────────────────────────────────────

def delta_chip(change, unit: str, up_is_good: bool = True) -> str:
    """Small colored Δ chip. `change` in the unit's terms (bp or pt)."""
    if change is None or abs(change) < (0.5 if unit == "bp" else 0.05):
        return ('<span style="font-size:var(--fs-2xs); color:var(--text-muted); '
                'font-weight:600;">unch</span>')
    good = (change > 0) == up_is_good
    col = "var(--success)" if good else "var(--danger)"
    dp = 0 if unit == "bp" else 1
    return (f'<span style="font-size:var(--fs-2xs); font-weight:700; color:{col};">'
            f'{change:+.{dp}f} {unit}</span>')


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
    sits inline after the value (use delta_chip), `foot_html` adds a tiny
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
        f'padding:{pad}; border-radius:7px; background:{bg}; '
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


# ──────────────────────────────────────────────────────────────────────
# Bank link row — deep-link list row (movers, calendar, leaderboard)
# ──────────────────────────────────────────────────────────────────────

def bank_link_row(ticker: str, name: str, right_html: str) -> str:
    """One `?bank=TICKER` deep-link row: bold ticker + truncating muted
    name on the left, caller-built right_html (price/date/metric) on the
    right in tabular figures. Ticker and name are escaped here."""
    return (
        f'<a href="?bank={_html.escape(str(ticker), quote=True)}" target="_self" '
        'style="display:flex; align-items:baseline; justify-content:space-between; '
        'gap:8px; padding:5px 11px; border-radius:7px; text-decoration:none; '
        'border:1px solid var(--border-subtle);">'
        '<span style="flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; '
        'white-space:nowrap;">'
        f'<strong style="color:var(--text-primary);">{_html.escape(str(ticker))}</strong> '
        f'<span style="color:var(--text-muted); font-size:var(--fs-sm);">'
        f'{_html.escape(str(name or ""))}</span></span>'
        f'<span style="white-space:nowrap; font-variant-numeric:tabular-nums;">'
        f'{right_html}</span></a>'
    )


def list_column(title: str, rows_html: str,
                accent: str = "var(--text-secondary)",
                empty_text: str = "—") -> str:
    """Mini-header + stacked rows (a leaderboard / movers / calendar column)."""
    head = (f'<div style="font-size:var(--fs-xs); font-weight:700; '
            f'letter-spacing:0.05em; color:{accent}; margin:0 0 5px;">{title}</div>')
    body = rows_html or (
        f'<div style="color:var(--text-muted); font-size:var(--fs-base); '
        f'padding:5px 11px;">{empty_text}</div>')
    return (head +
            f'<div style="display:flex; flex-direction:column; gap:4px;">{body}</div>')


# ──────────────────────────────────────────────────────────────────────
# News / deal card and alert rows (Alert Inbox)
# ──────────────────────────────────────────────────────────────────────

def news_card(meta_html: str, headline: str, summary: str = "",
              severity: str = ""):
    """Block card for feed-derived items (news tab, M&A). The headline and
    summary are third-party feed text and are ESCAPED HERE — one stray '<'
    would otherwise corrupt the page. meta_html is caller-built markup."""
    head_safe = _html.escape(headline)
    summ_safe = _html.escape(summary) if summary else ""
    sev = f" severity-{severity}" if severity else ""
    st.markdown(
        f'<div class="alert-row{sev}" style="display:block; padding:9px 14px;">'
        f'<div style="font-size:var(--fs-sm); color:var(--text-muted);">{meta_html}</div>'
        f'<div style="color:var(--text-primary); font-weight:600; margin-top:2px;">'
        f'{head_safe}</div>'
        + (f'<div style="color:var(--text-secondary); font-size:var(--fs-base); '
           f'margin-top:2px; line-height:1.45;">{summ_safe}</div>' if summ_safe else "")
        + '</div>',
        unsafe_allow_html=True,
    )


def alert_row(severity: str, left_html: str, right_html: str) -> str:
    """Single alert row using the shared .alert-row style (ui/styles.py)."""
    return (
        f'<div class="alert-row severity-{severity}">'
        f'<span>{left_html}</span>'
        f'<span style="color:var(--text-secondary);">{right_html}</span>'
        f'</div>'
    )


def external_link(url: str, text: str = "open ↗") -> str:
    """Small external 'open ↗' link, or '' when there is no URL."""
    if not url:
        return ""
    return (f' <a href="{_html.escape(url, quote=True)}" target="_blank" '
            f'style="color:var(--brand-accent); text-decoration:none;">{text}</a>')
