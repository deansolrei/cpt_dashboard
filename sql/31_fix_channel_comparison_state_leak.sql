-- 31_fix_channel_comparison_state_leak.sql
-- Fix: v_channel_comparison's all_combos CTE pulled candidate
-- (payer_name, cpt_code) pairs from intermediary_rates with NO state
-- filter, so a payer with rates for ANY state became a candidate row for
-- EVERY state's query. Once joined against the (correctly state-scoped)
-- headway/alma/grow CTEs, most of these ghost rows come back all-NULL and
-- read as "No Data" — but the underlying leak is real, is the same bug
-- independently found and fixed in backend/routers/intermediaries.py's
-- inline CHANNEL_COMPARISON_SQL (2026-08), and is worth closing here too
-- since this view backs /api/channel-comparison/summary and any other
-- future caller.
--
-- Symptom this closes: querying one state (e.g. Colorado) could surface a
-- payer that has zero intermediary_rates rows for that state at all (e.g.
-- "Anthem BCBS Maine" showing up under a Colorado query).
--
-- Fix: add "AND state = <active locality>" to the intermediary_rates half
-- of the all_combos UNION. The fee_schedule_lines half is untouched — that
-- table has no state column, so it isn't state-scoped by design (direct/
-- SBH contract rates aren't state-specific in this schema).
--
-- Run:
--   psql solrei_cpt -f sql/31_fix_channel_comparison_state_leak.sql

SELECT '=== Fixing v_channel_comparison all_combos state leak ===' AS info;

CREATE OR REPLACE VIEW v_channel_comparison AS
WITH

direct_rates AS (
    SELECT DISTINCT ON (p.payer_name, fsl.cpt_code)
        p.payer_name,
        fsl.cpt_code,
        fsl.allowed_amount AS direct_rate
    FROM fee_schedule_lines fsl
    JOIN contracts         c   ON fsl.contract_id       = c.contract_id
    JOIN payers            p   ON c.payer_id            = p.payer_id
    JOIN provider_entities pe  ON c.provider_entity_id  = pe.provider_entity_id
    WHERE c.active = TRUE
      AND (c.end_date  IS NULL OR c.end_date  >= CURRENT_DATE)
      AND (fsl.end_date IS NULL OR fsl.end_date >= CURRENT_DATE)
    ORDER BY
        p.payer_name,
        fsl.cpt_code,
        CASE pe.entity_type WHEN 'NPI1' THEN 0 ELSE 1 END,
        fsl.allowed_amount DESC
),

medicare AS (
    SELECT cpt_code, allowed_amount AS medicare_allowed
    FROM   benchmark_fee_schedule
    WHERE  source_name    = 'Medicare 2026'
      AND  effective_year = 2026
      AND  locality       = COALESCE(
                                NULLIF(current_setting('app.benchmark_locality', TRUE), ''),
                                'FL'
                            )
),

all_combos AS (
    -- FIXED: scoped to the active state, matching every CTE below. Was
    -- previously unfiltered, letting any payer with rates for ANY state
    -- become a candidate row for the state actually being queried.
    SELECT DISTINCT payer_name, cpt_code
    FROM   intermediary_rates
    WHERE  payer_name IS NOT NULL
      AND  cpt_code IN (
               '99214','99215','90833','90836','90838',
               '99204','99205','90785',
               '98002','98003','98006','98007'
           )
      AND  state = COALESCE(NULLIF(current_setting('app.benchmark_locality', TRUE), ''), 'FL')
    UNION
    -- Unchanged — fee_schedule_lines/contracts has no state column, direct/
    -- SBH contract rates aren't state-specific in this schema.
    SELECT DISTINCT p.payer_name, fsl.cpt_code
    FROM fee_schedule_lines fsl
    JOIN contracts         c  ON fsl.contract_id       = c.contract_id
    JOIN payers            p  ON c.payer_id            = p.payer_id
    WHERE c.active = TRUE
      AND (c.end_date  IS NULL OR c.end_date  >= CURRENT_DATE)
      AND (fsl.end_date IS NULL OR fsl.end_date >= CURRENT_DATE)
      AND fsl.cpt_code IN (
               '99214','99215','90833','90836','90838',
               '99204','99205','90785',
               '98002','98003','98006','98007'
           )
),

name_resolved AS (
    SELECT DISTINCT ON (ac.payer_name)
        ac.payer_name  AS intermediary_payer_name,
        COALESCE(
            ipm.direct_payer_name,
            (SELECT p.payer_name FROM payers p
             WHERE  lower(p.payer_name) = lower(ac.payer_name) LIMIT 1)
        ) AS direct_payer_name
    FROM all_combos ac
    LEFT JOIN intermediary_payer_map ipm
           ON ipm.intermediary_payer_name = ac.payer_name
    ORDER BY ac.payer_name, ipm.direct_payer_name NULLS LAST
),

headway AS (
    SELECT DISTINCT ON (ir.payer_name, ir.cpt_code)
        ir.payer_name,
        ir.cpt_code,
        ir.allowed_amount  AS headway_rate,
        ir.updated_at      AS headway_updated_at
    FROM   intermediary_rates ir
    JOIN   intermediaries i ON ir.intermediary_id = i.intermediary_id
    WHERE  i.name = 'Headway' AND i.active = TRUE
      AND  (ir.effective_date IS NULL OR ir.effective_date <= CURRENT_DATE)
      AND  ir.state = COALESCE(NULLIF(current_setting('app.benchmark_locality', TRUE), ''), 'FL')
    ORDER BY ir.payer_name, ir.cpt_code, ir.updated_at DESC, ir.allowed_amount DESC
),

alma AS (
    SELECT DISTINCT ON (ir.payer_name, ir.cpt_code)
        ir.payer_name,
        ir.cpt_code,
        ir.allowed_amount  AS alma_rate,
        ir.updated_at      AS alma_updated_at
    FROM   intermediary_rates ir
    JOIN   intermediaries i ON ir.intermediary_id = i.intermediary_id
    WHERE  i.name = 'Alma' AND i.active = TRUE
      AND  (ir.effective_date IS NULL OR ir.effective_date <= CURRENT_DATE)
      AND  ir.state = COALESCE(NULLIF(current_setting('app.benchmark_locality', TRUE), ''), 'FL')
    ORDER BY ir.payer_name, ir.cpt_code, ir.updated_at DESC, ir.allowed_amount DESC
),

grow AS (
    SELECT DISTINCT ON (ir.payer_name, ir.cpt_code)
        ir.payer_name,
        ir.cpt_code,
        ir.allowed_amount  AS grow_rate,
        ir.updated_at      AS grow_updated_at
    FROM   intermediary_rates ir
    JOIN   intermediaries i ON ir.intermediary_id = i.intermediary_id
    WHERE  i.name = 'Grow Therapy' AND i.active = TRUE
      AND  (ir.effective_date IS NULL OR ir.effective_date <= CURRENT_DATE)
      AND  ir.state = COALESCE(NULLIF(current_setting('app.benchmark_locality', TRUE), ''), 'FL')
    ORDER BY ir.payer_name, ir.cpt_code, ir.updated_at DESC, ir.allowed_amount DESC
),

combined AS (
    SELECT
        p.payer_id,
        ac.payer_name,
        ac.cpt_code,
        cc.short_description,
        cc.category,
        m.medicare_allowed,
        dr.direct_rate,
        CASE WHEN dr.direct_rate IS NOT NULL AND m.medicare_allowed > 0
             THEN ROUND((dr.direct_rate / m.medicare_allowed * 100)::numeric, 1)
        END AS direct_pct_of_medicare,
        h.headway_rate,
        h.headway_updated_at,
        a.alma_rate,
        a.alma_updated_at,
        g.grow_rate,
        g.grow_updated_at,
        LEAST(
            h.headway_updated_at,
            a.alma_updated_at,
            g.grow_updated_at
        ) AS oldest_intermediary_update,
        CASE
            WHEN GREATEST(
                COALESCE(dr.direct_rate,  0),
                COALESCE(h.headway_rate,  0),
                COALESCE(a.alma_rate,     0),
                COALESCE(g.grow_rate,     0)
            ) = 0 THEN 'No Data'
            WHEN COALESCE(dr.direct_rate, 0) >= COALESCE(h.headway_rate, 0)
             AND COALESCE(dr.direct_rate, 0) >= COALESCE(a.alma_rate,    0)
             AND COALESCE(dr.direct_rate, 0) >= COALESCE(g.grow_rate,    0)
             AND dr.direct_rate IS NOT NULL
            THEN 'Direct'
            ELSE 'Intermediary'
        END AS best_channel_type,
        CASE WHEN dr.direct_rate IS NOT NULL THEN TRUE ELSE FALSE END AS has_direct_contract
    FROM all_combos ac
    JOIN  cpt_codes    cc  ON cc.cpt_code  = ac.cpt_code
    LEFT JOIN medicare     m   ON m.cpt_code   = ac.cpt_code
    LEFT JOIN name_resolved nr ON nr.intermediary_payer_name = ac.payer_name
    LEFT JOIN direct_rates  dr ON dr.payer_name = nr.direct_payer_name
                               AND dr.cpt_code  = ac.cpt_code
    LEFT JOIN payers        p  ON p.payer_name  = nr.direct_payer_name
    LEFT JOIN headway       h  ON h.payer_name  = ac.payer_name AND h.cpt_code = ac.cpt_code
    LEFT JOIN alma          a  ON a.payer_name  = ac.payer_name AND a.cpt_code = ac.cpt_code
    LEFT JOIN grow          g  ON g.payer_name  = ac.payer_name AND g.cpt_code = ac.cpt_code
)

SELECT DISTINCT ON (payer_name, cpt_code)
    payer_id, payer_name, cpt_code, short_description, category,
    medicare_allowed, direct_rate, direct_pct_of_medicare,
    headway_rate, headway_updated_at,
    alma_rate,    alma_updated_at,
    grow_rate,    grow_updated_at,
    oldest_intermediary_update,
    best_channel_type, has_direct_contract
FROM combined
ORDER BY payer_name, cpt_code;

SELECT 'v_channel_comparison state-leak fix applied ✓' AS info;

-- ── Verify: no more payer_name showing for a state with zero real rows ──
-- (adjust the locality below to spot-check any state)
SET LOCAL app.benchmark_locality = 'CO';
SELECT payer_name, cpt_code, headway_rate, alma_rate, grow_rate, direct_rate
FROM v_channel_comparison
WHERE payer_name = 'Anthem BCBS Maine';
-- Expect: 0 rows (Anthem BCBS Maine has no Colorado data at all)
