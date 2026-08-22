BEGIN;

CREATE OR REPLACE VIEW v_sbh_vs_medicare AS
SELECT
    ir.payer_name,
    cc.cpt_code,
    cc.short_description,
    cc.category,
    ir.state,
    ir.allowed_amount AS payer_allowed,
    ir.provider,
    ir.effective_date,
    ir.updated_at,
    bfs.allowed_amount AS medicare_allowed,
    CASE
        WHEN bfs.allowed_amount IS NOT NULL AND bfs.allowed_amount > 0
        THEN ROUND((ir.allowed_amount / bfs.allowed_amount * 100)::numeric, 1)
    END AS pct_of_medicare
FROM intermediary_rates ir
JOIN intermediaries i ON ir.intermediary_id = i.intermediary_id
JOIN cpt_codes cc ON cc.cpt_code = ir.cpt_code
LEFT JOIN benchmark_fee_schedule bfs
    ON bfs.cpt_code = ir.cpt_code
    AND bfs.source_name = 'Medicare 2026'
    AND bfs.effective_year = 2026
    AND bfs.locality = COALESCE(NULLIF(current_setting('app.benchmark_locality', TRUE), ''), ir.state, 'FL')
WHERE i.name = 'SBH' AND i.active = TRUE
  AND ir.cpt_code IN ('99215','99214','90833','90836','90838','99204','99205','90785','98002','98003','98006','98007');

CREATE OR REPLACE VIEW v_negotiation_dashboard AS
WITH targets AS (
    SELECT sm.*,
        COALESCE(
            (SELECT nt.target_pct_of_medicare FROM negotiation_targets nt JOIN payers p ON nt.payer_id=p.payer_id WHERE lower(p.payer_name)=lower(sm.payer_name) AND nt.cpt_code=sm.cpt_code),
            (SELECT nt.target_pct_of_medicare FROM negotiation_targets nt JOIN payers p ON nt.payer_id=p.payer_id WHERE lower(p.payer_name)=lower(sm.payer_name) AND nt.cpt_code IS NULL),
            (SELECT nt.target_pct_of_medicare FROM negotiation_targets nt WHERE nt.payer_id IS NULL AND nt.cpt_code IS NULL),
            130.0
        ) AS target_pct_of_medicare
    FROM v_sbh_vs_medicare sm
)
SELECT
    ABS(('x'||substr(md5(t.payer_name),1,8))::bit(32)::int) AS fee_schedule_line_id,
    ABS(('x'||substr(md5(t.payer_name),1,8))::bit(32)::int) AS contract_id,
    p.payer_id,
    t.payer_name,
    NULL::int AS provider_entity_id,
    'SBH Clinic Submit' AS provider_name,
    'SBH' AS npi_number,
    'NPI2' AS entity_type,
    'SBH-DIRECT' AS payer_contract_id,
    'Clinic Submit' AS product_line,
    t.cpt_code,
    t.short_description,
    t.category,
    FALSE AS telehealth_eligible,
    FALSE AS is_addon,
    NULL::varchar AS modifier,
    '11' AS place_of_service,
    'per_visit' AS unit_type,
    t.effective_date,
    t.payer_allowed,
    t.medicare_allowed,
    t.pct_of_medicare,
    t.target_pct_of_medicare,
    CASE WHEN t.medicare_allowed IS NULL THEN NULL::numeric
         ELSE ROUND((t.medicare_allowed * t.target_pct_of_medicare / 100)::numeric, 2)
    END AS target_allowed,
    CASE WHEN t.medicare_allowed IS NULL THEN NULL::numeric
         ELSE ROUND((t.medicare_allowed * t.target_pct_of_medicare / 100 - t.payer_allowed)::numeric, 2)
    END AS rate_gap_per_unit,
    CASE WHEN t.pct_of_medicare IS NULL OR t.target_pct_of_medicare IS NULL THEN NULL::boolean
         WHEN t.pct_of_medicare < t.target_pct_of_medicare THEN TRUE
         ELSE FALSE
    END AS is_underpaid,
    NULL::int AS annual_volume,
    NULL::int AS volume_year,
    NULL::numeric AS annual_revenue_current,
    NULL::numeric AS annual_revenue_at_target,
    NULL::numeric AS annual_revenue_gap
FROM targets t
LEFT JOIN payers p ON lower(p.payer_name) = lower(t.payer_name)
ORDER BY t.payer_name, t.cpt_code;

CREATE OR REPLACE VIEW v_negotiation_summary AS
SELECT
    payer_id,
    payer_name,
    COUNT(*) AS codes_with_rates,
    COUNT(*) FILTER (WHERE is_underpaid = TRUE) AS codes_underpaid,
    ROUND(AVG(pct_of_medicare)::numeric, 1) AS avg_pct_of_medicare,
    AVG(target_pct_of_medicare) AS avg_target_pct,
    NULL::numeric AS total_revenue_current,
    NULL::numeric AS total_revenue_at_target,
    NULL::numeric AS total_revenue_gap
FROM v_negotiation_dashboard
GROUP BY payer_id, payer_name
ORDER BY codes_underpaid DESC, avg_pct_of_medicare ASC;

COMMIT;

SELECT 'v_sbh_vs_medicare' AS view_name, COUNT(*) AS rows FROM v_sbh_vs_medicare WHERE state='FL'
UNION ALL SELECT 'v_negotiation_dashboard', COUNT(*) FROM v_negotiation_dashboard
UNION ALL SELECT 'v_negotiation_summary', COUNT(*) FROM v_negotiation_summary
ORDER BY view_name;
