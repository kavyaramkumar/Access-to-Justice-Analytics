-- Computes the legal aid desert score for every county.
--
-- Method: each indicator is converted to a national percentile rank
-- (PERCENT_RANK gives 0.0 to 1.0), oriented so that HIGHER always means
-- MORE legal-aid need. Indicators where a low raw value means high need
-- (income, education, legal services supply) are ranked in descending order
-- to flip their direction.
--
--   need_index       = average of the six socioeconomic percentiles x 100
--   supply_gap_index = percentile of how few legal services exist per capita x 100
--   desert_score     = 60% need + 40% supply gap, on a 0-100 scale
--
-- A county scoring 90 has more combined need and less legal-service coverage
-- than roughly 90% of US counties. Counties under 1,000 residents are
-- excluded because their ACS estimates are too noisy to rank fairly.
--
-- The seven individual pr_* percentile columns are kept in the output table on
-- purpose: the dashboard reads them so it can recompute the score live when the
-- user changes the need/supply weighting or turns an indicator off. Keeping the
-- components means the weights are never baked into the data.
--
-- Note this table is deliberately community-agnostic. Need and supply do not
-- depend on which population you are studying, so the dashboard JOINs
-- county_populations to filter by community rather than rescoring per group.

DROP TABLE IF EXISTS desert_scores;

CREATE TABLE desert_scores AS
WITH ranked AS (
    SELECT
        county_fips,
        county_name,
        state_name,
        total_population,
        poverty_rate_pct,
        median_household_income,
        unemployment_rate_pct,
        bachelors_or_higher_pct,
        limited_english_households_pct,
        foreign_born_pct,
        legal_services_establishments,
        legal_services_per_10k,
        PERCENT_RANK() OVER (ORDER BY poverty_rate_pct)                 AS pr_poverty,
        PERCENT_RANK() OVER (ORDER BY median_household_income DESC)     AS pr_low_income,
        PERCENT_RANK() OVER (ORDER BY unemployment_rate_pct)            AS pr_unemployment,
        PERCENT_RANK() OVER (ORDER BY bachelors_or_higher_pct DESC)     AS pr_low_education,
        PERCENT_RANK() OVER (ORDER BY limited_english_households_pct)   AS pr_limited_english,
        PERCENT_RANK() OVER (ORDER BY foreign_born_pct)                 AS pr_foreign_born,
        PERCENT_RANK() OVER (ORDER BY legal_services_per_10k DESC)      AS pr_supply_gap
    FROM county_indicators
    WHERE total_population >= 1000
)
SELECT
    *,
    ROUND((pr_poverty + pr_low_income + pr_unemployment
           + pr_low_education + pr_limited_english + pr_foreign_born)
          / 6.0 * 100, 1)                                              AS need_index,
    ROUND(pr_supply_gap * 100, 1)                                      AS supply_gap_index,
    ROUND((0.6 * (pr_poverty + pr_low_income + pr_unemployment
                  + pr_low_education + pr_limited_english + pr_foreign_born) / 6.0
           + 0.4 * pr_supply_gap) * 100, 1)                            AS desert_score
FROM ranked;

CREATE INDEX idx_desert_scores_score ON desert_scores (desert_score DESC);
