"""Custom CSS for the bank valuation dashboard — clean white/light gray theme."""

CUSTOM_CSS = """
<style>
    /* Light theme base */
    .stApp {
        background-color: #ffffff;
    }

    /* Header styling */
    .dashboard-header {
        background: #f8f9fa;
        padding: 1.5rem 2rem;
        border-radius: 8px;
        margin-bottom: 1.5rem;
        border: 1px solid #e0e0e0;
    }
    .dashboard-header h1 {
        color: #1a1a1a;
        margin: 0;
        font-size: 1.8rem;
        font-weight: 700;
    }
    .dashboard-header p {
        color: #666;
        margin: 0.3rem 0 0 0;
        font-size: 0.9rem;
    }

    /* Metric cards */
    .metric-card {
        background: #f8f9fa;
        border: 1px solid #e0e0e0;
        border-radius: 8px;
        padding: 1rem;
        text-align: center;
    }
    .metric-card .label {
        color: #666;
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .metric-card .value {
        color: #1a1a1a;
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

    /* Table enhancements */
    .dataframe {
        font-size: 0.85rem !important;
    }
    .dataframe th {
        background-color: #f5f5f5 !important;
        color: #555 !important;
        font-weight: 600 !important;
        text-transform: uppercase;
        font-size: 0.75rem !important;
        letter-spacing: 0.03em;
        border-bottom: 2px solid #ddd !important;
    }
    .dataframe td {
        color: #1a1a1a !important;
        border-bottom: 1px solid #eee !important;
    }

    /* Sidebar styling */
    section[data-testid="stSidebar"] {
        background-color: #f8f9fa;
        border-right: 1px solid #e0e0e0;
    }

    /* Category headers */
    .category-header {
        color: #1a73e8;
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin: 1rem 0 0.3rem 0;
        padding-bottom: 0.2rem;
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
        width: 8px;
        height: 8px;
        border-radius: 50%;
        margin-right: 6px;
    }
    .status-connected { background: #2e7d32; }
    .status-disconnected { background: #c62828; }
</style>
"""
