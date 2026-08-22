-- =============================================================
-- 32_channel_plan_overrides.sql
-- Handles cases where a patient's real insurance plan gets billed
-- under a DIFFERENT plan name by a specific channel, due to
-- interstate/network reciprocity (BlueCard-style routing) — this is
-- NOT a spelling variant, it's a genuinely different negotiated
-- contract with its own rate card.
--
-- Confirmed case (2026-08-21): Alma has no direct New York Anthem
-- contract, so it bills Anthem BCBS New York patients under its
-- Blue Cross Blue Shield of Massachusetts contract instead — a real,
-- separately-rated plan (also used normally for actual Massachusetts
-- patients). Dean's own diagnosis, confirmed directly with him.
--
-- Deliberately a SEPARATE table from intermediary_rates, not a new
-- column on it — this answers a different question ("which plan
-- should this channel even be looked up under") than intermediary_rates
-- answers ("what does a plan pay"). Mixing the two would require an
-- unusual pivoted schema and make the rates tab harder to read.
--
-- Deliberately NOT for pure spelling/naming variants across systems
-- (e.g. Tebra's "Anthem BCBS New York" vs Headway's "Anthem Blue
-- Cross and Blue Shield New York" — same contract, different string).
-- Those stay in backend/routers/best_channel.py's CARRIER_MAP, which
-- already handles that case correctly. This table is only for cases
-- where the CONTRACT itself genuinely differs by channel.
--
-- Expected to stay small — most plans and channels will never need a
-- row here. Add rows only as real cases are confirmed (a rate that
-- looks implausibly wrong or blank for a channel known to cover that
-- patient is the trigger to investigate), not speculatively.
-- =============================================================

BEGIN;

CREATE TABLE IF NOT EXISTS channel_plan_overrides (
    override_id      SERIAL PRIMARY KEY,
    home_plan        VARCHAR(150) NOT NULL,
    channel          VARCHAR(100) NOT NULL,
    effective_plan   VARCHAR(150) NOT NULL,
    notes            TEXT,
    active           BOOLEAN DEFAULT TRUE,
    created_at       TIMESTAMP DEFAULT NOW(),
    updated_at       TIMESTAMP DEFAULT NOW(),

    UNIQUE (home_plan, channel)
);

CREATE INDEX IF NOT EXISTS idx_cpo_home_plan ON channel_plan_overrides(home_plan);

COMMENT ON TABLE channel_plan_overrides IS
    'Per-channel plan-name substitutions for cases where a channel bills a patient''s real plan under a different, separately-contracted plan (e.g. Alma routing out-of-network Anthem BCBS New York patients through its Blue Cross Blue Shield of Massachusetts contract). NOT for spelling/naming variants across systems -- those belong in best_channel.py CARRIER_MAP instead. Populated from the Master Sheet''s ChannelPlanOverrides tab via sync_rates_from_sheets.py / POST /api/channel-overrides/import.';

INSERT INTO channel_plan_overrides (home_plan, channel, effective_plan, notes)
VALUES (
    'Anthem BCBS New York', 'Alma', 'BCBS Massachusetts',
    'Alma has no direct NY Anthem contract; routes via BlueCard-style reciprocity through its Massachusetts BCBS contract. Confirmed by Dean 2026-08-21.'
)
ON CONFLICT (home_plan, channel) DO UPDATE SET
    effective_plan = EXCLUDED.effective_plan,
    notes          = EXCLUDED.notes,
    updated_at     = NOW();

COMMIT;

SELECT * FROM channel_plan_overrides ORDER BY home_plan, channel;
