"""
KSK Investors — premium light fintech theme.

Design system inspired by Linear, Stripe, and modern trading terminals
in light mode. White base, slate text, electric-blue accents, refined
typography, and subtle elevation.
"""

CUSTOM_CSS = """
<style>
    /* ═══════════════════════════════════════════════════════════════════
       Design Tokens (CSS custom properties) — light premium
    ═══════════════════════════════════════════════════════════════════ */
    :root {
        /* Surfaces */
        --bg-base:       #ffffff;
        --bg-surface:    #f8fafc;
        --bg-elevated:   #ffffff;
        --bg-hover:      #f1f5f9;
        --bg-sidebar:    #f8fafc;
        --bg-inset:      #f1f5f9;

        /* Borders */
        --border-subtle:  rgba(15, 23, 42, 0.06);
        --border-default: rgba(15, 23, 42, 0.10);
        --border-strong:  rgba(15, 23, 42, 0.16);

        /* Text */
        --text-primary:   #0f172a;
        --text-secondary: #475569;
        --text-muted:     #94a3b8;
        --text-inverse:   #ffffff;

        /* Brand — institutional navy (DESIGN-SYSTEM.md: deeper navy/steel
           replaces the bright blue for links, active states, primary series) */
        --brand-primary:   #1e40af;
        --brand-hover:     #1e3a8a;
        --brand-accent:    #2563eb;
        --brand-soft:      rgba(30, 64, 175, 0.07);
        --brand-border:    rgba(30, 64, 175, 0.24);

        /* Semantic */
        --success:        #059669;
        --success-soft:   rgba(5, 150, 105, 0.08);
        --success-border: rgba(5, 150, 105, 0.24);
        --warn:           #d97706;
        --warn-soft:      rgba(217, 119, 6, 0.08);
        --warn-border:    rgba(217, 119, 6, 0.24);
        --danger:         #dc2626;
        --danger-soft:    rgba(220, 38, 38, 0.08);
        --danger-border:  rgba(220, 38, 38, 0.24);

        /* Shadows — soft and premium */
        --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.04), 0 1px 3px rgba(15, 23, 42, 0.06);
        --shadow-md: 0 4px 6px rgba(15, 23, 42, 0.04), 0 10px 15px rgba(15, 23, 42, 0.06);
        --shadow-lg: 0 20px 25px rgba(15, 23, 42, 0.08), 0 10px 10px rgba(15, 23, 42, 0.04);

        /* Radii */
        --radius-sm: 6px;
        --radius-md: 10px;
        --radius-lg: 14px;
        --radius-xl: 20px;

        /* Type scale — the ONLY font sizes inline HTML may use.
           (The audit found 31 ad-hoc sizes between 0.55 and 1.2rem;
           every new size must snap to one of these 7 steps.) */
        --fs-2xs:  0.625rem;   /* 10px — pills, dense table captions */
        --fs-xs:   0.6875rem;  /* 11px — captions, axis/meta labels */
        --fs-sm:   0.75rem;    /* 12px — secondary text, table cells */
        --fs-base: 0.875rem;   /* 14px — body */
        --fs-md:   1rem;       /* 16px — emphasized body, card titles */
        --fs-lg:   1.125rem;   /* 18px — section headings */
        --fs-xl:   1.375rem;   /* 22px — page titles, hero numbers */

        /* Terminal density (DESIGN-SYSTEM.md) */
        --cell-pad-y: 2px;
        --cell-pad-x: 7px;
        --grid-line:  #e2e6ec;
        --grid-head:  #d6dae3;
        --grid-head-bg: #f4f6f9;
    }

    /* ═══════════════════════════════════════════════════════════════════
       Chrome — top nav, title bar, ledgers, grid tables (DESIGN-SYSTEM.md)
    ═══════════════════════════════════════════════════════════════════ */
    /* Top nav: the section radio rendered as a horizontal nav bar. */
    .st-key-topnav { border-bottom: 1px solid var(--grid-head); }
    .st-key-topnav [role="radiogroup"] { display: flex; gap: 4px; flex-wrap: wrap; }
    .st-key-topnav label {
        border: none !important; background: transparent !important;
        padding: 4px 10px !important; margin: 0 !important;
        font-size: var(--fs-sm) !important; color: var(--text-secondary) !important;
        border-radius: 0 !important; cursor: pointer;
        border-bottom: 2px solid transparent !important;
    }
    .st-key-topnav label:has(input:checked) {
        color: var(--brand-primary) !important; font-weight: 600 !important;
        border-bottom: 2px solid var(--brand-primary) !important;
        background: transparent !important;
    }
    /* Render the radio as clean text tabs: hide the circle (the label's
       first-child wrapper) and the <input>, leaving just the label text.
       VERIFIED LIVE on Chrome 149 (2026-06-14) by injecting + screenshotting
       on the user's exact browser: first-child IS the 15px circle wrapper
       there, hiding it leaves the <p> fully visible, tabs render clean.
       (The nav-blank incident that day was a browser render glitch —
       zoom/hardware-acceleration on a fresh Chrome 149 build — NOT this CSS;
       the same rule renders perfectly on the identical browser version.) */
    .st-key-topnav label > div:first-child { display: none !important; }
    .st-key-topnav label > input { position: absolute !important; opacity: 0 !important;
        width: 1px !important; height: 1px !important; margin: 0 !important; }
    .st-key-topnav label { display: inline-flex !important; align-items: center; }

    /* SNL-style title bar */
    .ksk-titlebar { padding: 2px 0 0; }
    .ksk-titlebar .tb-main { font-size: var(--fs-md); font-weight: 600; color: var(--text-primary); }
    .ksk-titlebar .tb-page { font-size: var(--fs-sm); font-weight: 600; letter-spacing: 0.06em; color: var(--text-secondary); }
    .ksk-titlebar .tb-sep  { color: var(--text-muted); font-weight: 400; padding: 0 4px; }
    .ksk-titlebar .tb-ids  { font-size: var(--fs-xs); color: var(--text-secondary); margin-top: 1px; }
    .ksk-titlebar .tb-ids a { color: var(--brand-primary); text-decoration: none; }

    /* Ledger rows (boxless KPI blocks) */
    .ksk-ledger { font-size: var(--fs-sm); }
    .ksk-ledger .lg-title {
        font-size: var(--fs-2xs); letter-spacing: 0.08em; font-weight: 600;
        color: var(--text-secondary); border-bottom: 1px solid var(--grid-head);
        padding-bottom: 2px; margin-bottom: 3px; text-transform: uppercase;
    }
    .ksk-ledger .lg-row {
        display: flex; justify-content: space-between; gap: 10px;
        padding: var(--cell-pad-y) 0; border-bottom: 0.5px solid #eef1f5;
    }
    .ksk-ledger .lg-row:last-child { border-bottom: none; }
    .ksk-ledger .lg-label { color: var(--text-secondary); }
    .ksk-ledger .lg-val   { font-variant-numeric: tabular-nums; font-weight: 600; }

    /* Full-grid data tables (SNL spreadsheet look) */
    .ksk-grid table, table.ksk-grid {
        border-collapse: collapse; font-size: var(--fs-xs);
        font-variant-numeric: tabular-nums; white-space: nowrap;
    }
    .ksk-grid th, .ksk-grid thead td, table.ksk-grid th {
        border: 0.5px solid var(--grid-head); background: var(--grid-head-bg);
        padding: var(--cell-pad-y) var(--cell-pad-x);
        font-weight: 600; color: var(--text-secondary); text-align: right;
    }
    .ksk-grid td, table.ksk-grid td {
        border: 0.5px solid var(--grid-line);
        padding: var(--cell-pad-y) var(--cell-pad-x); text-align: right;
    }
    .ksk-grid th:first-child, .ksk-grid td:first-child { text-align: left; }
    .ksk-grid .neg { color: var(--danger); }
    .ksk-grid .lnk { color: var(--brand-primary); cursor: pointer; }
    .ksk-grid .computed { border-bottom: 1px dotted var(--text-muted); }

    /* Status dot */
    .ksk-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%;
               margin-right: 4px; vertical-align: middle; }
    .ksk-dot.ok { background: var(--success); }
    .ksk-dot.warn { background: var(--warn); }
    .ksk-dot.bad { background: var(--danger); }

    /* ═══════════════════════════════════════════════════════════════════
       Base
    ═══════════════════════════════════════════════════════════════════ */
    .stApp {
        background: var(--bg-base);
        color: var(--text-primary);
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', system-ui, sans-serif;
        font-feature-settings: 'cv11', 'ss01', 'ss02', 'tnum';
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
    }

    /* Slightly smaller base so the whole dashboard shows more per screen. */
    html { font-size: 15px; }

    /* Main container — dense: more usable width, tight top padding. */
    .main > div.block-container,
    .block-container {
        padding-top: 0.6rem !important;
        padding-bottom: 1.25rem !important;
        padding-left: 1.25rem !important;
        padding-right: 1.25rem !important;
        max-width: 100%;
    }

    /* Compress vertical rhythm so you scroll far less. */
    [data-testid="stVerticalBlock"] { gap: 0.45rem !important; }
    [data-testid="stElementContainer"] { margin-bottom: 0 !important; }
    [data-testid="stHorizontalBlock"] { gap: 0.6rem !important; }
    .stMarkdown p { margin-bottom: 0.35rem; }
    hr { margin: 0.5rem 0 !important; }

    /* Typography — tighter headings */
    h1, h2, h3, h4, h5, h6 {
        color: var(--text-primary);
        font-weight: 600;
        letter-spacing: -0.011em;
        font-family: 'Inter', system-ui, sans-serif;
        margin-top: 0.2rem !important;
        margin-bottom: 0.3rem !important;
    }
    h1 { font-size: 1.5rem; letter-spacing: -0.022em; }
    h2 { font-size: 1.2rem; letter-spacing: -0.02em; }
    h3 { font-size: 1.02rem; }
    h4 { font-size: 0.92rem; }

    .stMarkdown p {
        color: var(--text-secondary);
        line-height: 1.55;
    }
    .stMarkdown code {
        background: var(--bg-inset) !important;
        color: var(--brand-primary) !important;
        padding: 2px 6px !important;
        border-radius: 4px !important;
        font-size: 0.85em !important;
        font-family: 'JetBrains Mono', 'SF Mono', 'Menlo', monospace !important;
        border: 1px solid var(--border-subtle) !important;
    }

    hr {
        border: none !important;
        height: 1px !important;
        background: var(--border-subtle) !important;
        margin: 1.5rem 0 !important;
    }

    ::selection {
        background: var(--brand-soft);
        color: var(--text-primary);
    }

    /* ═══════════════════════════════════════════════════════════════════
       Sidebar
    ═══════════════════════════════════════════════════════════════════ */
    section[data-testid="stSidebar"] {
        background: var(--bg-sidebar) !important;
        border-right: 1px solid var(--border-subtle) !important;
        padding-top: 0 !important;
    }
    section[data-testid="stSidebar"] > div {
        padding-top: 0.5rem !important;
    }
    section[data-testid="stSidebar"] .block-container {
        padding: 1.25rem 1rem !important;
    }

    section[data-testid="stSidebar"] h1 {
        font-size: 1rem !important;
        font-weight: 700 !important;
        letter-spacing: -0.01em !important;
        color: var(--text-primary) !important;
        margin-bottom: 0.2rem !important;
    }
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3 {
        font-size: 0.78rem !important;
        font-weight: 600 !important;
        color: var(--text-muted) !important;
        text-transform: uppercase;
        letter-spacing: 0.06em !important;
    }
    section[data-testid="stSidebar"] p {
        font-size: 0.8rem !important;
        color: var(--text-secondary) !important;
    }

    /* Sidebar nav pills */
    section[data-testid="stSidebar"] div[role="radiogroup"] {
        gap: 4px !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label {
        background: transparent !important;
        border: 1px solid transparent !important;
        border-radius: var(--radius-md) !important;
        padding: 8px 12px !important;
        margin: 0 !important;
        transition: all 0.15s ease !important;
        cursor: pointer;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label:hover {
        background: var(--bg-hover) !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label[data-baseweb="radio"] > div:first-child {
        display: none !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label p {
        color: var(--text-secondary) !important;
        font-size: 0.88rem !important;
        font-weight: 500 !important;
        margin: 0 !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) {
        background: var(--brand-soft) !important;
        border-color: var(--brand-border) !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) p {
        color: var(--brand-primary) !important;
        font-weight: 600 !important;
    }

    /* Ticker chips in sidebar */
    section[data-testid="stSidebar"] p code,
    section[data-testid="stSidebar"] code[class*="emotion"] {
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 12px !important;
        font-weight: 600 !important;
        padding: 2px 7px !important;
        background: var(--bg-elevated) !important;
        color: var(--brand-primary) !important;
        border: 1px solid var(--border-subtle) !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"] {
        margin-bottom: 2px !important;
    }

    section[data-testid="stSidebar"] hr {
        margin: 0.9rem 0 !important;
        background: var(--border-subtle) !important;
    }

    /* ═══════════════════════════════════════════════════════════════════
       Header / Hero
    ═══════════════════════════════════════════════════════════════════ */
    .dashboard-header {
        background: var(--bg-elevated);
        padding: 1.1rem 1.4rem;
        border-radius: var(--radius-lg);
        margin-bottom: 1.2rem;
        border: 1px solid var(--border-subtle);
        box-shadow: var(--shadow-sm);
    }
    .dashboard-header h1 {
        color: var(--text-primary);
        margin: 0;
        font-size: 1.45rem;
        font-weight: 700;
        letter-spacing: -0.02em;
    }
    .dashboard-header p {
        color: var(--text-secondary);
        margin: 0.35rem 0 0 0;
        font-size: 0.82rem;
        line-height: 1.45;
    }

    /* Hero card (Home) — light premium */
    .ksk-hero {
        background:
            radial-gradient(1200px 400px at 0% 0%, rgba(37, 99, 235, 0.06) 0%, transparent 55%),
            radial-gradient(800px 300px at 100% 100%, rgba(99, 102, 241, 0.04) 0%, transparent 60%),
            #ffffff;
        border: 1px solid var(--border-subtle);
        border-radius: var(--radius-xl);
        padding: 1.6rem 1.8rem;
        box-shadow: var(--shadow-md);
        position: relative;
        overflow: hidden;
    }
    .ksk-hero::before {
        content: "";
        position: absolute;
        top: 0; left: 0; right: 0;
        height: 2px;
        background: linear-gradient(90deg,
            transparent 0%,
            rgba(37, 99, 235, 0.4) 50%,
            transparent 100%);
    }
    .ksk-hero h1 {
        font-size: 1.75rem;
        font-weight: 700;
        letter-spacing: -0.025em;
        color: var(--text-primary);
        margin: 0;
    }
    .ksk-hero-subtitle {
        color: var(--text-secondary);
        font-size: 0.92rem;
        margin: 0.4rem 0 0 0;
        line-height: 1.5;
    }
    .ksk-hero-meta {
        color: var(--text-muted);
        font-size: 0.75rem;
        margin-top: 0.6rem;
        display: flex;
        align-items: center;
        gap: 0.5rem;
        flex-wrap: wrap;
    }
    .ksk-hero-meta .dot {
        width: 6px; height: 6px;
        border-radius: 50%;
        background: var(--success);
        box-shadow: 0 0 8px rgba(5, 150, 105, 0.5);
    }

    /* ═══════════════════════════════════════════════════════════════════
       Metric Cards (st.metric override)
    ═══════════════════════════════════════════════════════════════════ */
    div[data-testid="stMetric"] {
        background: var(--bg-elevated);
        border: 1px solid var(--border-subtle);
        border-radius: var(--radius-md);
        padding: 14px 16px !important;
        transition: all 0.15s ease;
        box-shadow: var(--shadow-sm);
    }
    div[data-testid="stMetric"]:hover {
        border-color: var(--border-default);
        box-shadow: var(--shadow-md);
    }
    div[data-testid="stMetricLabel"] {
        color: var(--text-muted) !important;
        font-size: 0.72rem !important;
        font-weight: 500 !important;
        text-transform: uppercase;
        letter-spacing: 0.06em;
    }
    div[data-testid="stMetricValue"] {
        color: var(--text-primary) !important;
        font-size: 1.45rem !important;
        font-weight: 600 !important;
        letter-spacing: -0.015em;
        font-variant-numeric: tabular-nums;
        line-height: 1.2 !important;
    }
    div[data-testid="stMetricDelta"] {
        font-size: 0.75rem !important;
        font-weight: 500 !important;
    }

    /* Status dots */
    .status-dot {
        display: inline-block;
        width: 7px;
        height: 7px;
        border-radius: 50%;
        margin-right: 6px;
    }
    .status-connected { background: var(--success); box-shadow: 0 0 8px rgba(5,150,105,0.4); }

    /* ═══════════════════════════════════════════════════════════════════
       Tabs
    ═══════════════════════════════════════════════════════════════════ */
    .stTabs [data-baseweb="tab-list"] {
        gap: 2px;
        background: var(--bg-surface);
        padding: 4px;
        border-radius: var(--radius-md);
        border: 1px solid var(--border-subtle);
    }
    .stTabs [data-baseweb="tab"] {
        height: 36px !important;
        padding: 0 14px !important;
        border-radius: var(--radius-sm) !important;
        color: var(--text-secondary) !important;
        font-size: 0.85rem !important;
        font-weight: 500 !important;
        border: none !important;
        background: transparent !important;
        transition: all 0.12s ease;
    }
    .stTabs [data-baseweb="tab"]:hover {
        background: var(--bg-elevated) !important;
        color: var(--text-primary) !important;
    }
    .stTabs [data-baseweb="tab"][aria-selected="true"] {
        background: var(--bg-elevated) !important;
        color: var(--text-primary) !important;
        font-weight: 600 !important;
        box-shadow: var(--shadow-sm);
    }
    .stTabs [data-baseweb="tab-highlight"],
    .stTabs [data-baseweb="tab-border"] {
        display: none !important;
    }

    /* ═══════════════════════════════════════════════════════════════════
       Tables / DataFrames
    ═══════════════════════════════════════════════════════════════════ */
    div[data-testid="stDataFrame"] {
        background: var(--bg-elevated);
        border: 1px solid var(--border-subtle);
        border-radius: var(--radius-md);
        overflow: hidden;
        box-shadow: var(--shadow-sm);
    }
    div[data-testid="stDataFrame"] canvas {
        font-size: 11px !important;
        font-family: 'Inter', system-ui, sans-serif !important;
    }

    /* ═══════════════════════════════════════════════════════════════════
       Buttons
    ═══════════════════════════════════════════════════════════════════ */
    .stButton > button {
        background: var(--bg-elevated) !important;
        color: var(--text-primary) !important;
        border: 1px solid var(--border-default) !important;
        border-radius: var(--radius-sm) !important;
        font-size: 0.85rem !important;
        font-weight: 500 !important;
        padding: 7px 14px !important;
        transition: all 0.12s ease;
        box-shadow: var(--shadow-sm);
    }
    .stButton > button:hover {
        background: var(--bg-hover) !important;
        border-color: var(--border-strong) !important;
    }
    .stButton > button[kind="primary"] {
        background: var(--brand-primary) !important;
        border-color: var(--brand-primary) !important;
        color: #ffffff !important;
    }
    .stButton > button[kind="primary"]:hover {
        background: var(--brand-hover) !important;
        border-color: var(--brand-hover) !important;
    }
    .stDownloadButton > button {
        background: var(--bg-elevated) !important;
        color: var(--text-primary) !important;
        border: 1px solid var(--border-default) !important;
        border-radius: var(--radius-sm) !important;
        font-size: 0.82rem !important;
    }

    /* ═══════════════════════════════════════════════════════════════════
       Form Inputs
    ═══════════════════════════════════════════════════════════════════ */
    div[data-baseweb="select"] > div,
    div[data-baseweb="input"] > div {
        background: var(--bg-elevated) !important;
        border: 1px solid var(--border-default) !important;
        border-radius: var(--radius-sm) !important;
        min-height: 36px !important;
        font-size: 0.88rem !important;
        transition: border-color 0.12s ease;
    }
    div[data-baseweb="select"] > div:hover,
    div[data-baseweb="input"] > div:hover {
        border-color: var(--border-strong) !important;
    }
    div[data-baseweb="select"] > div:focus-within,
    div[data-baseweb="input"] > div:focus-within {
        border-color: var(--brand-primary) !important;
        box-shadow: 0 0 0 3px var(--brand-soft) !important;
    }
    input, textarea {
        background: transparent !important;
        color: var(--text-primary) !important;
    }
    label {
        color: var(--text-secondary) !important;
        font-size: 0.8rem !important;
        font-weight: 500 !important;
    }

    /* Slider */
    div[data-testid="stSlider"] > div > div > div {
        background: var(--bg-inset) !important;
    }
    div[data-testid="stSlider"] [role="slider"] {
        background: var(--brand-primary) !important;
        border: 2px solid #ffffff !important;
        box-shadow: var(--shadow-sm);
    }

    /* Expanders */
    details {
        background: var(--bg-elevated) !important;
        border: 1px solid var(--border-subtle) !important;
        border-radius: var(--radius-md) !important;
        overflow: hidden;
        box-shadow: var(--shadow-sm);
    }
    details summary {
        font-size: 0.88rem !important;
        font-weight: 500 !important;
        color: var(--text-primary) !important;
        padding: 10px 14px !important;
        cursor: pointer;
    }
    details summary:hover {
        background: var(--bg-hover) !important;
    }
    details[open] summary {
        border-bottom: 1px solid var(--border-subtle) !important;
    }
    details > div {
        padding: 14px !important;
    }

    /* ═══════════════════════════════════════════════════════════════════
       Info / Warning / Error / Success callouts
    ═══════════════════════════════════════════════════════════════════ */
    div[data-testid="stAlert"] {
        border-radius: var(--radius-md) !important;
        border: 1px solid var(--border-subtle) !important;
        padding: 12px 14px !important;
        font-size: 0.88rem !important;
    }
    div[data-testid="stAlert"][data-baseweb="notification"][kind="info"] {
        background: var(--brand-soft) !important;
        border-color: var(--brand-border) !important;
        color: var(--brand-primary) !important;
    }
    div[data-testid="stAlert"][data-baseweb="notification"][kind="warning"] {
        background: var(--warn-soft) !important;
        border-color: var(--warn-border) !important;
        color: var(--warn) !important;
    }
    div[data-testid="stAlert"][data-baseweb="notification"][kind="error"] {
        background: var(--danger-soft) !important;
        border-color: var(--danger-border) !important;
        color: var(--danger) !important;
    }
    div[data-testid="stAlert"][data-baseweb="notification"][kind="success"] {
        background: var(--success-soft) !important;
        border-color: var(--success-border) !important;
        color: var(--success) !important;
    }

    /* ═══════════════════════════════════════════════════════════════════
       Alert row (Home inbox) — consistent visual
    ═══════════════════════════════════════════════════════════════════ */
    .alert-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 9px 13px;
        margin: 4px 0;
        border-radius: var(--radius-sm);
        font-size: 0.86rem;
        border: 1px solid var(--border-subtle);
        background: var(--bg-elevated);
        box-shadow: var(--shadow-sm);
    }
    .alert-row.severity-high {
        border-left: 3px solid var(--danger);
        background: var(--danger-soft);
    }
    .alert-row.severity-medium {
        border-left: 3px solid var(--warn);
        background: var(--warn-soft);
    }
    .alert-row.severity-ok {
        border-left: 3px solid var(--success);
        background: var(--success-soft);
    }

    /* ═══════════════════════════════════════════════════════════════════
       Freshness badges
    ═══════════════════════════════════════════════════════════════════ */
    .freshness-badge {
        display: inline-block;
        padding: 2px 9px;
        border-radius: 12px;
        font-size: 0.7rem;
        font-weight: 500;
        border: 1px solid;
    }
    .freshness-live {
        background: var(--success-soft);
        color: var(--success);
        border-color: var(--success-border);
    }
    .freshness-cached {
        background: var(--warn-soft);
        color: var(--warn);
        border-color: var(--warn-border);
    }
    .freshness-stale {
        background: var(--danger-soft);
        color: var(--danger);
        border-color: var(--danger-border);
    }

    /* ═══════════════════════════════════════════════════════════════════
       Scrollbars
    ═══════════════════════════════════════════════════════════════════ */
    ::-webkit-scrollbar {
        width: 10px;
        height: 10px;
    }
    ::-webkit-scrollbar-track {
        background: var(--bg-surface);
    }
    ::-webkit-scrollbar-thumb {
        background: var(--border-default);
        border-radius: 5px;
    }
    ::-webkit-scrollbar-thumb:hover {
        background: var(--border-strong);
    }

    /* Plotly chart containers */
    .js-plotly-plot .plotly {
        background: transparent !important;
    }
    .stCaption, div[data-testid="stCaptionContainer"] {
        color: var(--text-muted) !important;
        font-size: 0.78rem !important;
    }

    pre, code {
        background: var(--bg-inset) !important;
        color: var(--text-primary) !important;
        border-radius: var(--radius-sm);
    }

    /* ── Company Analysis two-level nav ──────────────────────────────────
       Primary = sections (underline tab bar); secondary = sub-tabs (lighter
       pills). Both scoped to keyed containers in app.py so they don't affect
       other radios (Annual/Quarterly, price period, sidebar nav). */

    /* Primary: section tab bar */
    .st-key-company_section_nav div[role="radiogroup"] {
        display: flex; flex-wrap: wrap; gap: 2px 4px; align-items: flex-end;
        border-bottom: 1px solid rgba(148, 163, 184, 0.28); margin-bottom: 2px;
    }
    .st-key-company_section_nav div[role="radiogroup"] > label {
        margin: 0 !important; padding: 7px 14px; cursor: pointer;
        border-bottom: 2px solid transparent; border-radius: 6px 6px 0 0;
        transition: background 0.12s, border-color 0.12s;
    }
    .st-key-company_section_nav div[role="radiogroup"] > label:hover {
        background: rgba(37, 99, 235, 0.06);
    }
    .st-key-company_section_nav div[role="radiogroup"] > label > div:first-of-type {
        display: none !important;
    }
    .st-key-company_section_nav div[role="radiogroup"] > label p {
        font-size: 0.95rem; color: var(--text-secondary); font-weight: 600;
    }
    .st-key-company_section_nav div[role="radiogroup"] > label:has(input:checked) {
        border-bottom-color: #2563eb;
    }
    .st-key-company_section_nav div[role="radiogroup"] > label:has(input:checked) p {
        color: #2563eb; font-weight: 700;
    }

    /* Secondary: sub-tab pills */
    .st-key-company_subtab_nav div[role="radiogroup"] {
        display: flex; flex-wrap: wrap; gap: 4px; align-items: center;
        margin: 6px 0 2px;
    }
    .st-key-company_subtab_nav div[role="radiogroup"] > label {
        margin: 0 !important; padding: 3px 11px; cursor: pointer;
        border-radius: 14px; transition: background 0.12s;
    }
    .st-key-company_subtab_nav div[role="radiogroup"] > label:hover {
        background: rgba(37, 99, 235, 0.06);
    }
    .st-key-company_subtab_nav div[role="radiogroup"] > label > div:first-of-type {
        display: none !important;
    }
    .st-key-company_subtab_nav div[role="radiogroup"] > label p {
        font-size: 0.82rem; color: var(--text-secondary); font-weight: 500;
    }
    .st-key-company_subtab_nav div[role="radiogroup"] > label:has(input:checked) {
        background: rgba(37, 99, 235, 0.12);
    }
    .st-key-company_subtab_nav div[role="radiogroup"] > label:has(input:checked) p {
        color: #2563eb; font-weight: 600;
    }

    /* Basis: Company Reported | Templated (segmented control between the
       section tab bar and the sub-tab pills) */
    .st-key-company_basis_nav div[role="radiogroup"] {
        display: inline-flex; gap: 0; margin: 6px 0 4px;
        border: 1px solid rgba(148, 163, 184, 0.45); border-radius: 8px; overflow: hidden;
    }
    .st-key-company_basis_nav div[role="radiogroup"] > label {
        margin: 0 !important; padding: 5px 18px; cursor: pointer;
        border-radius: 0; transition: background 0.12s;
    }
    .st-key-company_basis_nav div[role="radiogroup"] > label:hover {
        background: rgba(37, 99, 235, 0.06);
    }
    .st-key-company_basis_nav div[role="radiogroup"] > label > div:first-of-type {
        display: none !important;
    }
    .st-key-company_basis_nav div[role="radiogroup"] > label p {
        font-size: 0.85rem; color: var(--text-secondary); font-weight: 600;
    }
    .st-key-company_basis_nav div[role="radiogroup"] > label:has(input:checked) {
        background: #2563eb;
    }
    .st-key-company_basis_nav div[role="radiogroup"] > label:has(input:checked) p {
        color: #fff; font-weight: 700;
    }

    /* ── Native traceable metric cards (ui/source_trace) ──────────────────
       Rendered as plain HTML (no iframe), so the grid sizes to its content —
       hover a card for the calculation, click ↗ for the source filing. */
    .mc-grid { display: grid; gap: 8px; margin: 2px 0 8px; }
    .mc {
        background: rgba(148,163,184,0.05);
        border: 1px solid rgba(148,163,184,0.16);
        border-radius: 10px; padding: 8px 12px; min-width: 0;
        transition: border-color 0.12s, background 0.12s;
    }
    .mc[title]:hover { border-color: rgba(37,99,235,0.35); background: rgba(37,99,235,0.05); }
    .mc[title] { cursor: help; }
    .mc-l {
        font-size: 0.6rem; color: var(--text-secondary); font-weight: 600;
        text-transform: uppercase; letter-spacing: 0.02em;
        display: flex; justify-content: space-between; align-items: center; gap: 4px;
    }
    .mc-v {
        font-size: 1.05rem; font-weight: 700; color: var(--text-primary);
        line-height: 1.3; margin-top: 3px;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .mc-src { color: #b6c0cc; text-decoration: none; font-weight: 400; flex-shrink: 0; }
    .mc-src:hover { color: var(--brand-primary); }
</style>
"""
