-- Example analysis queries against legal_aid.db.
-- Run any of these with:  sqlite3 legal_aid.db < sql/analysis_queries.sql
-- or paste them individually into any SQLite client.

-- 1. The 25 most severe legal aid deserts in the country
SELECT county_name, state_name, total_population,
       poverty_rate_pct, legal_services_establishments, desert_score
FROM desert_scores
ORDER BY desert_score DESC
LIMIT 25;

-- 2. Deserts that intersect with the overlay community (default: South Asian).
--    Where should an affinity legal organization like SABA-NA focus pro bono
--    outreach? High desert score AND a meaningful overlay population.
SELECT county_name, state_name, total_population,
       overlay_population, overlay_pct, desert_score
FROM desert_scores
WHERE overlay_population >= 1000
ORDER BY desert_score DESC
LIMIT 25;

-- 3. Largest overlay communities living in above-average deserts
--    (sorted by community size instead of desert severity)
SELECT county_name, state_name, overlay_population, overlay_pct, desert_score
FROM desert_scores
WHERE desert_score >= 50
ORDER BY overlay_population DESC
LIMIT 25;

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
