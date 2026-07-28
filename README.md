# Access to Justice Analytics

A Python + SQL analytics tool that identifies **legal aid deserts** across all
50 states and DC by cross-referencing socioeconomic and demographic indicators
— poverty rate, median household income, unemployment, educational attainment,
limited English proficiency, and foreign-born population — against the actual
supply of legal services in each county.

The output is a 0–100 **desert score** for every US county, giving legal
service providers, bar associations, and affinity legal organizations (like
SABA-NA and SAAJCO) a data-driven way to direct pro bono outreach. A
configurable **population overlay** filters the results by a specific
community's presence — the default overlay is the South Asian population — so
you can see where general legal-aid need intersects with the community you
serve.

## Data sources (US Census Bureau APIs)

| Source | What it provides |
|---|---|
| ACS 5-Year Estimates (2024) | County-level poverty, income, unemployment, education, foreign-born population |
| ACS Subject Table S1602 | Percent of limited-English-speaking households per county |
| ACS Table B02015 | South Asian population by detailed group (Asian Indian, Bangladeshi, Bhutanese, Nepalese, Pakistani, Sikh, Sri Lankan) — the default overlay |
| County Business Patterns (2023), NAICS 5411 | Number of legal-services establishments per county — the supply side |

## How the desert score works

Every indicator is converted to a national percentile rank, oriented so higher
always means more need (low income and low educational attainment rank high,
for example). Then:

- **need_index** — average of the six socioeconomic percentiles (0–100)
- **supply_gap_index** — percentile of how *few* legal-services establishments
  exist per 10,000 residents (0–100). About 650 US counties have **zero**
  legal-services establishments.
- **desert_score = 60% need + 40% supply gap**

A county scoring 90 has more combined need and less legal-service coverage
than roughly 90% of US counties. Counties under 1,000 residents are excluded
(their survey estimates are too noisy to rank fairly). The scoring lives
entirely in [sql/desert_scores.sql](sql/desert_scores.sql), so the methodology
is transparent and easy to adjust.

## Setup

1. Install Python 3, then the two dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Get a free Census API key at
   <https://api.census.gov/data/key_signup.html>
3. Copy `.env.example` to a new file named `.env` and paste your key in.

## The dashboard

![Dashboard preview](docs/preview.png)

`docs/index.html` is an interactive dashboard — open it in any browser (no
server needed). It contains:

1. **A national county map** of all 3,110 scored counties. Hover any county for
   its full indicator breakdown: population, poverty rate, median income,
   unemployment, limited-English households, number of legal-services offices,
   and overlay-community population.
2. **A need-vs-supply scatter** — counties in the upper-left carry the most
   need with the fewest legal-services offices per resident.
3. **A ranked table** of the 20 most severe deserts.
4. **The overlay-community view** — where the configured community (default:
   South Asian) lives inside the worst deserts. This is the chart an affinity
   legal organization would use to pick outreach targets.
5. **A state-level ranking** by average county desert score.

Every number and chart is generated from a SQL query against `legal_aid.db`;
the queries are inline at the top of `build_dashboard.py` so you can see
exactly what feeds each visual.

To publish it as a live public link, go to the repo's **Settings → Pages** and
set Source to the `main` branch, `/docs` folder. GitHub then serves the
dashboard at `https://kavyaramkumar.github.io/Access-to-Justice-Analytics/`.

## Running it

```
python pull_census_data.py     # pulls ~3,100 counties from the Census APIs -> data/
python build_database.py       # builds legal_aid.db and scores every county -> outputs/
python build_dashboard.py      # builds the interactive dashboard -> docs/index.html
```

The ranked results land in `outputs/legal_aid_desert_scores.csv` (open it in
Excel) and in the `legal_aid.db` SQLite database. Ready-made queries — top
deserts nationally, deserts intersecting the overlay community, zero-provider
counties, state summaries — are in
[sql/analysis_queries.sql](sql/analysis_queries.sql):

```
sqlite3 legal_aid.db < sql/analysis_queries.sql
```

## Changing the population overlay

Edit `config.json` and set `population_overlay.active_group` to any group
defined under `groups` (South Asian, Hispanic or Latino, and Black or African
American are included). To add your own group, list the ACS variable codes for
it — browse them at <https://api.census.gov/data/2024/acs/acs5/variables.html>
— then re-run both scripts.

## Project structure

```
config.json               data years + population overlay definitions
pull_census_data.py       step 1: pull county data from the Census APIs
build_database.py         step 2: load SQLite, score, export rankings
sql/schema.sql            database schema
sql/desert_scores.sql     the desert-score methodology (all SQL)
sql/analysis_queries.sql  example analyses
outputs/                  ranked desert scores (committed for reference)
data/                     raw pulled data (generated, not committed)
```
