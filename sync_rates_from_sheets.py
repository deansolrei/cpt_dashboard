#!/usr/bin/env python3
"""
sync_rates_from_sheets.py
--------------------------
Pulls CPT rate data directly from Google Sheets (the "Reimbursement Rates"
Master sheet and its SLAVE sync-output sheet) and loads it into the SolRate
Postgres database via the existing FastAPI import endpoints.

Replaces the manual "export CSV, drag into dashboard" workflow with an
automated pull. The manual upload boxes on the dashboard still work exactly
as before — this script is an additional path into the same endpoints, not
a replacement for them.

Sources of truth (per Dean, 2026-07-27):
  - SLAVE sheet, "intermediary_rates" tab -> POST /api/intermediaries/import
    (this tab already includes SBH direct/clinic-submit rates as one of the
    four "intermediary_name" values, alongside Alma, Headway, Grow Therapy)
  - Master sheet, "Medicare" tab, "PMHNP Expected (85%)" column
    -> POST /api/import-benchmark (one call per state block)

Run modes:
  Normal (cron / manual):
      python3 sync_rates_from_sheets.py
  Dry run (fetch + parse + print summary, no POST):
      python3 sync_rates_from_sheets.py --dry-run
  Only one half:
      python3 sync_rates_from_sheets.py --skip-medicare
      python3 sync_rates_from_sheets.py --skip-intermediary
  Log to a file (for cron):
      python3 sync_rates_from_sheets.py --log-file ~/cpt_dashboard/logs/rate_sync.log

Requires:
  pip install --break-system-packages google-api-python-client google-auth requests
"""

import argparse
import csv
import io
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ── Configuration ──────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent

SERVICE_ACCOUNT_FILE = SCRIPT_DIR / ".secrets" / "service_account.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

MASTER_SHEET_ID = "1QyfSpVlAba_epE1eehN5wlU1543AGWEpIzmsGPNlgXE"   # "Reimbursement Rates"
SLAVE_SHEET_ID  = "1eSuZtC9gm0vwNftf-sl8GrCTPMExWZ8-ifo8mLRZlpY"   # "Reimbursement Rates — Sync Output (SLAVE)"

INTERMEDIARY_RANGE = "intermediary_rates!A1:G5000"
MEDICARE_RANGE     = "Medicare!A1:H2000"

API_BASE = "http://localhost:8000/api"

VALID_STATES = {
    "AK", "AZ", "CO", "DC", "FL", "HI", "ID", "IA", "KS", "ME", "MD",
    "MN", "MT", "NE", "NV", "NH", "NM", "ND", "OR", "SD", "VT", "WA", "WY",
}

DEFAULT_LOG_PATH = SCRIPT_DIR / "logs" / "rate_sync.log"


# ── Logging setup ──────────────────────────────────────────────

def setup_logging(log_file: Optional[str]):
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        path = Path(log_file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )


log = logging.getLogger("rate_sync")


# ── Google Sheets access ──────────────────────────────────────

def get_sheets_service():
    if not SERVICE_ACCOUNT_FILE.exists():
        raise FileNotFoundError(
            f"Service account key not found at {SERVICE_ACCOUNT_FILE}. "
            "See deployment notes for how to place it."
        )
    creds = service_account.Credentials.from_service_account_file(
        str(SERVICE_ACCOUNT_FILE), scopes=SCOPES
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def fetch_values(service, spreadsheet_id: str, range_name: str) -> list[list[str]]:
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=range_name)
        .execute()
    )
    return result.get("values", [])


# ── Date normalization ─────────────────────────────────────────
# The SLAVE sheet has inconsistent date formatting across rows
# (some cells "2026-07-04", others "7/4/26"). Normalize everything
# to YYYY-MM-DD before it reaches Postgres, rather than trusting
# an ambiguous raw string to parse correctly downstream.

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%y", "%m/%d/%Y")


def normalize_date(raw: str) -> Optional[str]:
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    log.warning("Could not parse date %r — leaving blank (import endpoint will default it)", raw)
    return None


# ── Intermediary / SBH direct rates ────────────────────────────

def build_intermediary_csv(rows: list[list[str]]) -> tuple[bytes, int]:
    """
    Convert the raw SLAVE intermediary_rates sheet rows into the exact CSV
    format /api/intermediaries/import expects:
      intermediary_name, payer_name, cpt_code, state, allowed_amount,
      effective_date, provider
    """
    if not rows:
        raise ValueError("intermediary_rates tab returned no rows")

    header = [h.strip().lower() for h in rows[0]]
    expected = ["intermediary_name", "payer_name", "cpt_code", "state",
                "allowed_amount", "effective_date", "provider"]
    if header[:len(expected)] != expected:
        raise ValueError(
            f"intermediary_rates header changed — expected {expected}, got {header}. "
            "Sheet layout may have changed; update this script before proceeding."
        )

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(expected)

    row_count = 0
    for raw_row in rows[1:]:
        if not raw_row or not (raw_row[0] or "").strip():
            continue
        padded = raw_row + [""] * (7 - len(raw_row))
        intermediary_name, payer_name, cpt_code, state, allowed_amount, effective_date, provider = padded[:7]

        amount_clean = allowed_amount.replace("$", "").replace(",", "").strip()
        if not amount_clean:
            continue

        writer.writerow([
            intermediary_name.strip(),
            payer_name.strip(),
            cpt_code.strip(),
            state.strip().upper(),
            amount_clean,
            normalize_date(effective_date) or "",
            provider.strip().upper(),
        ])
        row_count += 1

    return out.getvalue().encode("utf-8"), row_count


def push_intermediary_rates(csv_bytes: bytes, dry_run: bool) -> dict:
    if dry_run:
        log.info("[dry-run] Would POST intermediary_rates CSV (%d bytes)", len(csv_bytes))
        return {"status": "dry-run"}

    resp = requests.post(
        f"{API_BASE}/intermediaries/import",
        files={"file": ("intermediary_rates_sync.csv", csv_bytes, "text/csv")},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


# ── Medicare benchmark rates ────────────────────────────────────

def parse_medicare_by_state(rows: list[list[str]]) -> dict[str, list[dict]]:
    """
    The Medicare tab has no consistent blank-row separators between state
    blocks, so we can't rely on that. Instead: a row is a "state header" if
    column A is a bare 2-letter code in VALID_STATES and column B (short
    description) is empty. Every row after that, until the next state
    header, belongs to that state.
    """
    if not rows:
        raise ValueError("Medicare tab returned no rows")

    header = [h.strip() for h in rows[0]]
    try:
        code_col = header.index("HCPCS Code")
        pmhnp_col = header.index("PMHNP Expected (85%)")
    except ValueError as e:
        raise ValueError(
            f"Medicare tab header changed — could not find expected columns in {header}. "
            "Update this script before proceeding."
        ) from e

    by_state: dict[str, list[dict]] = {}
    current_state = None

    for raw_row in rows[1:]:
        if not raw_row:
            continue
        padded = raw_row + [""] * (max(code_col, pmhnp_col) + 1 - len(raw_row))
        col_a = (padded[0] or "").strip().upper()
        col_b = (padded[1] or "").strip() if len(padded) > 1 else ""

        if col_a in VALID_STATES and not col_b:
            current_state = col_a
            by_state.setdefault(current_state, [])
            continue

        if current_state is None:
            continue  # rows before the first recognized state header

        cpt_code = (padded[code_col] or "").strip()
        amount_raw = (padded[pmhnp_col] or "").replace("$", "").replace(",", "").strip()
        if not cpt_code or not amount_raw:
            continue
        try:
            amount = float(amount_raw)
        except ValueError:
            log.warning("Skipping unparsable Medicare rate for %s %s: %r",
                        current_state, cpt_code, amount_raw)
            continue

        by_state[current_state].append({"cpt_code": cpt_code, "allowed_amount": amount})

    return by_state


def push_medicare_rates(by_state: dict[str, list[dict]], year: int, dry_run: bool) -> dict:
    source_name = f"Medicare {year}"
    summary = {"states_processed": 0, "total_rates": 0, "errors": []}

    for locality, rates in by_state.items():
        if not rates:
            continue
        payload = {
            "source_name": source_name,
            "locality": locality,
            "effective_year": year,
            "rates": rates,
        }
        if dry_run:
            log.info("[dry-run] Would POST %d Medicare rate(s) for %s", len(rates), locality)
            summary["states_processed"] += 1
            summary["total_rates"] += len(rates)
            continue

        try:
            resp = requests.post(f"{API_BASE}/import-benchmark", json=payload, timeout=60)
            resp.raise_for_status()
            summary["states_processed"] += 1
            summary["total_rates"] += len(rates)
        except requests.RequestException as e:
            log.error("Failed to import Medicare rates for %s: %s", locality, e)
            summary["errors"].append(f"{locality}: {e}")

    return summary


# ── Main ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                         help="Fetch and parse but do not POST anything")
    parser.add_argument("--skip-intermediary", action="store_true")
    parser.add_argument("--skip-medicare", action="store_true")
    parser.add_argument("--year", type=int, default=date.today().year,
                         help="Medicare effective year (default: current year)")
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_PATH))
    args = parser.parse_args()

    setup_logging(args.log_file)
    log.info("=== Rate sync started %s ===", datetime.now().isoformat(timespec="seconds"))

    try:
        service = get_sheets_service()
    except Exception as e:
        log.error("Could not authenticate to Google Sheets: %s", e)
        sys.exit(1)

    exit_code = 0

    if not args.skip_intermediary:
        try:
            log.info("Fetching SLAVE intermediary_rates tab...")
            rows = fetch_values(service, SLAVE_SHEET_ID, INTERMEDIARY_RANGE)
            csv_bytes, row_count = build_intermediary_csv(rows)
            log.info("Parsed %d intermediary/direct rate rows", row_count)
            result = push_intermediary_rates(csv_bytes, args.dry_run)
            log.info("Intermediary import result: %s", result)
        except Exception as e:
            log.error("Intermediary rate sync failed: %s", e)
            exit_code = 1

    if not args.skip_medicare:
        try:
            log.info("Fetching Master Medicare tab...")
            rows = fetch_values(service, MASTER_SHEET_ID, MEDICARE_RANGE)
            by_state = parse_medicare_by_state(rows)
            log.info("Parsed Medicare rates for %d state(s)", len(by_state))
            result = push_medicare_rates(by_state, args.year, args.dry_run)
            log.info("Medicare import result: %s", result)
            if result.get("errors"):
                exit_code = 1
        except Exception as e:
            log.error("Medicare rate sync failed: %s", e)
            exit_code = 1

    log.info("=== Rate sync finished (exit code %d) ===", exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
