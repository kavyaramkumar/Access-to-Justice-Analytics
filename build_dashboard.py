"""
Step 3 of 3: Build the visual dashboard.

Every number and chart on the dashboard comes from a SQL query against
legal_aid.db (the queries are inline below so you can see exactly what feeds
each visual). Produces a single self-contained page:

    docs/index.html

Open that file in any browser. It is also laid out so GitHub Pages can serve
it as a live public URL (Settings -> Pages -> Source: main branch, /docs).

Charts:
  1. National county choropleth map of desert scores (all 3,100+ counties)
  2. State ranking bar chart by average desert score
  3. Need vs. legal-services-supply scatter, sized by population
  4. Overlay-community intersection: where the configured community lives
     inside the worst deserts
  5. Table of the 20 most severe deserts
"""

import json
import os
import sqlite3
import sys

import pandas as pd
import requests
import plotly.express as px
import plotly.graph_objects as go

HERE = os.path.dirname(os.path.abspath(__file__))

# Desert scores are percentile ranks, so they spread evenly from 0 to 100.
# That means any straight linear ramp paints the median county at the middle of
# the scale — with a plain red scale most of the map comes out medium red and
# the genuinely severe deserts stop standing out. So this ramp deliberately
# breaks cool-to-warm at the midpoint (a county reading warm is above-average
# need at a glance) and reserves the darkest reds for the top ~15%.
DESERT_SCALE = [
    [0.00, "#f7fafb"],
    [0.25, "#e4ecf2"],
    [0.45, "#ffe9c4"],
    [0.60, "#fcc276"],
    [0.72, "#f79245"],
    [0.83, "#e85a25"],
    [0.92, "#c02418"],
    [1.00, "#6b0a10"],
]

COUNTY_GEOJSON_URL = (
    "https://raw.githubusercontent.com/plotly/datasets/master/"
    "geojson-counties-fips.json"
)

with open(os.path.join(HERE, "config.json")) as f:
    CONFIG = json.load(f)

OVERLAY_NAME = CONFIG["population_overlay"]["active_group"]
OVERLAY_LABEL = OVERLAY_NAME.replace("_", " ").title()

db_path = os.path.join(HERE, "legal_aid.db")
if not os.path.exists(db_path):
    sys.exit("legal_aid.db not found — run pull_census_data.py then build_database.py first.")

conn = sqlite3.connect(db_path)


def q(sql):
    return pd.read_sql_query(sql, conn)


# ---------------------------------------------------------------------------
# SQL: pull everything the dashboard needs
# ---------------------------------------------------------------------------

counties = q("""
    SELECT county_fips, county_name, state_name, total_population,
           poverty_rate_pct, median_household_income, unemployment_rate_pct,
           bachelors_or_higher_pct, limited_english_households_pct,
           foreign_born_pct, legal_services_establishments,
           legal_services_per_10k, overlay_population, overlay_pct,
           need_index, supply_gap_index, desert_score
    FROM desert_scores
""")

headline = q("""
    SELECT COUNT(*)                                                AS counties_scored,
           COUNT(DISTINCT CASE WHEN state_name <> 'District of Columbia'
                               THEN state_name END)                AS states,
           SUM(CASE WHEN legal_services_establishments = 0 THEN 1 ELSE 0 END)
                                                                   AS zero_provider_counties,
           SUM(CASE WHEN desert_score >= 75 THEN total_population ELSE 0 END)
                                                                   AS pop_in_severe_deserts,
           SUM(CASE WHEN legal_services_establishments = 0 THEN total_population ELSE 0 END)
                                                                   AS pop_with_no_provider
    FROM desert_scores
""").iloc[0]

by_state = q("""
    SELECT state_name,
           COUNT(*)                    AS counties,
           ROUND(AVG(desert_score), 1) AS avg_desert_score,
           SUM(CASE WHEN desert_score >= 75 THEN 1 ELSE 0 END) AS severe_counties
    FROM desert_scores
    GROUP BY state_name
    ORDER BY avg_desert_score DESC
""")

worst = q("""
    SELECT county_name, state_name, total_population, poverty_rate_pct,
           median_household_income, legal_services_establishments, desert_score
    FROM desert_scores
    ORDER BY desert_score DESC
    LIMIT 20
""")

overlay = q("""
    SELECT county_name, state_name, total_population,
           overlay_population, overlay_pct, desert_score
    FROM desert_scores
    WHERE overlay_population >= 1000
    ORDER BY desert_score DESC
    LIMIT 20
""")

conn.close()

# ---------------------------------------------------------------------------
# County boundaries (cached locally after the first download)
# ---------------------------------------------------------------------------

os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
geo_path = os.path.join(HERE, "data", "geojson-counties-fips.json")
if not os.path.exists(geo_path):
    print("Downloading county boundary file (one time, ~3 MB)...")
    r = requests.get(COUNTY_GEOJSON_URL, timeout=120)
    r.raise_for_status()
    geo = r.json()
    with open(geo_path, "w") as f:
        json.dump(geo, f)
else:
    with open(geo_path) as f:
        geo = json.load(f)

counties["county_fips"] = counties["county_fips"].astype(str).str.zfill(5)
counties["short_name"] = counties["county_name"].str.replace(
    r",.*$", "", regex=True
)

# ---------------------------------------------------------------------------
# Chart 1: national choropleth
# ---------------------------------------------------------------------------

print("Building national desert map...")
map_fig = px.choropleth(
    counties,
    geojson=geo,
    locations="county_fips",
    color="desert_score",
    scope="usa",
    color_continuous_scale=DESERT_SCALE,
    range_color=(0, 100),
    custom_data=["short_name", "state_name", "desert_score", "total_population",
                 "poverty_rate_pct", "median_household_income",
                 "unemployment_rate_pct", "limited_english_households_pct",
                 "legal_services_establishments", "overlay_population"],
    labels={"desert_score": "Desert score"},
)
map_fig.update_traces(
    marker_line_width=0.15,
    marker_line_color="rgba(255,255,255,0.5)",
    hovertemplate=(
        "<b>%{customdata[0]} County, %{customdata[1]}</b><br>"
        "Desert score: <b>%{customdata[2]:.1f}</b> / 100<br>"
        "<br>"
        "Population: %{customdata[3]:,.0f}<br>"
        "Poverty rate: %{customdata[4]:.1f}%<br>"
        "Median income: $%{customdata[5]:,.0f}<br>"
        "Unemployment: %{customdata[6]:.1f}%<br>"
        "Limited-English households: %{customdata[7]:.1f}%<br>"
        "Legal services offices: %{customdata[8]:,.0f}<br>"
        f"{OVERLAY_LABEL} residents: " + "%{customdata[9]:,.0f}"
        "<extra></extra>"
    ),
)
map_fig.update_geos(
    visible=False, showsubunits=True, subunitcolor="rgba(45,45,45,0.75)",
    subunitwidth=1.0,
)
map_fig.update_layout(
    margin=dict(l=0, r=0, t=0, b=0),
    height=620,
    coloraxis_colorbar=dict(
        title="Desert<br>score", thickness=14, len=0.75, x=0.98,
        ticks="outside", tickvals=[0, 25, 50, 75, 100],
    ),
    dragmode="pan",
)

# ---------------------------------------------------------------------------
# Chart 2: state ranking
# ---------------------------------------------------------------------------

state_fig = px.bar(
    by_state.sort_values("avg_desert_score"),
    x="avg_desert_score", y="state_name", orientation="h",
    color="avg_desert_score", color_continuous_scale=DESERT_SCALE,
    range_color=(0, 100),
    custom_data=["counties", "severe_counties"],
)
state_fig.update_traces(hovertemplate=(
    "<b>%{y}</b><br>Average desert score: %{x:.1f}<br>"
    "Counties: %{customdata[0]}<br>"
    "Severe deserts (score 75+): %{customdata[1]}<extra></extra>"
))
state_fig.update_layout(
    height=1000, margin=dict(l=0, r=0, t=10, b=40),
    xaxis_title="Average county desert score", yaxis_title=None,
    coloraxis_showscale=False, bargap=0.25,
)

# ---------------------------------------------------------------------------
# Chart 3: need vs. supply
# ---------------------------------------------------------------------------

scatter = counties.copy()
scatter["legal_services_per_10k_clipped"] = scatter["legal_services_per_10k"].clip(upper=12)
supply_fig = px.scatter(
    scatter,
    x="legal_services_per_10k_clipped", y="need_index",
    size="total_population", size_max=42,
    color="desert_score", color_continuous_scale=DESERT_SCALE, range_color=(0, 100),
    custom_data=["short_name", "state_name", "total_population",
                 "legal_services_per_10k", "need_index", "desert_score"],
)
supply_fig.update_traces(
    marker=dict(opacity=0.62, line=dict(width=0.4, color="rgba(0,0,0,0.35)")),
    hovertemplate=(
        "<b>%{customdata[0]} County, %{customdata[1]}</b><br>"
        "Population: %{customdata[2]:,.0f}<br>"
        "Legal offices per 10k residents: %{customdata[3]:.2f}<br>"
        "Need index: %{customdata[4]:.1f}<br>"
        "Desert score: %{customdata[5]:.1f}<extra></extra>"
    ),
)
supply_fig.add_annotation(
    x=0.35, y=97, text="highest need, fewest lawyers", showarrow=False,
    font=dict(size=11, color="#8a1c1c"), xanchor="left",
)
supply_fig.update_layout(
    height=520, margin=dict(l=0, r=0, t=30, b=40),
    xaxis_title="Legal services offices per 10,000 residents (capped at 12)",
    yaxis_title="Socioeconomic need index (0-100)",
    coloraxis_colorbar=dict(title="Desert<br>score", thickness=14, len=0.8),
)

# ---------------------------------------------------------------------------
# Chart 4: overlay community intersection
# ---------------------------------------------------------------------------

ov = overlay.sort_values("desert_score")
ov["short_label"] = (
    ov["county_name"].str.split(",").str[0].str.replace(" County", "", regex=False)
    + ", " + ov["state_name"]
)
overlay_fig = go.Figure()
overlay_fig.add_trace(go.Bar(
    x=ov["desert_score"], y=ov["short_label"], orientation="h",
    marker=dict(color=ov["desert_score"], colorscale=DESERT_SCALE, cmin=0, cmax=100),
    customdata=ov[["overlay_population", "overlay_pct", "total_population"]].values,
    hovertemplate=(
        "<b>%{y}</b><br>Desert score: %{x:.1f}<br>"
        f"{OVERLAY_LABEL} residents: " + "%{customdata[0]:,.0f} "
        "(%{customdata[1]:.2f}% of county)<br>"
        "County population: %{customdata[2]:,.0f}<extra></extra>"
    ),
))
overlay_fig.update_layout(
    height=620, margin=dict(l=0, r=0, t=10, b=40),
    xaxis_title="Desert score", yaxis_title=None, bargap=0.28,
)

# ---------------------------------------------------------------------------
# Assemble the page
# ---------------------------------------------------------------------------

CSS = """
:root {
  --ink: #1a1a1a; --muted: #5f6672; --line: #e3e6ea;
  --accent: #8a1c1c; --bg: #ffffff; --panel: #fafbfc;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  line-height: 1.55;
}
.wrap { max-width: 1180px; margin: 0 auto; padding: 44px 28px 80px; }
header { border-bottom: 3px solid var(--ink); padding-bottom: 22px; margin-bottom: 34px; }
h1 { font-size: 2.1rem; margin: 0 0 10px; letter-spacing: -0.02em; }
.sub { color: var(--muted); font-size: 1.02rem; max-width: 780px; margin: 0; }
.meta { color: var(--muted); font-size: 0.82rem; margin-top: 14px; }
.kpis {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 14px; margin: 0 0 42px;
}
.kpi {
  border: 1px solid var(--line); border-radius: 10px; padding: 18px 20px;
  background: var(--panel);
}
.kpi .n { font-size: 1.85rem; font-weight: 650; letter-spacing: -0.02em; }
.kpi .l { font-size: 0.8rem; color: var(--muted); margin-top: 4px; }
section { margin-bottom: 52px; }
h2 { font-size: 1.32rem; margin: 0 0 6px; letter-spacing: -0.01em; }
h2 .num { color: var(--accent); font-variant-numeric: tabular-nums; margin-right: 8px; }
.note { color: var(--muted); font-size: 0.93rem; margin: 0 0 18px; max-width: 830px; }
.chart { border: 1px solid var(--line); border-radius: 10px; padding: 8px; overflow: hidden; }
table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
th, td { padding: 9px 12px; text-align: right; border-bottom: 1px solid var(--line); }
th { font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.05em;
     color: var(--muted); font-weight: 600; }
td:first-child, th:first-child, td:nth-child(2), th:nth-child(2) { text-align: left; }
tbody tr:hover { background: var(--panel); }
.score { font-weight: 650; color: var(--accent); font-variant-numeric: tabular-nums; }
.method { background: var(--panel); border: 1px solid var(--line);
          border-radius: 10px; padding: 22px 26px; font-size: 0.92rem; }
.method h2 { font-size: 1.1rem; }
.method code { background: #eceff2; padding: 1px 5px; border-radius: 4px; font-size: 0.86em; }
.method ul { margin: 10px 0 0; padding-left: 20px; }
.method li { margin-bottom: 6px; }
footer { border-top: 1px solid var(--line); padding-top: 18px; color: var(--muted);
         font-size: 0.82rem; }
@media (max-width: 640px) { .wrap { padding: 28px 16px 60px; } h1 { font-size: 1.6rem; } }
"""


def fig_html(fig, first=False):
    return fig.to_html(
        full_html=False,
        include_plotlyjs="cdn" if first else False,
        config={"displayModeBar": False, "scrollZoom": False,
                "responsive": True},
    )


rows = "\n".join(
    f"<tr><td>{r.county_name.split(',')[0].replace(' County', '')}</td>"
    f"<td>{r.state_name}</td>"
    f"<td>{r.total_population:,.0f}</td>"
    f"<td>{r.poverty_rate_pct:.1f}%</td>"
    f"<td>{'—' if pd.isna(r.median_household_income) else f'${r.median_household_income:,.0f}'}</td>"
    f"<td>{r.legal_services_establishments:,.0f}</td>"
    f"<td class='score'>{r.desert_score:.1f}</td></tr>"
    for r in worst.itertuples()
)

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Access to Justice Analytics — US Legal Aid Deserts</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">

<header>
  <h1>Legal Aid Deserts in the United States</h1>
  <p class="sub">Every county in the 50 states and DC, scored on how much
  unmet legal need it carries and how little legal-services capacity exists
  there to meet it. Built to help legal service providers, bar associations,
  and affinity legal organizations direct pro bono outreach where it lands
  hardest.</p>
  <p class="meta">Sources: US Census Bureau American Community Survey
  {CONFIG['acs_year']} 5-year estimates &middot; County Business Patterns
  {CONFIG['cbp_year']} (NAICS 5411, Legal Services). Population overlay:
  {OVERLAY_LABEL}.</p>
</header>

<div class="kpis">
  <div class="kpi"><div class="n">{headline.counties_scored:,}</div>
    <div class="l">Counties scored across {headline.states} states &amp; DC</div></div>
  <div class="kpi"><div class="n">{counties['desert_score'].max():.0f}</div>
    <div class="l">Highest desert score
    ({worst.iloc[0].county_name.split(',')[0].replace(' County', '')},
    {worst.iloc[0].state_name})</div></div>
  <div class="kpi"><div class="n">{headline.zero_provider_counties:,}</div>
    <div class="l">Counties with <em>zero</em> legal services offices</div></div>
  <div class="kpi"><div class="n">{headline.pop_with_no_provider/1e6:.1f}M</div>
    <div class="l">People living in a county with no legal services office</div></div>
  <div class="kpi"><div class="n">{headline.pop_in_severe_deserts/1e6:.1f}M</div>
    <div class="l">People in severe deserts (score 75+)</div></div>
</div>

<section>
  <h2><span class="num">01</span>The national picture</h2>
  <p class="note">Darker counties combine higher socioeconomic need with
  thinner legal-services coverage. Hover any county for its full indicator
  breakdown. The deepest deserts cluster along the Texas&ndash;Mexico border,
  the Mississippi Delta, Appalachian Kentucky and West Virginia, the Black
  Belt across Alabama and Georgia, and tribal counties in the Dakotas,
  Arizona, and New Mexico.</p>
  <div class="chart">{fig_html(map_fig, first=True)}</div>
</section>

<section>
  <h2><span class="num">02</span>Need against actual legal-services supply</h2>
  <p class="note">Each dot is a county, sized by population. Counties in the
  upper-left carry the highest socioeconomic need while having the fewest
  legal-services offices per resident &mdash; these are the places where the
  gap between need and capacity is widest, and they are exactly what the
  desert score is designed to surface.</p>
  <div class="chart">{fig_html(supply_fig)}</div>
</section>

<section>
  <h2><span class="num">03</span>The 20 most severe legal aid deserts</h2>
  <p class="note">Ranked by desert score out of 100. A score of 90 means the
  county has more combined need and less legal-services coverage than roughly
  90% of all US counties.</p>
  <div class="chart" style="padding:4px 14px 6px">
  <table>
    <thead><tr><th>County</th><th>State</th><th>Population</th>
    <th>Poverty</th><th>Median income</th><th>Legal offices</th>
    <th>Desert score</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  </div>
</section>

<section>
  <h2><span class="num">04</span>Where {OVERLAY_LABEL} communities sit inside deserts</h2>
  <p class="note">The population overlay narrows the map to counties where a
  specific community has a meaningful presence &mdash; here, at least 1,000
  {OVERLAY_LABEL} residents &mdash; ranked by desert severity. This is the view
  an affinity legal organization would use to pick outreach targets. The
  overlay group is configurable in <code>config.json</code>.</p>
  <div class="chart">{fig_html(overlay_fig)}</div>
</section>

<section>
  <h2><span class="num">05</span>State-level ranking</h2>
  <p class="note">Average desert score across every county in each state.
  State averages weight small rural counties equally with large metros, so a
  high average signals deserts spread widely across the state rather than
  concentrated in a few places.</p>
  <div class="chart">{fig_html(state_fig)}</div>
</section>

<section class="method">
  <h2>How the desert score is calculated</h2>
  <p>Each indicator is converted to a national percentile rank in SQL, oriented
  so that higher always means more legal-aid need &mdash; low median income and
  low educational attainment therefore rank high. The two halves are then
  combined:</p>
  <ul>
    <li><strong>Need index (60% weight)</strong> &mdash; the average percentile
    across six indicators: poverty rate, median household income,
    unemployment rate, share of adults with a bachelor's degree or higher,
    share of limited-English-speaking households, and foreign-born share.</li>
    <li><strong>Supply gap index (40% weight)</strong> &mdash; the percentile of
    how <em>few</em> legal-services establishments exist per 10,000 residents,
    from County Business Patterns.</li>
  </ul>
  <p style="margin-top:14px"><code>desert_score = (0.6 &times; need_index) +
  (0.4 &times; supply_gap_index)</code></p>
  <p style="margin-top:14px">Counties under 1,000 residents are excluded
  because their survey estimates are too noisy to rank fairly. The entire
  calculation lives in <code>sql/desert_scores.sql</code> using window
  functions, so the methodology is auditable and the weights are easy to
  change.</p>
</section>

<footer>
  Access to Justice Analytics &middot; Python + SQL + Plotly &middot;
  data pulled directly from the US Census Bureau API.
  Establishment counts measure all legal-services businesses (NAICS 5411),
  which is a proxy for total legal capacity rather than a direct count of
  free or reduced-cost legal aid providers.
</footer>

</div>
</body>
</html>
"""

os.makedirs(os.path.join(HERE, "docs"), exist_ok=True)
out_path = os.path.join(HERE, "docs", "index.html")
with open(out_path, "w") as f:
    f.write(html)

size_mb = os.path.getsize(out_path) / 1e6
print(f"\nDashboard written -> {out_path}  ({size_mb:.1f} MB)")

# Static map image for the README (GitHub can't render the interactive HTML).
# Optional: needs `pip install kaleido`. Skipped cleanly if unavailable.
png_path = os.path.join(HERE, "docs", "preview.png")
try:
    still = go.Figure(map_fig)
    still.update_layout(
        title=dict(
            text="Legal Aid Deserts by County — US Census ACS "
                 f"{CONFIG['acs_year']} + CBP {CONFIG['cbp_year']}",
            x=0.5, xanchor="center", y=0.97,
            font=dict(size=17, color="#1a1a1a"),
        ),
        margin=dict(l=0, r=0, t=46, b=0),
        width=1200, height=700,
        paper_bgcolor="white",
    )
    still.write_image(png_path, scale=2)
    print(f"Static preview  -> {png_path}")
except Exception as exc:
    print(f"(Skipped static preview.png: {type(exc).__name__}: {exc})")

print("Open it in a browser, or publish it with GitHub Pages")
print("(Settings -> Pages -> Source: main branch, /docs folder).")
