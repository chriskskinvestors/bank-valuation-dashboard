"""Custom CSS for the bank valuation dashboard — compact white/light gray theme."""

CUSTOM_CSS = """
<style>
    /* Light theme base */
    .stApp {
        background-color: #ffffff;
    }

    /* Compact global spacing */
    .block-container {
        padding-top: 1rem !important;
        padding-bottom: 0.5rem !important;
    }
    .stMarkdown, .stDataFrame, .stSelectbox, .stTextInput {
        margin-bottom: 0 !important;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.1rem !important;
    }
    div[data-testid="stMetricLabel"] {
        font-size: 0.7rem !important;
    }
    div[data-testid="stMetric"] {
        padding: 0.3rem 0 !important;
    }

    /* Smaller sidebar */
    section[data-testid="stSidebar"] {
        background-color: #f8f9fa;
        border-right: 1px solid #e0e0e0;
        width: 220px !important;
    }
    section[data-testid="stSidebar"] .block-container {
        padding: 0.5rem 0.8rem !important;
    }
    section[data-testid="stSidebar"] h1 {
        font-size: 1.2rem !important;
    }
    section[data-testid="stSidebar"] h2, section[data-testid="stSidebar"] h3 {
        font-size: 0.85rem !important;
        margin-top: 0.3rem !important;
    }
    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] span {
        font-size: 0.78rem !important;
    }

    /* Header styling — compact */
    .dashboard-header {
        background: #f8f9fa;
        padding: 0.8rem 1.2rem;
        border-radius: 6px;
        margin-bottom: 0.6rem;
        border: 1px solid #e0e0e0;
    }
    .dashboard-header h1 {
        color: #1a1a1a;
        margin: 0;
        font-size: 1.3rem;
        font-weight: 700;
    }
    .dashboard-header p {
        color: #666;
        margin: 0.15rem 0 0 0;
        font-size: 0.75rem;
    }

    /* Metric cards — compact */
    .metric-card {
        background: #f8f9fa;
        border: 1px solid #e0e0e0;
        border-radius: 6px;
        padding: 0.5rem;
        text-align: center;
    }
    .metric-card .label {
        color: #666;
        font-size: 0.65rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .metric-card .value {
        color: #1a1a1a;
        font-size: 1.1rem;
        font-weight: 600;
    }

    /* Data freshness badges */
    .freshness-badge {
        display: inline-block;
        padding: 1px 6px;
        border-radius: 10px;
        font-size: 0.6rem;
        font-weight: 500;
    }
    .freshness-live {
        background: #e8f5e9;
        color: #2e7d32;
    }
    .freshness-cached {
        background: #fff8e1;
        color: #f57f17;
    }
    .freshness-stale {
        background: #ffebee;
        color: #c62828;
    }

    /* Table — compact */
    .dataframe {
        font-size: 0.75rem !important;
    }
    .dataframe th {
        background-color: #f5f5f5 !important;
        color: #555 !important;
        font-weight: 600 !important;
        text-transform: uppercase;
        font-size: 0.65rem !important;
        letter-spacing: 0.02em;
        border-bottom: 2px solid #ddd !important;
        padding: 4px 6px !important;
    }
    .dataframe td {
        color: #1a1a1a !important;
        border-bottom: 1px solid #eee !important;
        padding: 3px 6px !important;
    }

    /* Smaller selectboxes / inputs */
    div[data-baseweb="select"] {
        font-size: 0.8rem !important;
    }
    div[data-baseweb="select"] > div {
        min-height: 32px !important;
    }

    /* Category headers */
    .category-header {
        color: #1a73e8;
        font-size: 0.65rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin: 0.5rem 0 0.2rem 0;
        padding-bottom: 0.15rem;
        border-bottom: 1px solid #e0e0e0;
    }

    /* Clickable row hover */
    .bank-row:hover {
        background-color: rgba(26,115,232,0.06) !important;
        cursor: pointer;
    }

    /* Status indicators */
    .status-dot {
        display: inline-block;
        width: 7px;
        height: 7px;
        border-radius: 50%;
        margin-right: 5px;
    }
    .status-connected { background: #2e7d32; }
    .status-disconnected { background: #c62828; }

    /* Compact expanders */
    details summary {
        font-size: 0.8rem !important;
    }

    /* Tighter button spacing */
    .stButton > button {
        font-size: 0.78rem !important;
        padding: 0.25rem 0.75rem !important;
    }
</style>
"""
