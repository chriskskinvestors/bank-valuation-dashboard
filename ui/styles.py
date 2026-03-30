"""Custom CSS for the bank valuation dashboard."""

CUSTOM_CSS = """
<style>
    /* Dark theme overrides */
    .stApp {
        background-color: #0e1117;
    }

    /* Header styling */
    .dashboard-header {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        padding: 1.5rem 2rem;
        border-radius: 10px;
        margin-bottom: 1.5rem;
        border: 1px solid #2a2a4a;
    }
    .dashboard-header h1 {
        color: #e0e0e0;
        margin: 0;
        font-size: 1.8rem;
    }
    .dashboard-header p {
        color: #888;
        margin: 0.3rem 0 0 0;
        font-size: 0.9rem;
    }

    /* Metric cards */
    .metric-card {
        background: #1a1a2e;
        border: 1px solid #2a2a4a;
        border-radius: 8px;
        padding: 1rem;
        text-align: center;
    }
    .metric-card .label {
        color: #888;
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .metric-card .value {
        color: #e0e0e0;
        font-size: 1.5rem;
        font-weight: 600;
    }

    /* Data freshness badges */
    .freshness-badge {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 0.7rem;
        font-weight: 500;
    }
    .freshness-live {
        background: rgba(0,200,83,0.2);
        color: #00c853;
    }
    .freshness-cached {
        background: rgba(255,214,0,0.2);
        color: #ffd600;
    }
    .freshness-stale {
        background: rgba(255,23,68,0.2);
        color: #ff1744;
    }

    /* Table enhancements */
    .dataframe {
        font-size: 0.85rem !important;
    }
    .dataframe th {
        background-color: #1a1a2e !important;
        color: #aaa !important;
        font-weight: 600 !important;
        text-transform: uppercase;
        font-size: 0.75rem !important;
        letter-spacing: 0.03em;
    }
    .dataframe td {
        color: #e0e0e0 !important;
    }

    /* Sidebar styling */
    .css-1d391kg {
        background-color: #0e1117;
    }

    /* Category headers in table */
    .category-header {
        color: #64b5f6;
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin: 1rem 0 0.3rem 0;
        padding-bottom: 0.2rem;
        border-bottom: 1px solid #2a2a4a;
    }

    /* Clickable row hover effect */
    .bank-row:hover {
        background-color: rgba(100,181,246,0.1) !important;
        cursor: pointer;
    }

    /* Status indicators */
    .status-dot {
        display: inline-block;
        width: 8px;
        height: 8px;
        border-radius: 50%;
        margin-right: 6px;
    }
    .status-connected { background: #00c853; }
    .status-disconnected { background: #ff1744; }
</style>
"""
