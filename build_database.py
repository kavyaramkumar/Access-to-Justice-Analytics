"""
Step 2 of 2: Load the pulled Census data into SQLite and score every county.

Reads data/census_county_data.csv (created by pull_census_data.py), builds
legal_aid.db using sql/schema.sql, computes desert scores with
sql/desert_scores.sql, and exports the ranked results to
outputs/legal_aid_desert_scores.csv.
"""

import json
import os
import sqlite3
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(HERE, "config.json")) as f:
    CONFIG = json.load(f)

OVERLAY_NAME = CONFIG["population_overlay"]["active_group"]

csv_path = os.path.join(HERE, "data", "census_county_data.csv")
if not os.path.exists(csv_path):
    sys.exit("data/census_county_data.csv not found — run pull_census_data.py first.")

df = pd.read_csv(csv_path, dtype={"county_fips": str})

# The pull script names the overlay columns after the active group
# (e.g. south_asian_population); the database uses generic names so the
# SQL never has to change when you switch groups in config.json.
df = df.rename(columns={
    f"{OVERLAY_NAME}_population": "overlay_population",
    f"{OVERLAY_NAME}_pct": "overlay_pct",
})

TABLE_COLUMNS = [
    "county_fips", "county_name", "state_name",
    "total_population", "total_households",
    "poverty_rate_pct", "median_household_income", "unemployment_rate_pct",
    "bachelors_or_higher_pct", "limited_english_households_pct",
    "foreign_born_pct",
    "legal_services_establishments", "legal_services_per_10k",
    "overlay_population", "overlay_pct",
]

db_path = os.path.join(HERE, "legal_aid.db")
conn = sqlite3.connect(db_path)

with open(os.path.join(HERE, "sql", "schema.sql")) as f:
    conn.executescript(f.read())

df[TABLE_COLUMNS].to_sql("county_indicators", conn, if_exists="append", index=False)

conn.executemany(
    "INSERT INTO run_metadata (key, value) VALUES (?, ?)",
    [
        ("overlay_group", OVERLAY_NAME),
        ("acs_year", str(CONFIG["acs_year"])),
        ("cbp_year", str(CONFIG["cbp_year"])),
    ],
)

with open(os.path.join(HERE, "sql", "desert_scores.sql")) as f:
    conn.executescript(f.read())

conn.commit()

scores = pd.read_sql_query(
    "SELECT * FROM desert_scores ORDER BY desert_score DESC", conn
)
conn.close()

# Export the ranked results, restoring the descriptive overlay column names
out = scores.rename(columns={
    "overlay_population": f"{OVERLAY_NAME}_population",
    "overlay_pct": f"{OVERLAY_NAME}_pct",
})
os.makedirs(os.path.join(HERE, "outputs"), exist_ok=True)
out_path = os.path.join(HERE, "outputs", "legal_aid_desert_scores.csv")
out.to_csv(out_path, index=False)

print(f"Scored {len(scores)} counties -> {out_path}")
print(f"Database: {db_path}\n")

pd.set_option("display.width", 120)
print("Top 10 legal aid deserts nationally:")
print(scores[["county_name", "state_name", "total_population",
              "poverty_rate_pct", "legal_services_establishments",
              "desert_score"]].head(10).to_string(index=False))

overlay_hits = scores[scores["overlay_population"] >= 1000].head(10)
print(f"\nTop 10 deserts with at least 1,000 {OVERLAY_NAME} residents:")
print(overlay_hits[["county_name", "state_name", "overlay_population",
                    "overlay_pct", "desert_score"]].to_string(index=False))
