-- Database schema for the Access to Justice Analytics project.
-- One row per US county (50 states + DC), loaded from data/census_county_data.csv
-- by build_database.py.

DROP TABLE IF EXISTS county_indicators;

CREATE TABLE county_indicators (
    county_fips                     TEXT PRIMARY KEY,  -- 5-digit state+county FIPS code
    county_name                     TEXT NOT NULL,
    state_name                      TEXT NOT NULL,

    -- Population base
    total_population                INTEGER,
    total_households                INTEGER,

    -- Socioeconomic need indicators (ACS 5-year estimates)
    poverty_rate_pct                REAL,   -- higher = more need
    median_household_income         REAL,   -- lower  = more need
    unemployment_rate_pct           REAL,   -- higher = more need
    bachelors_or_higher_pct         REAL,   -- lower  = more need
    limited_english_households_pct  REAL,   -- higher = more need
    foreign_born_pct                REAL,   -- higher = more need

    -- Legal services supply (County Business Patterns, NAICS 5411)
    legal_services_establishments   INTEGER,
    legal_services_per_10k          REAL,   -- lower = bigger supply gap

    -- Configurable population overlay (which group it is lives in run_metadata)
    overlay_population              INTEGER,
    overlay_pct                     REAL
);

-- Records which overlay group and data years produced this database
DROP TABLE IF EXISTS run_metadata;

CREATE TABLE run_metadata (
    key   TEXT PRIMARY KEY,
    value TEXT
);
