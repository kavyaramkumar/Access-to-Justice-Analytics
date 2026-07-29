"""
Step 2 of 3: Load the pulled Census data into SQLite and score every county.

Reads the two CSVs from data/ (created by pull_census_data.py), builds
legal_aid.db using sql/schema.sql, computes desert scores with
sql/desert_scores.sql, and exports ranked results to outputs/.
"""

import json
import os
import sqlite3
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(HERE, "config.json")) as f:
    CONFIG = json.load(f)

indicators_path = os.path.join(HERE, "data", "census_county_data.csv")
pops_path = os.path.join(HERE, "data", "census_county_populations.csv")
for path in (indicators_path, pops_path):
    if not os.path.exists(path):
        sys.exit(f"{os.path.relpath(path, HERE)} not found — run pull_census_data.py first.")

indicators = pd.read_csv(indicators_path, dtype={"county_fips": str})
pops = pd.read_csv(pops_path, dtype={"county_fips": str})

db_path = os.path.join(HERE, "legal_aid.db")
conn = sqlite3.connect(db_path)

with open(os.path.join(HERE, "sql", "schema.sql")) as f:
    conn.executescript(f.read())

indicators.to_sql("county_indicators", conn, if_exists="append", index=False)
pops.to_sql("county_populations", conn, if_exists="append", index=False)

# Each community's share of its county, computed in SQL against the freshly
# loaded indicator table rather than carried through the CSV.
conn.execute("""
    UPDATE county_populations
    SET pct_of_county = ROUND(
        100.0 * population / (
            SELECT NULLIF(i.total_population, 0)
            FROM county_indicators i
            WHERE i.county_fips = county_populations.county_fips
        ), 4)
""")

conn.executemany(
    "INSERT INTO run_metadata (key, value) VALUES (?, ?)",
    [
        ("acs_year", str(CONFIG["acs_year"])),
        ("cbp_year", str(CONFIG["cbp_year"])),
        ("community_groups", str(len(CONFIG["population_groups"]))),
    ],
)

with open(os.path.join(HERE, "sql", "desert_scores.sql")) as f:
    conn.executescript(f.read())

conn.commit()

# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

os.makedirs(os.path.join(HERE, "outputs"), exist_ok=True)

scores = pd.read_sql_query(
    "SELECT * FROM desert_scores ORDER BY desert_score DESC", conn
)
scores_path = os.path.join(HERE, "outputs", "legal_aid_desert_scores.csv")
scores.to_csv(scores_path, index=False)

# Which communities are most exposed to legal aid deserts (query 3 in
# sql/analysis_queries.sql) — a useful standalone deliverable.
exposure = pd.read_sql_query("""
    SELECT p.group_category, p.group_label,
           SUM(p.population)                                          AS national_population,
           ROUND(SUM(p.population * d.desert_score) / SUM(p.population), 1)
                                                                      AS weighted_desert_score,
           ROUND(100.0 * SUM(CASE WHEN d.desert_score >= 75 THEN p.population ELSE 0 END)
                 / SUM(p.population), 1)                              AS pct_in_severe_deserts
    FROM county_populations p
    JOIN desert_scores d ON d.county_fips = p.county_fips
    GROUP BY p.group_key
    HAVING SUM(p.population) > 0
    ORDER BY weighted_desert_score DESC
""", conn)
exposure_path = os.path.join(HERE, "outputs", "community_desert_exposure.csv")
exposure.to_csv(exposure_path, index=False)

n_groups = conn.execute("SELECT COUNT(DISTINCT group_key) FROM county_populations").fetchone()[0]
conn.close()

print(f"Scored {len(scores):,} counties across {scores['state_name'].nunique()} states/DC")
print(f"Loaded {n_groups} community groups")
print(f"Database -> {db_path}")
print(f"  -> {scores_path}")
print(f"  -> {exposure_path}\n")

pd.set_option("display.width", 130)
print("Top 10 legal aid deserts nationally:")
print(scores[["county_name", "state_name", "total_population",
              "poverty_rate_pct", "legal_services_establishments",
              "desert_score"]].head(10).to_string(index=False))

print("\nCommunities most exposed to legal aid deserts "
      "(population-weighted, 250k+ national population):")
big = exposure[exposure["national_population"] >= 250000].head(12)
print(big.to_string(index=False))

print("\nNext step: python build_dashboard.py")
