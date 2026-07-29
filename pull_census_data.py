"""
Step 1 of 3: Pull county-level data for all 50 states + DC from the Census Bureau.

Covers every county in the country. Two output files:

  data/census_county_data.csv         one row per county: need indicators + supply
  data/census_county_populations.csv  long format: one row per county per community

What gets pulled:

1. ACS 5-Year Estimates (detail tables) — the socioeconomic need indicators:
   - B01003_001E                total population
   - B17001_002E / B17001_001E  population below poverty / poverty universe
   - B19013_001E                median household income
   - B05002_013E                foreign-born population
   - B15003_001E, _022E-_025E   education universe 25+, bachelor's and above
   - B23025_003E / B23025_005E  civilian labor force / unemployed

2. Every community defined in config.json's population_groups — pulled from
   ACS tables B02015 (Asian groups), B02024 (Middle Eastern / North African),
   B03001 (Hispanic or Latino origin), and B02001 (broad race categories).
   All groups come from the same requests, so adding one is free.

3. ACS subject table S1602 — limited English speaking households, as a
   ready-made percentage (S1602_C04_001E).

4. County Business Patterns (NAICS 5411 "Legal Services") — the SUPPLY side:
   how many legal-services establishments exist in each county. Counties
   missing from this dataset have zero.

The Census API caps each request at 50 variables, so the detail-table pull is
automatically split into batches and merged back together on county FIPS.

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
MAX_VARS_PER_REQUEST = 45          # Census hard limit is 50; leave headroom
MISSING_SENTINEL_FLOOR = -100000   # Census uses e.g. -666666666 for "no estimate"

with open(os.path.join(HERE, "config.json")) as f:
    CONFIG = json.load(f)

ACS_YEAR = CONFIG["acs_year"]
CBP_YEAR = CONFIG["cbp_year"]
EXCLUDE_STATES = set(CONFIG["exclude_states"].keys())
GROUPS = CONFIG["population_groups"]

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

# Every distinct ACS variable used by any community group
group_vars = sorted({v for g in GROUPS for v in g["variables"]})


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
    response = requests.get(url, params=dict(params, key=API_KEY), timeout=180)
    response.raise_for_status()
    data = response.json()
    return pd.DataFrame(data[1:], columns=data[0])


def census_get_batched(url, variables, geo_params, include_name=False):
    """
    Pull `variables` for every US county, splitting into batches that respect
    the API's 50-variable cap and merging the batches on state+county.
    """
    frame = None
    for start in range(0, len(variables), MAX_VARS_PER_REQUEST):
        batch = variables[start:start + MAX_VARS_PER_REQUEST]
        get_list = (["NAME"] if include_name and frame is None else []) + batch
        print(f"    batch {start // MAX_VARS_PER_REQUEST + 1}: {len(batch)} variables")
        part = census_get(url, dict(geo_params, get=",".join(get_list)))
        frame = part if frame is None else frame.merge(
            part, on=["state", "county"], how="outer"
        )
    return frame


# ---------------------------------------------------------------------------
# 1 + 2. ACS detail tables: need indicators and every community group
# ---------------------------------------------------------------------------

print(f"Pulling ACS {ACS_YEAR} 5-year detail tables for every US county")
print(f"  {len(DETAIL_VARS)} need indicators + {len(group_vars)} community variables "
      f"({len(GROUPS)} groups)")
df = census_get_batched(
    f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5",
    list(DETAIL_VARS) + group_vars,
    {"for": "county:*"},
    include_name=True,
)
print(f"  {len(df)} counties returned")

# ---------------------------------------------------------------------------
# 3. ACS subject table S1602: limited English speaking households (percent)
# ---------------------------------------------------------------------------

print(f"Pulling ACS {ACS_YEAR} subject table S1602 (limited English households)")
lep_df = census_get(
    f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5/subject",
    {"get": "S1602_C01_001E,S1602_C04_001E", "for": "county:*"},
)
df = df.merge(lep_df, on=["state", "county"], how="left")

# ---------------------------------------------------------------------------
# 4. County Business Patterns: legal-services establishments (NAICS 5411)
# ---------------------------------------------------------------------------

print(f"Pulling CBP {CBP_YEAR} legal-services establishments (NAICS 5411)")
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

df = df[~df["state"].isin(EXCLUDE_STATES)].copy()

df = df.rename(columns=DETAIL_VARS)
df = df.rename(columns={
    "NAME": "county_name",
    "S1602_C01_001E": "total_households",
    "S1602_C04_001E": "limited_english_households_pct",
})

numeric_cols = (
    list(DETAIL_VARS.values())
    + ["total_households", "limited_english_households_pct",
       "legal_services_establishments"]
    + group_vars
)
for col in numeric_cols:
    df[col] = pd.to_numeric(df[col], errors="coerce")

# Treat the Census "no estimate available" sentinels as missing
df[numeric_cols] = df[numeric_cols].where(df[numeric_cols] > MISSING_SENTINEL_FLOOR)

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

# ---------------------------------------------------------------------------
# Save: county indicators (wide) + community populations (long)
# ---------------------------------------------------------------------------

os.makedirs(os.path.join(HERE, "data"), exist_ok=True)

indicator_cols = [
    "county_fips", "county_name", "state_name",
    "total_population", "total_households",
    "poverty_rate_pct", "median_household_income", "unemployment_rate_pct",
    "bachelors_or_higher_pct", "limited_english_households_pct",
    "foreign_born_pct",
    "legal_services_establishments", "legal_services_per_10k",
]
indicators_path = os.path.join(HERE, "data", "census_county_data.csv")
df[indicator_cols].to_csv(indicators_path, index=False)

# One row per county per community group, so the dashboard can filter by any
# group without the schema needing a column per group.
records = []
for g in GROUPS:
    total = df[g["variables"]].sum(axis=1, min_count=1)
    records.append(pd.DataFrame({
        "county_fips": df["county_fips"],
        "group_key": g["key"],
        "group_label": g["label"],
        "group_category": g["category"],
        "population": total.fillna(0).round().astype("int64"),
    }))
pops = pd.concat(records, ignore_index=True)
pops_path = os.path.join(HERE, "data", "census_county_populations.csv")
pops.to_csv(pops_path, index=False)

print(f"\nSaved {len(df)} counties across {df['state_name'].nunique()} states/DC")
print(f"  -> {indicators_path}")
print(f"  -> {pops_path}  ({len(pops):,} rows, {len(GROUPS)} communities)")
print("\nNext step: python build_database.py")
