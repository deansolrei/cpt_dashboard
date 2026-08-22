"""
routers/channel_overrides.py
------------------------------
Per-channel plan-name substitutions (see sql/32_channel_plan_overrides.sql
for the full explanation of what this is and why it's separate from
intermediary_rates). Small table -- only grows when a real case is
confirmed, not populated speculatively.

CSV format (from the Master Sheet's ChannelPlanOverrides tab):
  home_plan, channel, effective_plan, notes, active

channel must exactly match an intermediaries.name value:
  SBH / Headway / Alma / Grow Therapy
"""

import csv
import io

from fastapi import APIRouter, UploadFile, File
from ..database import get_db

router = APIRouter(prefix="/api", tags=["Channel Plan Overrides"])

VALID_CHANNELS = {"SBH", "Headway", "Alma", "Grow Therapy"}


# ── List all overrides ──────────────────────────────────────────

@router.get("/channel-overrides")
def list_overrides():
    with get_db() as cur:
        cur.execute(
            """
            SELECT override_id, home_plan, channel, effective_plan,
                   notes, active, created_at, updated_at
            FROM channel_plan_overrides
            ORDER BY home_plan, channel
            """
        )
        return cur.fetchall()


# ── CSV import ───────────────────────────────────────────────────

@router.post("/channel-overrides/import")
async def import_overrides(file: UploadFile = File(...)):
    """
    Upload the Master Sheet's ChannelPlanOverrides tab CSV.
    Columns: home_plan, channel, effective_plan, notes, active

    home_plan and effective_plan should be canonical payer names --
    the same strings CARRIER_MAP resolves to and intermediary_rates
    stores under payer_name. channel must exactly match an
    intermediaries.name value (SBH / Headway / Alma / Grow Therapy) --
    rows with any other channel value are rejected, not silently
    dropped, since a typo'd channel name would otherwise silently
    never match anything at lookup time.

    Safe to re-upload -- uses INSERT ON CONFLICT DO UPDATE, keyed on
    (home_plan, channel).
    """
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    imported = 0
    skipped = 0
    errors = []

    with get_db() as cur:
        dict_reader = csv.DictReader(io.StringIO(text))

        for i, row in enumerate(dict_reader, start=2):
            # Skip comment/instruction rows
            first_val = (list(row.values())[0] or "").strip()
            if first_val.startswith("#"):
                skipped += 1
                continue

            home_plan = (row.get("home_plan") or "").strip()
            channel = (row.get("channel") or "").strip()
            effective_plan = (row.get("effective_plan") or "").strip()

            if not home_plan or not channel or not effective_plan:
                skipped += 1
                continue

            if channel not in VALID_CHANNELS:
                errors.append(
                    f"Row {i}: unknown channel '{channel}' -- must be exactly "
                    f"one of {sorted(VALID_CHANNELS)} -- skipped"
                )
                skipped += 1
                continue

            notes = (row.get("notes") or "").strip()
            active_raw = (row.get("active") or "TRUE").strip().upper()
            active = active_raw not in ("FALSE", "0", "NO", "N")

            try:
                cur.execute(
                    """
                    INSERT INTO channel_plan_overrides
                        (home_plan, channel, effective_plan, notes, active, updated_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (home_plan, channel)
                    DO UPDATE SET
                        effective_plan = EXCLUDED.effective_plan,
                        notes          = EXCLUDED.notes,
                        active         = EXCLUDED.active,
                        updated_at     = NOW()
                    """,
                    (home_plan, channel, effective_plan, notes, active),
                )
                imported += 1
            except Exception as e:
                errors.append(f"Row {i}: DB error -- {str(e)}")
                skipped += 1

    return {"imported": imported, "skipped": skipped, "errors": errors}
