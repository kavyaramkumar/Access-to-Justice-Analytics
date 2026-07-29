-- Database schema for the Access to Justice Analytics project.
-- Loaded from the data/ CSVs by build_database.py.
--
-- Design note: community population lives in its own table rather than as one
-- column per group. The desert score measures legal-aid need and supply, which
-- do not depend on which community you are looking at — so the score is
-- computed once, group-agnostic, and the dashboard JOINs to county_populations
-- to filter by community. Adding a new community is then a data change, not a
-- schema change.

DROP TABLE IF EXISTS county_indicators;

CREATE TABLE county_indicators (
    county_fips                     TEXT PRIMARY KEY,  -- 5-digit state+county FIPS
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
    legal_services_per_10k          REAL    -- lower = bigger supply gap
);


DROP TABLE IF EXISTS county_populations;

CREATE TABLE county_populations (
    county_fips     TEXT NOT NULL,
    group_key       TEXT NOT NULL,   -- e.g. 'south_asian', 'vietnamese'
    group_label     TEXT NOT NULL,   -- display name
    group_category  TEXT NOT NULL,   -- dropdown grouping in the dashboard
    population      INTEGER NOT NULL,
    pct_of_county   REAL,            -- filled in by build_database.py via JOIN
    PRIMARY KEY (county_fips, group_key),
    FOREIGN KEY (county_fips) REFERENCES county_indicators (county_fips)
);

CREATE INDEX idx_pops_group ON county_populations (group_key, population DESC);


-- Records the data years behind this build
DROP TABLE IF EXISTS run_metadata;

CREATE TABLE run_metadata (
    key   TEXT PRIMARY KEY,
    value TEXT
);
