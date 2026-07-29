-- Example analysis queries against legal_aid.db.
-- Run them all with:  sqlite3 -header -column legal_aid.db < sql/analysis_queries.sql
-- or paste them individually into any SQLite client.

-- 1. The 25 most severe legal aid deserts in the country
SELECT county_name, state_name, total_population,
       poverty_rate_pct, legal_services_establishments, desert_score
FROM desert_scores
ORDER BY desert_score DESC
LIMIT 25;


-- 2. Deserts that intersect a specific community.
--    Change 'south_asian' to any group_key in county_populations
--    (e.g. 'vietnamese', 'mexican', 'arab', 'black', 'nepalese').
--    This is the query behind the dashboard's Communities tab.
SELECT d.county_name, d.state_name, d.total_population,
       p.population AS community_population,
       ROUND(p.pct_of_county, 2) AS community_pct,
       d.desert_score
FROM desert_scores d
JOIN county_populations p ON p.county_fips = d.county_fips
WHERE p.group_key = 'south_asian'
  AND p.population >= 1000
ORDER BY d.desert_score DESC
LIMIT 25;


-- 3. Which communities are most exposed to legal aid deserts?
--    Population-weighted average desert score per community, plus the share of
--    that community living in a severe desert. A high exposure score means the
--    community is concentrated in underserved counties.
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


-- 6. Where is a community both numerous AND underserved?
--    Ranks counties by community population inside above-average deserts —
--    the practical shortlist for where an affinity legal organization would
--    get the most reach per outreach dollar.
SELECT p.group_label, d.county_name, d.state_name,
       p.population AS community_population,
       d.desert_score
FROM county_populations p
JOIN desert_scores d ON d.county_fips = p.county_fips
WHERE p.group_key = 'south_asian'
  AND d.desert_score >= 50
ORDER BY p.population DESC
LIMIT 25;
