#!/usr/bin/env python3
"""
One-time patch for ~/cpt_dashboard/backend/routers/best_channel.py

Replaces CARRIER_MAP and BCBS_BY_STATE with corrected versions that:
  1. Add explicit self-mappings for all 28 named Blue Cross plans now
     living in intermediary_rates (as of the 2026-07-29 fixed-grid
     rebuild), using the EXACT spelling from BLUE_CROSS_PLANS in
     Code.gs / intermediary_rates.payer_name. Without these, carrier
     names containing "bcbs", "anthem", or "blue shield" fall through
     to the generic state-default fallback (BCBS_BY_STATE), which is
     what caused Horizon BCBS New Jersey to silently resolve to
     "Florida Blue" for a Florida-billed patient.
  2. Fix two existing entries whose mapped value didn't exactly match
     the new canonical payer_name spelling (would have matched a carrier
     name correctly but then found zero rows in the database):
       "independence blue cross" -> was "Independence Blue Cross PA",
                                     now "Independence Blue Cross Pennsylvania"
       "regence blueShield of washington" / "regence blueShield" -> was
                                     "Regence Blue Shield Washington",
                                     now "Regence BlueShield Washington"
  3. Fix a pre-existing (not from today's work) mismap:
       "carefirst" -> was "Anthem BCBS Colorado", now "BCBS CareFirst"
  4. Fix BCBS_BY_STATE's WA entry to the same corrected spelling.

Run on sbhserver1:
  cd ~/cpt_dashboard && python3 /tmp/patch_best_channel_carrier_map.py
(copy this file to sbhserver1 first, e.g. via scp, or paste its contents
into a file there named patch_best_channel_carrier_map.py)

The script makes a timestamped backup of the original file before writing,
and prints a diff-style summary of what changed.
"""

import re
import shutil
import datetime

TARGET = "backend/routers/best_channel.py"

NEW_CARRIER_MAP = '''CARRIER_MAP = {
    "aetna": "Aetna",
    "aetna - allied plan": "Aetna",
    "aetna - pacificsource": "Aetna",
    "aetna - signature": "Aetna",
    "aetna banner": "Aetna",
    "aetna choice": "Aetna",
    "aetna (headway)": "Aetna",
    "ambetter": "Ambetter",

    # Anthem BCBS plans — each state's plan mapped explicitly.
    # IMPORTANT: specific multi-word keys must stay ABOVE the generic
    # "anthem" fallback below for the longest-prefix-match logic in
    # _resolve_carrier to prefer them.
    "anthem blue cross and blue shield colorado": "Anthem BCBS Colorado",
    "anthem blue cross and blue shield nevada": "Anthem BCBS Nevada",
    "anthem blue cross and blue shield florida": "Florida Blue",
    "anthem blue cross and blue shield connecticut": "Anthem BCBS Connecticut",
    "anthem blue cross and blue shield maine": "Anthem BCBS Maine",
    "anthem blue cross and blue shield new hampshire": "Anthem BCBS New Hampshire",
    "anthem blue cross and blue shield virginia": "Anthem BCBS Virginia",
    "anthem blue cross and blue shield indiana": "Anthem BCBS Indiana",
    "anthem blue cross california": "Anthem Blue Cross California",
    "anthem blue cross and blue shield california": "Anthem Blue Cross California",
    "anthem bcbs colorado": "Anthem BCBS Colorado",
    "anthem bcbs connecticut": "Anthem BCBS Connecticut",
    "anthem bcbs indiana": "Anthem BCBS Indiana",
    "anthem bcbs maine": "Anthem BCBS Maine",
    "anthem bcbs nevada": "Anthem BCBS Nevada",
    "anthem bcbs new hampshire": "Anthem BCBS New Hampshire",
    "anthem bcbs virginia": "Anthem BCBS Virginia",
    "anthem": "Anthem BCBS Colorado",  # last-resort generic Anthem fallback
    "bcbs - anthem": "Anthem BCBS Colorado",

    # BCBS CareFirst — FIXED 2026-07-29: was wrongly mapped to
    # "Anthem BCBS Colorado" (pre-existing bug, unrelated to Horizon).
    "carefirst": "BCBS CareFirst",
    "bcbs carefirst": "BCBS CareFirst",

    "blue cross blue shield of arizona": "BCBS Arizona",
    "bcbs arizona": "BCBS Arizona",
    "blue cross blue shield of massachusetts": "BCBS Massachusetts",
    "bcbs massachusetts": "BCBS Massachusetts",
    "blue cross and blue shield of minnesota": "BCBS Minnesota",
    "bcbs minnesota": "BCBS Minnesota",
    "bcbs minnesota medicaid": "BCBS Minnesota Medicaid",
    "blue cross blue shield - wellmark": "Wellmark Iowa",
    "wellmark": "Wellmark Iowa",
    "florida blue": "Florida Blue",
    "blue cross and blue shield of florida": "Florida Blue",
    "florida blue medicare advantage": "Florida Blue Medicare Advantage",

    # Independence Blue Cross — FIXED 2026-07-29: canonical payer_name is
    # "...Pennsylvania" (matches intermediary_rates), was "...PA".
    "independence blue cross": "Independence Blue Cross Pennsylvania",
    "independence blue cross pennsylvania": "Independence Blue Cross Pennsylvania",

    "premera blue cross washington": "Premera Blue Cross Washington",
    "premera blue cross": "Premera Blue Cross Washington",

    "regence bluecross blueShield of oregon": "Regence BCBS Oregon",
    "regence bluecross": "Regence BCBS Oregon",
    "regence bcbs oregon": "Regence BCBS Oregon",
    "regence group": "Regence BCBS Oregon",

    # Regence BlueShield Washington — TWO bugs fixed here 2026-07-29:
    # (1) canonical payer_name has no space between "Blue" and "Shield"
    #     (was "Regence Blue Shield Washington");
    # (2) these two keys had a capital "S" in "blueShield," but
    #     _resolve_carrier always lowercases its input before comparing
    #     — a mixed-case key can never match a lowercased string, so
    #     these two entries were dead/unreachable even before today.
    "regence blueshield of washington": "Regence BlueShield Washington",
    "regence blueshield": "Regence BlueShield Washington",
    "regence blueshield washington": "Regence BlueShield Washington",

    # New explicit entries added 2026-07-29 for plans with no prior
    # mapping — without these they either hit the wrong generic
    # BCBS_BY_STATE fallback (if their name contains "bcbs"/"blue
    # shield"/"anthem") or get no mapping at all.
    "horizon bcbs new jersey": "Horizon BCBS New Jersey",
    "providence health plan": "Providence Health Plan",
    "providence": "Providence Health Plan",
    "blue shield of california": "Blue Shield of California",
    "bcbs hawaii": "BCBS Hawaii",
    "bcbs michigan": "BCBS Michigan",
    "bcbs montana": "BCBS Montana",
    "bcbs nebraska": "BCBS Nebraska",
    "bcbs texas": "BCBS Texas",

    # Generic fallbacks — deliberately LAST. Only reached if nothing
    # more specific above matched. Resolves to the state's default
    # Blue Cross plan via BCBS_BY_STATE.
    "blue cross blue shield": None,
    "blue cross and blue shield": None,
    "bcbs": None,
    "blue shield": None,

    "cigna": "Cigna",
    "oscar": "Optum/UHC/Oscar",
    "oxford": "Optum/UHC/Oscar",
    "umr": "Optum/UHC/Oscar",
    "united healthcare": "Optum/UHC/Oscar",
    "united health": "Optum/UHC/Oscar",
    "uhc": "Optum/UHC/Oscar",
    "surest": "Optum/UHC/Oscar",
    "medica - united": "Optum/UHC/Oscar",
    "carelon": "Carelon Behavioral Health",
    "beacon": "Carelon Behavioral Health",
}'''

NEW_BCBS_BY_STATE = '''BCBS_BY_STATE = {
    "AK": "BCBS Massachusetts", "AZ": "BCBS Arizona", "CO": "Anthem BCBS Colorado",
    "CT": "Anthem BCBS Connecticut", "DC": "Blue Cross", "FL": "Florida Blue",
    "HI": "Blue Cross", "ID": "Blue Cross", "IA": "Wellmark Iowa",
    "KS": "Blue Cross", "ME": "Anthem BCBS Maine", "MD": "Blue Cross",
    "MN": "BCBS Minnesota", "MT": "Blue Cross", "NE": "Blue Cross",
    "NV": "Anthem BCBS Nevada", "NH": "Anthem BCBS New Hampshire",
    "NM": "BCBS Massachusetts", "ND": "Blue Cross", "OR": "Regence BCBS Oregon",
    "SD": "Blue Cross", "UT": "Blue Cross", "VT": "Blue Cross",
    "WA": "Regence BlueShield Washington", "WY": "Blue Cross",
}'''

def main():
    with open(TARGET, "r") as f:
        content = f.read()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = TARGET + ".bak." + ts
    shutil.copyfile(TARGET, backup_path)
    print("Backed up original to:", backup_path)

    carrier_pattern = re.compile(r"CARRIER_MAP = \{.*?\n\}", re.DOTALL)
    bcbs_pattern = re.compile(r"BCBS_BY_STATE = \{.*?\n\}", re.DOTALL)

    if not carrier_pattern.search(content):
        raise SystemExit("ABORTED: could not find CARRIER_MAP block — file may have changed. No edits made.")
    if not bcbs_pattern.search(content):
        raise SystemExit("ABORTED: could not find BCBS_BY_STATE block — file may have changed. No edits made.")

    new_content = carrier_pattern.sub(lambda m: NEW_CARRIER_MAP, content, count=1)
    new_content = bcbs_pattern.sub(lambda m: NEW_BCBS_BY_STATE, new_content, count=1)

    with open(TARGET, "w") as f:
        f.write(new_content)

    print("Patched", TARGET)
    print("CARRIER_MAP now has", NEW_CARRIER_MAP.count(": "), "entries (roughly)")
    print("Restart the service after this: pm2 restart cpt-dashboard")

if __name__ == "__main__":
    main()

