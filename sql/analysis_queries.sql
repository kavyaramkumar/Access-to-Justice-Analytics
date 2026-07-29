-- Example analysis queries against legal_aid.db.
-- Run them all with:  sqlite3 -header -column legal_aid.db < sql/analysis_queries.sql
-- or paste them individually into any SQLite client.
--
-- Queries 2 and 3 cover every community at once, so nothing here privileges a
-- particular group. Query 6 shows the single-community pattern; it takes the
-- community from one editable line rather than hard-coding one throughout.

-- 1. The 25 most severe legal aid deserts in the country
SELECT county_name, state_name, total_population,
       poverty_rate_pct, legal_services_establishments, desert_score
FROM desert_scores
ORDER BY desert_score DESC
LIMIT 25;


-- 2. The worst desert each community faces.
--    One row per community: its highest-scoring county with a meaningful
--    presence (1,000+ residents). This is the dashboard's Communities tab for
--    every group simultaneously, via a window function.
WITH ranked AS (
    SELECT p.group_category, p.group_label,
           d.county_name, d.state_name,
           p.population AS community_population,
           ROUND(p.pct_of_county, 2) AS community_pct,
           d.desert_score,
           ROW_NUMBER() OVER (PARTITION BY p.group_key
                              ORDER BY d.desert_score DESC) AS rn
    FROM county_populations p
    JOIN desert_scores d ON d.county_fips = p.county_fips
    WHERE p.population >= 1000
)
SELECT group_category, group_label, county_name, state_name,
       community_population, community_pct, desert_score
FROM ranked
WHERE rn = 1
ORDER BY desert_score DESC;


-- 3. Which communities are most exposed to legal aid deserts?
--    Population-weighted average desert score per community, plus the share of
--    that community living in a severe desert. Weighting by population means
--    this reflects where people actually live rather than counting a
--    300-person county equally with Los Angeles.
SELECT p.group_category,
       p.group_label,
       SUM(p.population)                                            AS national_population,
       ROUND(SUM(p.population * d.desert_score) / SUM(p.population), 1)
                                                                    AS weighted_desert_score,
       ROUND(100.0 * SUM(CASE WHEN d.desert_score >= 75 THEN p.population ELSE 0 END)
             / SUM(p.population), 1)                                AS pct_in_severe_deserts
FROM county_populations p
JOIN desert_scores d ON d.county_fips = p.county_fips
GROUP BY p.group_key
HAVING SUM(p.population) >= 50000
ORDER BY weighted_desert_score DESC;


-- 4. Counties with ZERO legal services establishments — literal deserts
SELECT county_name, state_name, total_population, poverty_rate_pct, desert_score
FROM desert_scores
WHERE legal_services_establishments = 0
ORDER BY total_population DESC
LIMIT 25;


-- 5. State-level summary: average desert score and residents living in
--    high-desert counties (score >= 75)
SELECT state_name,
       COUNT(*)                                                   AS counties,
       ROUND(AVG(desert_score), 1)                                AS avg_desert_score,
       SUM(CASE WHEN desert_score >= 75 THEN total_population ELSE 0 END)
                                                                  AS pop_in_high_desert_counties
FROM desert_scores
GROUP BY state_name
ORDER BY avg_desert_score DESC;


-- 6. Single-community view: where is one community both numerous AND
--    underserved? The practical shortlist for where an affinity legal
--    organisation gets the most reach per outreach dollar.
--
--    Change the one value in `target` to any group_key. Run
--        SELECT DISTINCT group_key, group_label FROM county_populations
--                 ORDER BY group_label;
--    to list all 48.
WITH target AS (SELECT 'hispanic_latino' AS group_key)
SELECT p.group_label, d.county_name, d.state_name,
       p.population AS community_population,
       ROUND(p.pct_of_county, 2) AS community_pct,
       d.desert_score
FROM county_populations p
JOIN desert_scores d ON d.county_fips = p.county_fips
WHERE p.group_key = (SELECT group_key FROM target)
  AND d.desert_score >= 50
ORDER BY p.population DESC
LIMIT 25;
