from fastapi import APIRouter, Query
from ..database import get_db

router = APIRouter(prefix="/api", tags=["Best Channel"])

CARRIER_MAP = {
    "aetna": "Aetna",
    "aetna - allied plan": "Aetna",
    "aetna - pacificsource": "Aetna",
    "aetna - signature": "Aetna",
    "aetna banner": "Aetna",
    "aetna choice": "Aetna",
    "aetna (headway)": "Aetna",
    "ambetter": "Ambetter",
    # FIXED (2026-08-21): rewritten to match the actual "Anthem BCBS ___"
    # abbreviated format SolBoard sends (confirmed against real appointment
    # data) — the old keys were spelled out ("anthem blue cross and blue
    # shield colorado") and never matched anything in practice. Every one
    # of these six was silently falling through to the bare "anthem"
    # catch-all below, which hardcoded Colorado regardless of the patient's
    # actual plan. That catch-all (plus "bcbs - anthem" and "carefirst",
    # which is a different company entirely, unrelated to Anthem) has been
    # removed — an unrecognized Anthem-family carrier now correctly falls
    # through to "not mapped" instead of guessing.
    #
    # EXTENDED (2026-08-21, round 2): both the abbreviated AND the fully
    # spelled-out "Anthem Blue Cross and Blue Shield ___" form are kept for
    # every state below. Real input uses either — Headway's own naming
    # convention is always spelled-out, and it also sneaks into Tebra
    # inconsistently alongside the abbreviated form. Before this round, a
    # spelled-out name fell through to the generic "blue cross and blue
    # shield" substring match further down and silently returned the WRONG
    # state's fallback — the exact same failure class as the original bug,
    # just triggered by a different input string. Caught by testing every
    # name from Headway's and Alma's actual naming conventions against the
    # live resolver, not just the abbreviated forms this fix started with.
    "anthem bcbs colorado": "Anthem BCBS Colorado",
    "anthem blue cross and blue shield colorado": "Anthem BCBS Colorado",
    "anthem bcbs nevada": "Anthem BCBS Nevada",
    "anthem blue cross and blue shield nevada": "Anthem BCBS Nevada",
    "anthem bcbs florida": "Florida Blue",
    "anthem blue cross and blue shield florida": "Florida Blue",
    "anthem bcbs connecticut": "Anthem BCBS Connecticut",
    "anthem blue cross and blue shield connecticut": "Anthem BCBS Connecticut",
    "anthem bcbs maine": "Anthem BCBS Maine",
    "anthem blue cross and blue shield maine": "Anthem BCBS Maine",
    "anthem bcbs new hampshire": "Anthem BCBS New Hampshire",
    "anthem blue cross and blue shield new hampshire": "Anthem BCBS New Hampshire",
    "anthem bcbs new york": "Anthem BCBS New York",
    "anthem blue cross and blue shield new york": "Anthem BCBS New York",
    "anthem blue cross blue shield - new york": "Anthem BCBS New York",  # Tebra's own inconsistent variant (Dean flagged this one himself)
    "blue cross blue shield of arizona": "BCBS Arizona",
    "blue cross blue shield of massachusetts": "BCBS Massachusetts",
    "blue cross and blue shield of minnesota": "BCBS Minnesota",
    "blue cross blue shield - wellmark": "Wellmark Iowa",
    "wellmark": "Wellmark Iowa",
    "florida blue": "Florida Blue",
    "blue cross and blue shield of florida": "Florida Blue",
    "independence blue cross": "Independence Blue Cross PA",
    # NEW (2026-08-21): was falling through to the generic Blue Cross
    # guess, same failure class as the Anthem entries above.
    "horizon blue cross and blue shield of new jersey": "Horizon BCBS New Jersey",
    "horizon bcbs new jersey": "Horizon BCBS New Jersey",
    "premera blue cross washington": "Premera Blue Cross Washington",
    # FIXED (2026-08-21): these three keys had a stray uppercase "S" in
    # "blueShield" that silently never matched anything, since input is
    # always lowercased before comparison but these keys weren't. Every
    # Washington Regence request was falling all the way through to
    # "not mapped" — worse than a wrong guess, just silently no answer.
    "regence bluecross blueshield of oregon": "Regence BCBS Oregon",
    "regence bluecross": "Regence BCBS Oregon",
    "regence blueshield of washington": "Regence Blue Shield Washington",
    "regence blueshield": "Regence Blue Shield Washington",
    "regence group": "Regence BCBS Oregon",
    "blue cross blue shield": None,
    "blue cross and blue shield": None,
    "bcbs": None,
    "blue shield": None,
    "cigna": "Cigna",
    "oscar": "Optum/UHC/Oscar",
    "oxford": "Optum/UHC/Oscar",
    "umr": "Optum/UHC/Oscar",
    # NEW (2026-08-21): Alma's own bare naming for these three was
    # completely unresolved before. Dean confirmed all six of Alma's Optum-
    # family names (Optum, United, UnitedHealthcare, UMR, Oscar, Oxford)
    # are one single rate, so these are safe to add as plain synonyms.
    "optum": "Optum/UHC/Oscar",
    "united": "Optum/UHC/Oscar",
    "unitedhealthcare": "Optum/UHC/Oscar",
    "united healthcare": "Optum/UHC/Oscar",
    "united health": "Optum/UHC/Oscar",
    "uhc": "Optum/UHC/Oscar",
    "surest": "Optum/UHC/Oscar",
    "medica - united": "Optum/UHC/Oscar",
    "carelon": "Carelon Behavioral Health",
    "beacon": "Carelon Behavioral Health",
}

BCBS_BY_STATE = {
    "AK": "BCBS Massachusetts", "AZ": "BCBS Arizona", "CO": "Anthem BCBS Colorado",
    "CT": "Anthem BCBS Connecticut", "DC": "Blue Cross", "FL": "Florida Blue",
    "HI": "Blue Cross", "ID": "Blue Cross", "IA": "Wellmark Iowa",
    "KS": "Blue Cross", "ME": "Anthem BCBS Maine", "MD": "Blue Cross",
    "MN": "BCBS Minnesota", "MT": "Blue Cross", "NE": "Blue Cross",
    "NV": "Anthem BCBS Nevada", "NH": "Anthem BCBS New Hampshire",
    "NM": "BCBS Massachusetts", "ND": "Blue Cross", "OR": "Regence BCBS Oregon",
    "SD": "Blue Cross", "UT": "Blue Cross", "VT": "Blue Cross",
    "WA": "Regence Blue Shield Washington", "WY": "Blue Cross",
}

# Map CRB provID to CPT Dashboard provider tag
PROV_MAP = {"jodene": "JJ", "katie": "KR", "lori": "LK"}


def _resolve_carrier(carrier_name, state):
    if not carrier_name:
        return None
    n = carrier_name.lower().strip()
    if "cash" in n or "self pay" in n or "self-pay" in n:
        return None
    if n in CARRIER_MAP:
        r = CARRIER_MAP[n]
        return BCBS_BY_STATE.get(state.upper(), "Blue Cross") if r is None else r
    best_key, best_len = None, 0
    for key, val in CARRIER_MAP.items():
        if n.startswith(key) and len(key) > best_len:
            best_key, best_len = key, len(key)
    if best_key:
        r = CARRIER_MAP[best_key]
        return BCBS_BY_STATE.get(state.upper(), "Blue Cross") if r is None else r
    for key, val in sorted(CARRIER_MAP.items(), key=lambda x: -len(x[0])):
        if key in n:
            r = val
            return BCBS_BY_STATE.get(state.upper(), "Blue Cross") if r is None else r
    return None


@router.get("/best-channel")
def get_best_channel(
    carrier: str = Query(...),
    state: str = Query(default="FL"),
    cpts: str = Query(default="99214"),
    provider: str = Query(default=""),
):
    state_upper = (state or "FL").upper()
    cpt_list = [c.strip() for c in cpts.split(",") if c.strip()] or ["99214"]
    # Remove duplicates while preserving order
    seen = set()
    cpt_list = [c for c in cpt_list if not (c in seen or seen.add(c))]

    n_lower = carrier.lower()
    is_blue_cross = any(x in n_lower for x in [
        "blue cross", "blue shield", "bcbs", "anthem", "carefirst",
        "premera", "regence", "wellmark", "independence blue", "florida blue"
    ])
    canonical = _resolve_carrier(carrier, state_upper)

    if not canonical:
        if is_blue_cross:
            return {
                "canonical_payer": None, "state": state_upper, "cpt_results": [],
                "overall_best_channel": "Clinic Submit",
                "mapped": False, "raw_carrier": carrier,
                "default_reason": "Blue Cross plan — no intermediary rates on file. Submit directly.",
                "show_default": True,
            }
        return {"canonical_payer": None, "state": state_upper, "cpt_results": [],
                "overall_best_channel": None, "mapped": False, "raw_carrier": carrier,
                "note": "Carrier not mapped — check CPT Dashboard"}

    # Map provider param to tag (JJ / KR / LK / empty)
    prov_tag = PROV_MAP.get(provider.lower().strip(), provider.upper().strip())

    # Provider filter clause:
    # - If prov_tag is set: return rows where provider matches OR provider is NULL/empty (COMMON rates)
    # - If no prov_tag: return all rows (no filter)
    if prov_tag:
        prov_filter = "AND (ir.provider IS NULL OR ir.provider = '' OR ir.provider = %s)"
    else:
        prov_filter = "AND (1=1 OR %s IS NULL)"  # always true, param ignored

    # Per-channel plan overrides (2026-08-21) — see
    # sql/32_channel_plan_overrides.sql. Some channels bill a patient's
    # real plan under a DIFFERENT, separately-contracted plan due to
    # interstate/network reciprocity (e.g. Alma has no direct NY Anthem
    # contract, so it bills those patients under its Massachusetts BCBS
    # contract instead) — a genuinely different rate card, not a spelling
    # variant of the same one. This is NOT what CARRIER_MAP handles above;
    # CARRIER_MAP already resolved `canonical` to one true plan name by
    # this point, and this step runs strictly after that, only ever
    # redirecting individual channels' lookups away from it. Absent any
    # override row for this plan, every channel just uses `canonical`
    # exactly as before this change — existing behavior is unaffected for
    # the overwhelming majority of plans, which have no override at all.
    with get_db() as cur:
        cur.execute(
            "SELECT channel, effective_plan FROM channel_plan_overrides "
            "WHERE home_plan = %s AND active = TRUE",
            (canonical,),
        )
        overrides_by_channel = {r["channel"]: r["effective_plan"] for r in cur.fetchall()}

    payer_sbh = overrides_by_channel.get("SBH", canonical)
    payer_headway = overrides_by_channel.get("Headway", canonical)
    payer_alma = overrides_by_channel.get("Alma", canonical)
    payer_grow = overrides_by_channel.get("Grow Therapy", canonical)

    with get_db() as cur:
        cur.execute(f"""
            WITH
            sbh AS (
                SELECT ir.cpt_code, MAX(ir.allowed_amount) AS clinic_rate
                FROM intermediary_rates ir
                JOIN intermediaries i ON ir.intermediary_id = i.intermediary_id
                WHERE i.name = 'SBH' AND ir.payer_name = %s AND ir.state = %s
                {prov_filter}
                GROUP BY ir.cpt_code
            ),
            headway AS (
                SELECT ir.cpt_code, MAX(ir.allowed_amount) AS headway_rate
                FROM intermediary_rates ir
                JOIN intermediaries i ON ir.intermediary_id = i.intermediary_id
                WHERE i.name = 'Headway' AND ir.payer_name = %s AND ir.state = %s
                {prov_filter}
                GROUP BY ir.cpt_code
            ),
            alma AS (
                SELECT ir.cpt_code, MAX(ir.allowed_amount) AS alma_rate
                FROM intermediary_rates ir
                JOIN intermediaries i ON ir.intermediary_id = i.intermediary_id
                WHERE i.name = 'Alma' AND ir.payer_name = %s AND ir.state = %s
                {prov_filter}
                GROUP BY ir.cpt_code
            ),
            grow AS (
                SELECT ir.cpt_code, MAX(ir.allowed_amount) AS grow_rate
                FROM intermediary_rates ir
                JOIN intermediaries i ON ir.intermediary_id = i.intermediary_id
                WHERE i.name = 'Grow Therapy' AND ir.payer_name = %s AND ir.state = %s
                {prov_filter}
                GROUP BY ir.cpt_code
            ),
            medicare AS (
                SELECT cpt_code, allowed_amount AS medicare_rate
                FROM benchmark_fee_schedule
                WHERE source_name = 'Medicare 2026' AND effective_year = 2026 AND locality = %s
            )
            SELECT
                COALESCE(s.cpt_code, h.cpt_code, a.cpt_code, g.cpt_code) AS cpt_code,
                s.clinic_rate, h.headway_rate, a.alma_rate, g.grow_rate, m.medicare_rate
            FROM sbh s
            FULL JOIN headway h USING (cpt_code)
            FULL JOIN alma    a USING (cpt_code)
            FULL JOIN grow    g USING (cpt_code)
            FULL JOIN medicare m USING (cpt_code)
            WHERE COALESCE(s.cpt_code, h.cpt_code, a.cpt_code, g.cpt_code) = ANY(%s)
            ORDER BY cpt_code
        """, (
            payer_sbh, state_upper, prov_tag,      # sbh
            payer_headway, state_upper, prov_tag,  # headway
            payer_alma, state_upper, prov_tag,     # alma
            payer_grow, state_upper, prov_tag,     # grow
            state_upper,                        # medicare
            cpt_list,
        ))
        rows = cur.fetchall()

    if not rows and is_blue_cross:
        return {
            "canonical_payer": canonical, "state": state_upper, "cpt_results": [],
            "overall_best_channel": "Clinic Submit",
            "mapped": True, "raw_carrier": carrier,
            "default_reason": "No intermediary rates on file for this Blue Cross plan in " + state_upper + ". Submit directly via Clinic Submit.",
            "show_default": True,
        }
    if not rows:
        return {
            "canonical_payer": canonical, "state": state_upper, "cpt_results": [],
            "overall_best_channel": "Clinic Submit",
            "mapped": True, "raw_carrier": carrier,
            "default_reason": "No rates on file for " + (canonical or carrier) + " in " + state_upper + ".",
            "show_default": True,
        }

    # Display order for CPT codes on a claim:
    #   1) Primary evaluation/session codes (only one per appointment) - always first
    #   2) Add-on psychotherapy codes - always second
    #   3) 90785 (interactive complexity add-on) - always last
    #   4) Anything else not in the lists above - falls in the middle
    PRIMARY_CODES = {"99214", "99215", "99204",
                     "99205", "98002", "98003", "98006", "98007"}
    ADDON_CODES = {"90833", "90836", "90838"}
    LAST_CODES = {"90785"}

    def _cpt_sort_key(cpt_code):
        if cpt_code in PRIMARY_CODES:
            return (0, cpt_code)
        if cpt_code in ADDON_CODES:
            return (1, cpt_code)
        if cpt_code in LAST_CODES:
            return (3, cpt_code)
        return (2, cpt_code)

    rows = sorted(rows, key=lambda r: _cpt_sort_key(r["cpt_code"]))

    CHANNELS = ["Clinic Submit", "Headway", "Alma", "Grow Therapy"]
    cpt_results, channel_votes = [], {}
    # int, not 0.0 -- rates are Decimal (NUMERIC columns via psycopg2), and Decimal + float raises TypeError
    channel_totals = {c: 0 for c in CHANNELS}
    channel_covers_all_cpts = {c: True for c in CHANNELS}

    for row in rows:
        rates = {
            "Clinic Submit": row["clinic_rate"],
            "Headway":       row["headway_rate"],
            "Alma":          row["alma_rate"],
            "Grow Therapy":  row["grow_rate"],
        }
        available = {k: v for k, v in rates.items() if v is not None}
        best = max(available, key=available.get) if available else None
        if best:
            channel_votes[best] = channel_votes.get(best, 0) + 1
        pct = round(row["clinic_rate"] / row["medicare_rate"] * 100,
                    1) if row["clinic_rate"] and row["medicare_rate"] else None
        cpt_results.append({
            "cpt_code":        row["cpt_code"],
            "clinic_rate":     row["clinic_rate"],
            "headway_rate":    row["headway_rate"],
            "alma_rate":       row["alma_rate"],
            "grow_rate":       row["grow_rate"],
            "medicare_rate":   row["medicare_rate"],
            "pct_of_medicare": pct,
            "best_channel":    best,
            "best_rate":       available.get(best),
            "clinic_is_best":  best == "Clinic Submit",
        })
        # Running dollar total per channel across every CPT code on this claim.
        # A channel only counts as a candidate for "best overall" if it has a
        # payable rate for EVERY code being billed -- a channel missing a rate
        # for even one code on the claim can't actually take the whole claim,
        # so it shouldn't win on the strength of a single high-paying line.
        for c in CHANNELS:
            if rates[c] is None:
                channel_covers_all_cpts[c] = False
            else:
                channel_totals[c] += rates[c]

    channel_totals = {
        c: round(channel_totals[c], 2)
        for c in CHANNELS
        if channel_covers_all_cpts[c]
    }
    overall_best = max(
        channel_totals, key=channel_totals.get) if channel_totals else None
    return {
        "canonical_payer":      canonical,
        "state":               state_upper,
        "cpt_results":         cpt_results,
        "overall_best_channel": overall_best,
        "channel_totals":       channel_totals,
        "channel_vote_counts":  channel_votes,
        "mapped":              True,
        "raw_carrier":         carrier,
    }
