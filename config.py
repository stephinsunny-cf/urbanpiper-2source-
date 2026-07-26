"""
UrbanPiper Atlas - Configuration
=================================
For LOCAL use: fill in the values directly below.
For GITHUB ACTIONS: set these as repository secrets — the script reads them
from environment variables automatically.

GitHub Secrets to create:
  UP_EMAIL        → your Atlas login email
  UP_PASSWORD     → your Atlas login password
  REPORT_EMAILS   → comma-separated list e.g. a@x.com,b@x.com,c@x.com
"""

import os

# ── Credentials ───────────────────────────────────────────────────────────────
UP_EMAIL    = os.environ.get("UP_EMAIL",    "your-email@company.com")
UP_PASSWORD = os.environ.get("UP_PASSWORD", "your-password-here")

# ── Report Recipients ─────────────────────────────────────────────────────────
_emails_env = os.environ.get("REPORT_EMAILS", "ss23stephin2002@gmail.com,stephinsunny@karunya.edu.in,unofficialss23@gmail.com")
REPORT_EMAILS = [e.strip() for e in _emails_env.split(",") if e.strip()]

# ── Atlas URLs ────────────────────────────────────────────────────────────────
ATLAS_LOGIN_URL   = "https://atlas.urbanpiper.com/login"
ATLAS_GRAPHQL_URL = "https://atlas-backend.svc.urbanpiper.com/graphql"

# ── Report Definitions ────────────────────────────────────────────────────────
REPORTS = [
    {
        "id": "codex##1",
        "name": "Order Transactions"
    },
    {
        "id": "codex##4",
        "name": "Order Status Transitions"
    }
]

EXPORT_FORMAT = "csv"

PLATFORMS = [
    "supplynote", "growthfalcons", "zomato", "masalabox",
    "urbanpiper", "bitsila", "ownly", "magicpin",
    "swiggy", "meraki", "thrive", "tipplr"
]

ORDER_STATES = [
    "Placed", "Acknowledged", "Food Ready", "Dispatched",
    "Completed", "Cancelled", "Expired"
]

# Passing an empty list fetches for all locations.
LOCATION_IDS = []

SESSION_FILE = "up_session.json"
