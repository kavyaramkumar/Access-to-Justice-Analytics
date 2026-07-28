"""
Step 1 of 2: Pull county-level data for all 50 states + DC from the Census Bureau.

Three API calls, each covering every county in the country at once:

1. ACS 5-Year Estimates (detail tables) — the socioeconomic need indicators:
   - B01003_001E                total population
   - B17001_002E / B17001_001E  population below poverty / poverty universe
   - B19013_001E                median household income
   - B05002_013E                foreign-born population
   - B15003_001E, _022E-_025E   education universe 25+, bachelor's/master's/professional/doctorate
   - B23025_003E / B23025_005E  civilian labor force / unemployed
   plus the configurable population-overlay variables from config.json
   (default: the South Asian groups in table B02015)

2. ACS 5-Year Estimates (subject table S1602) — limited English speaking
   households as a ready-made percentage (S1602_C04_001E).

3. County Business Patterns (NAICS 5411 "Legal Services") — the SUPPLY side:
   how many legal-services establishments (law firms, legal aid offices, etc.)
   actually exist in each county. Counties missing from this dataset have zero.

Output: data/census_county_data.csv (one row per county), consumed by
build_database.py.

Setup: put your Census API key in a file named .env in this folder:
    CENSUS_API_KEY=yourkeyhere
(Free key: https://api.census.gov/data/key_signup.html)
"""

import json
import os
import sys

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Configuration and API key
# ---------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(HERE, "config.json")) as f:
    CONFIG = json.load(f)

ACS_YEAR = CONFIG["acs_year"]
CBP_YEAR = CONFIG["cbp_year"]
EXCLUDE_STATES = set(CONFIG["exclude_states"].keys())

OVERLAY_NAME = CONFIG["population_overlay"]["active_group"]
OVERLAY_VARS = CONFIG["population_overlay"]["groups"][OVERLAY_NAME]["variables"]


def load_api_key():
    """Read CENSUS_API_KEY from the environment or from a local .env file."""
    key = os.environ.get("CENSUS_API_KEY")
    if key:
        return key
    env_path = os.path.join(HERE, ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("CENSUS_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit(
        "No API key found. Create a file named .env in this folder containing:\n"
        "    CENSUS_API_KEY=yourkeyhere\n"
        "Get a free key at https://api.census.gov/data/key_signup.html"
    )


API_KEY = load_api_key()


def census_get(url, params):
    """Call a Census API endpoint and return the response as a DataFrame."""
    params = dict(params, key=API_KEY)
    response = requests.get(url, params=params, timeout=120)
    response.raise_for_status()
    data = response.json()
    return pd.DataFrame(data[1:], columns=data[0])


# ---------------------------------------------------------------------------
# 1. ACS detail tables: need indicators + population overlay, every US county
# ---------------------------------------------------------------------------

DETAIL_VARS = {
    "B01003_001E": "total_population",
    "B17001_001E": "poverty_universe",
    "B17001_002E": "population_below_poverty",
    "B19013_001E": "median_household_income",
    "B05002_013E": "foreign_born_population",
    "B15003_001E": "population_25_plus",
    "B15003_022E": "bachelors_degree",
    "B15003_023E": "masters_degree",
    "B15003_024E": "professional_degree",
    "B15003_025E": "doctorate_degree",
    "B23025_003E": "labor_force",
    "B23025_005E": "unemployed",
}

print(f"Pulling ACS {ACS_YEAR} 5-year detail tables for every US county...")
df = census_get(
    f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5",
    {"get": ",".join(["NAME"] + list(DETAIL_VARS) + list(OVERLAY_VARS)),
     "for": "county:*"},
)
print(f"  {len(df)} counties returned")

# ---------------------------------------------------------------------------
# 2. ACS subject table S1602: limited English speaking households (percent)
# ---------------------------------------------------------------------------

print(f"Pulling ACS {ACS_YEAR} subject table S1602 (limited English households)...")
lep_df = census_get(
    f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5/subject",
    {"get": "S1602_C01_001E,S1602_C04_001E", "for": "county:*"},
)
df = df.merge(lep_df, on=["state", "county"], how="left")

# ---------------------------------------------------------------------------
# 3. County Business Patterns: legal-services establishments (NAICS 5411)
# ---------------------------------------------------------------------------

print(f"Pulling CBP {CBP_YEAR} legal-services establishments (NAICS 5411)...")
cbp_df = census_get(
    f"https://api.census.gov/data/{CBP_YEAR}/cbp",
    {"get": "ESTAB", "for": "county:*", "NAICS2017": "5411"},
)
cbp_df = cbp_df.rename(columns={"ESTAB": "legal_services_establishments"})
df = df.merge(
    cbp_df[["state", "county", "legal_services_establishments"]],
    on=["state", "county"], how="left",
)
# A county absent from CBP for NAICS 5411 has no legal-services establishments.
df["legal_services_establishments"] = df["legal_services_establishments"].fillna(0)

# ---------------------------------------------------------------------------
# Clean up and derive the analysis columns
# ---------------------------------------------------------------------------

df = df[~df["state"].isin(EXCLUDE_STATES)]

df = df.rename(columns=DETAIL_VARS)
df = df.rename(columns={
    "NAME": "county_name",
    "S1602_C01_001E": "total_households",
    "S1602_C04_001E": "limited_english_households_pct",
})

overlay_cols = []
for var, label in OVERLAY_VARS.items():
    col = f"{OVERLAY_NAME}__{label.lower().replace(' ', '_')}"
    df = df.rename(columns={var: col})
    overlay_cols.append(col)

numeric_cols = (
    list(DETAIL_VARS.values())
    + ["total_households", "limited_english_households_pct",
       "legal_services_establishments"]
    + overlay_cols
)
for col in numeric_cols:
    df[col] = pd.to_numeric(df[col], errors="coerce")

# The Census API uses large negative sentinels (e.g. -666666666) where an
# estimate can't be computed; treat those as missing.
df[numeric_cols] = df[numeric_cols].where(df[numeric_cols] > -100000)

df["county_fips"] = df["state"] + df["county"]
df["state_name"] = df["county_name"].str.split(", ").str[-1]

df["poverty_rate_pct"] = (df["population_below_poverty"] / df["poverty_universe"] * 100).round(2)
df["bachelors_or_higher_pct"] = (
    (df["bachelors_degree"] + df["masters_degree"]
     + df["professional_degree"] + df["doctorate_degree"])
    / df["population_25_plus"] * 100
).round(2)
df["unemployment_rate_pct"] = (df["unemployed"] / df["labor_force"] * 100).round(2)
df["foreign_born_pct"] = (df["foreign_born_population"] / df["total_population"] * 100).round(2)
df["legal_services_per_10k"] = (
    df["legal_services_establishments"] / df["total_population"] * 10000
).round(3)

df[f"{OVERLAY_NAME}_population"] = df[overlay_cols].sum(axis=1)
df[f"{OVERLAY_NAME}_pct"] = (
    df[f"{OVERLAY_NAME}_population"] / df["total_population"] * 100
).round(3)

# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

out_cols = [
    "county_fips", "county_name", "state_name",
    "total_population", "total_households",
    "poverty_rate_pct", "median_household_income", "unemployment_rate_pct",
    "bachelors_or_higher_pct", "limited_english_households_pct",
    "foreign_born_pct",
    "legal_services_establishments", "legal_services_per_10k",
    f"{OVERLAY_NAME}_population", f"{OVERLAY_NAME}_pct",
] + overlay_cols

os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
out_path = os.path.join(HERE, "data", "census_county_data.csv")
df[out_cols].to_csv(out_path, index=False)

print(f"\nSaved {len(df)} counties across {df['state_name'].nunique()} states/DC")
print(f"  -> {out_path}")
print(f"\nPopulation overlay: {OVERLAY_NAME}")
print("Next step: python build_database.py")
