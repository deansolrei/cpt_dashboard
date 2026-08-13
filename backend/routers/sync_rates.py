"""
routers/sync_rates.py
----------------------
On-demand trigger for sync_rates_from_sheets.py (lives at the project root,
one level above backend/). Powers the "Sync Rates Now" button on the
dashboard. The monthly cron job runs the same script independently of
this endpoint — this just lets someone force a refresh from the UI
without SSH'ing into the server.
"""

import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["Rate Sync"])

# backend/routers/sync_rates.py -> backend/routers -> backend -> project root
SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "sync_rates_from_sheets.py"


@router.post("/sync-rates/run")
def run_rate_sync():
    """
    Run sync_rates_from_sheets.py synchronously and return its result.
    Typically takes 5-20 seconds depending on Google Sheets API latency.
    Safe to call repeatedly — the underlying import endpoints upsert,
    they don't duplicate.
    """
    if not SCRIPT_PATH.exists():
        return {
            "status": "error",
            "message": f"Sync script not found at {SCRIPT_PATH}. "
                       "Expected it at the cpt_dashboard project root.",
        }

    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Sync script timed out after 180s."}

    return {
        "status": "ok" if result.returncode == 0 else "error",
        "exit_code": result.returncode,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-2000:],
    }
