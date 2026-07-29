"""
Step 3 of 3: Build the interactive dashboard.

Reads legal_aid.db and emits a single self-contained page:

    docs/index.html

Open it by double-clicking (everything is embedded, no server needed), or let
GitHub Pages serve it as a live URL (Settings -> Pages -> Source: main, /docs).

Rather than rendering fixed images, this exports the scored county data plus
each community's population into the page and drives Plotly from JavaScript.
That is what makes the filters live: switching community, re-weighting the
model, or toggling an indicator recomputes in the browser instead of requiring
the Python to be re-run.

Tabs:
  Overview     headline numbers and the national map
  Map          choose what to colour by, zoom to a state
  Communities  filter all 48 communities; see which are most desert-exposed
  Counties     searchable, sortable table of every county
  Method       how the score works, with live weighting controls

Also writes docs/preview.png (static map) for the README, since GitHub cannot
render the interactive page inline. Needs `pip install kaleido`; skipped
cleanly if that is unavailable.
"""

import json
import os
import sqlite3
import sys

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
COUNTY_GEOJSON_URL = (
    "https://raw.githubusercontent.com/plotly/datasets/master/"
    "geojson-counties-fips.json"
)

# The six socioeconomic need indicators, in the order shown in the UI.
# key -> (payload field, percentile field, display label, hover format)
NEED_INDICATORS = [
    ("pov",   "poverty_rate_pct",               "pr_poverty",          "Poverty rate"),
    ("inc",   "median_household_income",        "pr_low_income",       "Low median income"),
    ("unemp", "unemployment_rate_pct",          "pr_unemployment",     "Unemployment"),
    ("edu",   "bachelors_or_higher_pct",        "pr_low_education",    "Low education"),
    ("lep",   "limited_english_households_pct", "pr_limited_english",  "Limited English"),
    ("fb",    "foreign_born_pct",               "pr_foreign_born",     "Foreign-born share"),
]

with open(os.path.join(HERE, "config.json")) as f:
    CONFIG = json.load(f)

db_path = os.path.join(HERE, "legal_aid.db")
if not os.path.exists(db_path):
    sys.exit("legal_aid.db not found — run pull_census_data.py then build_database.py first.")

conn = sqlite3.connect(db_path)
counties = pd.read_sql_query(
    "SELECT * FROM desert_scores ORDER BY county_fips", conn
)
pops = pd.read_sql_query("""
    SELECT p.county_fips, p.group_key, p.group_label, p.group_category, p.population
    FROM county_populations p
    JOIN desert_scores d ON d.county_fips = p.county_fips
""", conn)
conn.close()

counties["county_fips"] = counties["county_fips"].astype(str).str.zfill(5)
pops["county_fips"] = pops["county_fips"].astype(str).str.zfill(5)

# Short display name: "Zapata County, Texas" -> "Zapata"
counties["short_name"] = (
    counties["county_name"].str.split(",").str[0]
    .str.replace(r"\s+(County|Parish|Borough|Census Area|Municipality|City and Borough"
                 r"|City|Municipio)$", "", regex=True)
)

print(f"Building dashboard for {len(counties):,} counties, "
      f"{pops['group_key'].nunique()} communities")

# ---------------------------------------------------------------------------
# County boundaries (cached locally after the first download)
# ---------------------------------------------------------------------------

os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
geo_path = os.path.join(HERE, "data", "geojson-counties-fips.json")
if not os.path.exists(geo_path):
    print("  downloading county boundary file (one time, ~3 MB)")
    r = requests.get(COUNTY_GEOJSON_URL, timeout=180)
    r.raise_for_status()
    geo = r.json()
    with open(geo_path, "w") as f:
        json.dump(geo, f)
else:
    with open(geo_path) as f:
        geo = json.load(f)

# ---------------------------------------------------------------------------
# Patch in counties the boundary file predates.
#
# The Plotly boundary file is a few years old, so it is missing every county
# whose FIPS code has changed since: Connecticut replaced its eight counties
# with nine planning regions in 2022, Alaska reorganised three census areas,
# and Shannon County SD became Oglala Lakota County. Those counties are all
# present in current ACS data, so without this they render as blank holes —
# and Oglala Lakota (Pine Ridge) is one of the most severe deserts in the
# country. Missing geometry is fetched from the Census TIGERweb service and
# cached, so this self-heals if FIPS codes change again.
# ---------------------------------------------------------------------------

TIGERWEB_COUNTIES = ("https://tigerweb.geo.census.gov/arcgis/rest/services/"
                     "TIGERweb/State_County/MapServer/37/query")


def thin(coords, places=3):
    """
    Round coordinates and drop consecutive duplicates. TIGERweb returns full
    survey-resolution geometry — about 25x more detail per county than the
    national file — which is wasted on a country-wide map.
    """
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], places), round(coords[1], places)]
    out = []
    for c in coords:
        t = thin(c, places)
        if not out or t != out[-1]:
            out.append(t)
    # Keep rings closed and legal after thinning
    if out and isinstance(out[0], list) and isinstance(out[0][0], float):
        if len(out) < 4:
            return None
        if out[0] != out[-1]:
            out.append(out[0])
    return out


def ring_is_ccw(ring):
    """Shoelace signed area; positive means counter-clockwise."""
    area = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        area += x1 * y2 - x2 * y1
    return area > 0


def fix_winding(geom):
    """
    Rewind rings to CLOCKWISE exteriors, matching the national boundary file.

    Plotly renders geo traces through d3-geo, which treats polygons as
    spherical: ring direction decides which side is "inside", so a ring wound
    the wrong way is drawn as the entire globe *minus* the shape. Every
    exterior ring in the national file is clockwise, and those render
    correctly. TIGERweb returns counter-clockwise exteriors, so patched
    counties must be reversed — otherwise a single Alaskan census area floods
    the whole map and hides every other county.

    (Note this is the opposite of the GeoJSON RFC 7946 right-hand rule. What
    matters here is consistency with the file Plotly already renders correctly.)
    """
    polys = ([geom["coordinates"]] if geom["type"] == "Polygon"
             else geom["coordinates"])
    fixed = []
    for poly in polys:
        rings = []
        for n, ring in enumerate(poly):
            # ring 0 is the exterior (clockwise); any others are holes
            want_ccw = (n != 0)
            rings.append(ring if ring_is_ccw(ring) == want_ccw else ring[::-1])
        fixed.append(rings)
    if geom["type"] == "Polygon":
        return {"type": "Polygon", "coordinates": fixed[0]}
    return {"type": "MultiPolygon", "coordinates": fixed}


def fetch_missing_boundaries(missing):
    patch_path = os.path.join(HERE, "data", "geojson-patch.json")
    cached = {}
    if os.path.exists(patch_path):
        with open(patch_path) as f:
            cached = {ft["id"]: ft for ft in json.load(f)}
    todo = sorted(set(missing) - set(cached))
    if todo:
        print(f"  fetching boundaries for {len(todo)} counties missing from the "
              f"base file (FIPS changes)")
        ids = ",".join(f"'{f}'" for f in todo)
        resp = requests.get(TIGERWEB_COUNTIES, params={
            "where": f"GEOID IN ({ids})", "outFields": "GEOID,NAME",
            "returnGeometry": "true", "outSR": "4326", "f": "geojson",
        }, timeout=180)
        resp.raise_for_status()
        for ft in resp.json().get("features", []):
            gid = ft["properties"]["GEOID"]
            geom = dict(ft["geometry"])
            simplified = thin(geom["coordinates"])
            if simplified:
                geom["coordinates"] = simplified
                cached[gid] = {"type": "Feature", "id": gid, "properties": {},
                               "geometry": fix_winding(geom)}
        with open(patch_path, "w") as f:
            json.dump(list(cached.values()), f)
    return cached


keep_fips = set(counties["county_fips"])
have = {ft.get("id") for ft in geo["features"]}
missing = keep_fips - have
if missing:
    patched = fetch_missing_boundaries(missing)
    geo["features"].extend(patched[f] for f in sorted(missing) if f in patched)
    still = sorted(missing - set(patched))
    if still:
        print(f"  WARNING: no boundary found for {len(still)} counties: "
              f"{', '.join(still)} — they will not appear on the map")

# Drop boundary features for counties we do not score, so the embedded geometry
# is not carrying territories and sub-1,000-population counties we never draw.
geo["features"] = [ft for ft in geo["features"] if ft.get("id") in keep_fips]
print(f"  {len(geo['features'])} county boundaries "
      f"({len(keep_fips - {ft['id'] for ft in geo['features']})} missing)")

# ---------------------------------------------------------------------------
# Build the data payload
# ---------------------------------------------------------------------------


def nums(series, digits=2):
    """Round for compactness and convert NaN to None (-> null in JSON)."""
    return [None if pd.isna(v) else round(float(v), digits) for v in series]


fips_index = {f: i for i, f in enumerate(counties["county_fips"])}

# Community populations are stored sparsely: most communities are absent from
# most counties, so we only ship the counties where the population is above
# zero. This roughly halves the payload versus 48 dense arrays.
group_payload = []
for (key, label, category), grp in pops.groupby(
    ["group_key", "group_label", "group_category"], sort=False
):
    nonzero = grp[grp["population"] > 0]
    idx = [fips_index[f] for f in nonzero["county_fips"] if f in fips_index]
    vals = [int(v) for f, v in zip(nonzero["county_fips"], nonzero["population"])
            if f in fips_index]
    group_payload.append({
        "k": key, "l": label, "c": category,
        "total": int(sum(vals)), "i": idx, "v": vals,
    })

# Preserve the config file's ordering so the dropdown reads sensibly
order = {g["key"]: n for n, g in enumerate(CONFIG["population_groups"])}
group_payload.sort(key=lambda g: order.get(g["k"], 999))

# ---------------------------------------------------------------------------
# Which community does the dashboard open on?
#
# Hard-coding one community here would be an editorial choice, so it is derived
# from the data instead: whichever community has the highest population-weighted
# desert score, i.e. is living in the least-served counties. config.json can
# pin a specific community if you are running this for one organisation.
# ---------------------------------------------------------------------------

DASHBOARD_CFG = CONFIG.get("dashboard") or {}

# A population-weighted score over a small community rests on a handful of
# counties and swings hard, so the derived default is drawn from communities
# large enough for the ranking to be stable. (The Communities tab's exposure
# chart uses a lower 50,000 floor — it is showing a ranking, not choosing a
# landing view, so volatility there is visible rather than misleading.)
DEFAULT_MIN_POPULATION = 250000

score_by_fips = dict(zip(counties["county_fips"], counties["desert_score"]))


def most_exposed_community():
    ranked = []
    for g in group_payload:
        if g["total"] < DEFAULT_MIN_POPULATION:
            continue
        weighted = total = 0.0
        for n, pop in zip(g["i"], g["v"]):
            score = score_by_fips.get(counties["county_fips"].iloc[n])
            if score is not None:
                weighted += pop * score
                total += pop
        if total:
            ranked.append((weighted / total, g["k"], g["l"]))
    if not ranked:
        return group_payload[0]["k"], group_payload[0]["l"], None
    score, key, label = max(ranked)
    return key, label, score


requested = DASHBOARD_CFG.get("default_community", "most_exposed")
if requested == "most_exposed":
    default_group, default_label, default_score = most_exposed_community()
    print(f"  default community: {default_label} "
          f"(most desert-exposed, weighted score {default_score:.1f})")
else:
    if requested not in {g["k"] for g in group_payload}:
        sys.exit(f"config.json dashboard.default_community='{requested}' is not "
                 f"a known group key")
    default_group = requested
    default_label = next(g["l"] for g in group_payload if g["k"] == requested)
    print(f"  default community: {default_label} (pinned by config)")

def state_bounds():
    """
    Bounding box [west, south, east, north] per state, derived from the county
    boundaries we ship. Used to zoom the map, because Plotly's fitbounds is
    ignored for geojson choropleths and silently leaves the view unchanged.
    """
    fips_to_state = dict(zip(counties["county_fips"], counties["state_name"]))
    acc = {}

    def walk(coords, out):
        # Polygon rings nest one level, MultiPolygon two; recurse to the leaves.
        if isinstance(coords[0], (int, float)):
            out.append(coords)
        else:
            for c in coords:
                walk(c, out)

    for ft in geo["features"]:
        st = fips_to_state.get(ft.get("id"))
        if not st:
            continue
        pts = []
        walk(ft["geometry"]["coordinates"], pts)
        acc.setdefault(st, []).extend(pts)

    out = {}
    for st, pts in acc.items():
        lons = [p[0] for p in pts]
        lats = [p[1] for p in pts]
        # Alaska's Aleutians cross the antimeridian, which would otherwise
        # produce a bounding box spanning the entire globe.
        if max(lons) - min(lons) > 100:
            neg = [x for x in lons if x < 0]
            if neg:
                lons = neg
        pad = max(0.35, (max(lons) - min(lons)) * 0.04)
        vpad = max(0.35, (max(lats) - min(lats)) * 0.04)
        out[st] = [round(min(lons) - pad, 3), round(min(lats) - vpad, 3),
                   round(max(lons) + pad, 3), round(max(lats) + vpad, 3)]
    return out


payload = {
    "stateBounds": state_bounds(),
    "meta": {
        "acs": CONFIG["acs_year"],
        "cbp": CONFIG["cbp_year"],
        "nCounties": len(counties),
        "nStates": int(counties.loc[
            counties["state_name"] != "District of Columbia", "state_name"].nunique()),
        "nGroups": len(group_payload),
        "zeroProvider": int((counties["legal_services_establishments"] == 0).sum()),
        "popNoProvider": int(counties.loc[
            counties["legal_services_establishments"] == 0, "total_population"].sum()),
    },
    "fips": list(counties["county_fips"]),
    "name": list(counties["short_name"]),
    "state": list(counties["state_name"]),
    "pop": [int(v) for v in counties["total_population"]],
    "est": [int(v) for v in counties["legal_services_establishments"]],
    "p10k": nums(counties["legal_services_per_10k"], 3),
    "prSupply": nums(counties["pr_supply_gap"], 5),
    "states": sorted(counties["state_name"].unique()),
    "groups": group_payload,
    "defaultGroup": default_group,
    "defaultIsDerived": requested == "most_exposed",
    "indicators": [{"k": k, "label": label} for k, _, _, label in NEED_INDICATORS],
}
for key, raw_col, pr_col, _label in NEED_INDICATORS:
    payload[key] = nums(counties[raw_col], 2)
    payload["pr_" + key] = nums(counties[pr_col], 5)

payload_json = json.dumps(payload, separators=(",", ":"))
geo_json = json.dumps(geo, separators=(",", ":"))

# ---------------------------------------------------------------------------
# Page assets
# ---------------------------------------------------------------------------

CSS = """
:root {
  --ink:#151719; --muted:#646b76; --faint:#8b929c;
  --line:#e2e6ea; --line2:#eef1f4;
  --accent:#a3231b; --accent2:#0b6b7a;
  --bg:#ffffff; --panel:#f8fafb; --panel2:#f2f5f7;
}
* { box-sizing:border-box; }
html { scroll-behavior:smooth; }
body {
  margin:0; background:var(--bg); color:var(--ink); line-height:1.55;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased;
}
.wrap { max-width:1220px; margin:0 auto; padding:0 26px 90px; }

header { padding:40px 0 0; }
h1 { font-size:2.05rem; margin:0 0 10px; letter-spacing:-0.025em; line-height:1.15; }
.sub { color:var(--muted); font-size:1.01rem; max-width:790px; margin:0; }
.srcline { color:var(--faint); font-size:0.79rem; margin-top:12px; }

/* Tabs */
.tabbar {
  display:flex; gap:2px; margin:26px 0 0; border-bottom:2px solid var(--line);
  overflow-x:auto; scrollbar-width:none;
}
.tabbar::-webkit-scrollbar { display:none; }
.tab {
  appearance:none; background:none; border:0; cursor:pointer;
  padding:11px 17px; font-size:0.93rem; font-weight:500; color:var(--muted);
  font-family:inherit; white-space:nowrap; position:relative; top:2px;
  border-bottom:3px solid transparent; transition:color .12s;
}
.tab:hover { color:var(--ink); }
.tab.on { color:var(--accent); border-bottom-color:var(--accent); font-weight:650; }
.panel { display:none; padding-top:30px; }
.panel.on { display:block; }

/* KPIs */
.kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(178px,1fr)); gap:13px; margin-bottom:34px; }
.kpi { border:1px solid var(--line); border-radius:11px; padding:17px 19px; background:var(--panel); }
.kpi .n { font-size:1.8rem; font-weight:660; letter-spacing:-0.025em; line-height:1.1; font-variant-numeric:tabular-nums; }
.kpi .l { font-size:0.79rem; color:var(--muted); margin-top:5px; }
.kpi.hl { background:#fdf3f2; border-color:#f0cdc9; }
.kpi.hl .n { color:var(--accent); }

/* Controls */
.controls {
  display:flex; flex-wrap:wrap; gap:16px 22px; align-items:flex-end;
  background:var(--panel); border:1px solid var(--line);
  border-radius:11px; padding:15px 18px; margin-bottom:20px;
}
.ctl { display:flex; flex-direction:column; gap:5px; }
.ctl label { font-size:0.72rem; text-transform:uppercase; letter-spacing:0.055em; color:var(--muted); font-weight:600; }
select, input[type=text] {
  font-family:inherit; font-size:0.9rem; padding:7px 10px; color:var(--ink);
  border:1px solid #ccd3da; border-radius:7px; background:#fff; min-width:170px;
}
select:focus, input[type=text]:focus { outline:2px solid var(--accent2); outline-offset:-1px; border-color:var(--accent2); }
input[type=range] { width:190px; accent-color:var(--accent); }
.ctl .val { font-size:0.8rem; color:var(--muted); font-variant-numeric:tabular-nums; }
.chips { display:flex; flex-wrap:wrap; gap:7px; }
.chip {
  font-size:0.79rem; padding:5px 11px; border-radius:20px; cursor:pointer;
  border:1px solid #ccd3da; background:#fff; color:var(--muted);
  font-family:inherit; transition:all .12s; user-select:none;
}
.chip.on { background:var(--accent); border-color:var(--accent); color:#fff; font-weight:550; }
.chip:hover { border-color:var(--accent); }
.btn {
  font-family:inherit; font-size:0.82rem; padding:7px 13px; cursor:pointer;
  border:1px solid #ccd3da; border-radius:7px; background:#fff; color:var(--ink);
}
.btn:hover { border-color:var(--accent); color:var(--accent); }
.flag {
  display:none; font-size:0.79rem; color:var(--accent); background:#fdf3f2;
  border:1px solid #f0cdc9; border-radius:7px; padding:8px 13px; margin-bottom:18px;
}
.flag.on { display:block; }

/* Sections */
section { margin-bottom:46px; }
h2 { font-size:1.28rem; margin:0 0 6px; letter-spacing:-0.015em; }
h2 .num { color:var(--accent); margin-right:9px; font-variant-numeric:tabular-nums; }
h3 { font-size:1.02rem; margin:0 0 5px; }
.note { color:var(--muted); font-size:0.92rem; margin:0 0 17px; max-width:840px; }
.chart { border:1px solid var(--line); border-radius:11px; padding:8px; }
.chart.tight { padding:2px 12px 6px; }
.two { display:grid; grid-template-columns:1fr 1fr; gap:20px; }
@media (max-width:860px) { .two { grid-template-columns:1fr; } }

/* Tables */
.scroll { max-height:620px; overflow:auto; }
table { width:100%; border-collapse:collapse; font-size:0.87rem; }
th, td { padding:8px 11px; text-align:right; border-bottom:1px solid var(--line2); white-space:nowrap; }
th {
  font-size:0.71rem; text-transform:uppercase; letter-spacing:0.05em; color:var(--muted);
  font-weight:600; position:sticky; top:0; background:#fff; cursor:pointer;
  border-bottom:1px solid var(--line); z-index:1;
}
th:hover { color:var(--accent); }
th.sorted::after { content:" \\25BC"; font-size:0.7em; }
th.sorted.asc::after { content:" \\25B2"; }
td.l, th.l { text-align:left; }
tbody tr:hover { background:var(--panel); }
.score { font-weight:650; color:var(--accent); font-variant-numeric:tabular-nums; }
.num { font-variant-numeric:tabular-nums; }
.zero { color:var(--accent); font-weight:600; }
.count { color:var(--muted); font-size:0.83rem; margin:10px 0 0; }

/* Method */
.method { background:var(--panel); border:1px solid var(--line); border-radius:11px; padding:24px 27px; }
.method h3 { margin-top:20px; }
.method h3:first-child { margin-top:0; }
code { background:var(--panel2); padding:1.5px 5px; border-radius:4px; font-size:0.85em;
       font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
.method ul { padding-left:20px; margin:9px 0; }
.method li { margin-bottom:7px; }
.formula {
  background:#fff; border:1px solid var(--line); border-radius:8px;
  padding:13px 16px; margin:14px 0; font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:0.86rem; overflow-x:auto;
}
.caveat { border-left:3px solid var(--accent); padding-left:14px; margin:16px 0 0; color:var(--muted); font-size:0.9rem; }
footer { border-top:1px solid var(--line); padding-top:18px; color:var(--faint); font-size:0.81rem; }
.loading { padding:60px 0; text-align:center; color:var(--faint); }
"""

BODY = """
<div class="wrap">
<header>
  <h1>Legal Aid Deserts in the United States</h1>
  <p class="sub">Every county in the 50 states and DC, scored on how much unmet
  legal need it carries against how little legal-services capacity exists there
  to meet it &mdash; then filterable by any of __NGROUPS__ communities, so you can
  see where general legal-aid need overlaps the population you serve.</p>
  <p class="srcline">US Census Bureau &middot; American Community Survey __ACS__
  5-year estimates &middot; County Business Patterns __CBP__ (NAICS 5411, Legal
  Services). __NCOUNTIES__ counties scored.</p>
</header>

<div class="tabbar" id="tabbar">
  <button class="tab on" data-t="overview">Overview</button>
  <button class="tab" data-t="map">Explore the map</button>
  <button class="tab" data-t="communities">Communities</button>
  <button class="tab" data-t="counties">County lookup</button>
  <button class="tab" data-t="method">How it works</button>
</div>

<!-- OVERVIEW -->
<div class="panel on" id="p-overview">
  <div class="kpis" id="kpis-overview"></div>
  <div class="flag" id="flag-overview"></div>
  <section>
    <h2><span class="num">01</span>The national picture</h2>
    <p class="note">Warmer counties combine higher socioeconomic need with
    thinner legal-services coverage. Hover any county for its full breakdown.
    The deepest deserts cluster along the Texas&ndash;Mexico border, the
    Mississippi Delta, Appalachian Kentucky and West Virginia, the Black Belt
    across Alabama and Georgia, and tribal counties in the Dakotas, Arizona and
    New Mexico.</p>
    <div class="chart"><div id="map-overview" style="height:600px"></div></div>
  </section>
  <section>
    <h2><span class="num">02</span>Need against actual legal-services supply</h2>
    <p class="note">Each dot is a county, sized by population. Counties toward
    the upper left carry the highest need while having the fewest
    legal-services offices per resident &mdash; the widest gaps between need and
    capacity, which is exactly what the desert score is built to surface.</p>
    <div class="chart"><div id="scatter" style="height:500px"></div></div>
  </section>
  <section>
    <h2><span class="num">03</span>The 20 most severe deserts</h2>
    <p class="note">A score of 90 means the county has more combined need and
    less legal-services coverage than roughly 90% of all US counties.</p>
    <div class="chart tight"><table id="tbl-worst"></table></div>
  </section>
</div>

<!-- MAP -->
<div class="panel" id="p-map">
  <div class="controls">
    <div class="ctl">
      <label for="m-color">Colour counties by</label>
      <select id="m-color">
        <option value="desert">Desert score (need + supply gap)</option>
        <option value="need">Need index only</option>
        <option value="supply">Supply gap only</option>
        <option value="pov">Poverty rate</option>
        <option value="inc">Median household income</option>
        <option value="unemp">Unemployment rate</option>
        <option value="lep">Limited-English households</option>
        <option value="fb">Foreign-born share</option>
        <option value="p10k">Legal offices per 10k residents</option>
        <option value="group">Selected community, % of county</option>
      </select>
    </div>
    <div class="ctl">
      <label for="m-state">Zoom to</label>
      <select id="m-state"><option value="">Whole country</option></select>
    </div>
    <div class="ctl">
      <label for="m-group">Community (for the community view)</label>
      <select id="m-group"></select>
    </div>
  </div>
  <div class="flag" id="flag-map"></div>
  <p class="note" id="map-note"></p>
  <div class="chart"><div id="map-main" style="height:660px"></div></div>
</div>

<!-- COMMUNITIES -->
<div class="panel" id="p-communities">
  <div class="controls">
    <div class="ctl">
      <label for="c-group">Community</label>
      <select id="c-group"></select>
    </div>
    <div class="ctl">
      <label for="c-rank">Rank counties by</label>
      <select id="c-rank">
        <option value="desert">Desert severity</option>
        <option value="pop">Community population</option>
        <option value="pct">Community share of county</option>
      </select>
    </div>
    <div class="ctl">
      <label for="c-min">Minimum community population</label>
      <select id="c-min">
        <option value="0">No minimum</option>
        <option value="100">100+</option>
        <option value="500">500+</option>
        <option value="1000" selected>1,000+</option>
        <option value="5000">5,000+</option>
        <option value="25000">25,000+</option>
      </select>
    </div>
  </div>
  <p class="note">__DEFAULTNOTE__</p>
  <div class="kpis" id="kpis-community"></div>
  <div class="flag" id="flag-communities"></div>
  <section>
    <h2><span class="num">01</span><span id="c-title-1"></span></h2>
    <p class="note" id="c-note-1"></p>
    <div class="chart"><div id="c-bars" style="height:620px"></div></div>
  </section>
  <section>
    <h2><span class="num">02</span>Which communities are most exposed to deserts?</h2>
    <p class="note">Population-weighted average desert score across every county
    a community lives in, so it reflects where people actually are rather than
    treating a 300-person county the same as Los Angeles. Communities above
    50 are concentrated in counties that are underserved on average. Only
    communities of 50,000+ nationally are shown, since small populations swing
    wildly. Your selection is highlighted.</p>
    <div class="chart"><div id="c-exposure" style="height:760px"></div></div>
  </section>
  <section>
    <h2><span class="num">03</span><span id="c-title-3"></span></h2>
    <p class="note">Every county meeting the population threshold, sorted by
    desert score. Click a column heading to re-sort.</p>
    <div class="chart tight"><div class="scroll"><table id="tbl-community"></table></div></div>
    <p class="count" id="c-count"></p>
  </section>
</div>

<!-- COUNTIES -->
<div class="panel" id="p-counties">
  <div class="controls">
    <div class="ctl">
      <label for="t-search">Search county or state</label>
      <input type="text" id="t-search" placeholder="e.g. Wayne, or Michigan" autocomplete="off">
    </div>
    <div class="ctl">
      <label for="t-state">State</label>
      <select id="t-state"><option value="">All states</option></select>
    </div>
    <div class="ctl">
      <label for="t-group">Show community column</label>
      <select id="t-group"></select>
    </div>
    <div class="ctl">
      <label>Filters</label>
      <div class="chips">
        <button class="chip" id="t-zero">No legal offices at all</button>
        <button class="chip" id="t-severe">Severe deserts only (75+)</button>
      </div>
    </div>
  </div>
  <div class="flag" id="flag-counties"></div>
  <p class="count" id="t-count"></p>
  <div class="chart tight"><div class="scroll"><table id="tbl-counties"></table></div></div>
</div>

<!-- METHOD -->
<div class="panel" id="p-method">
  <section>
    <h2>Adjust the model</h2>
    <p class="note">The score is not hard-coded. These controls recompute every
    county live, across all tabs, straight from the underlying percentile ranks
    &mdash; so you can see how sensitive the rankings are to the weighting, or
    build a score that reflects the kind of need your organisation handles.</p>
    <div class="controls">
      <div class="ctl">
        <label for="w-need">Need vs. supply gap weighting</label>
        <input type="range" id="w-need" min="0" max="100" step="5" value="60">
        <span class="val" id="w-need-val"></span>
      </div>
      <div class="ctl" style="flex:1; min-width:300px">
        <label>Need indicators included</label>
        <div class="chips" id="ind-chips"></div>
      </div>
      <div class="ctl">
        <label>&nbsp;</label>
        <button class="btn" id="w-reset">Reset to default</button>
      </div>
    </div>
    <div class="flag" id="flag-method"></div>
    <div class="two">
      <div>
        <h3>Score distribution</h3>
        <p class="note">How the __NCOUNTIES__ counties spread across the scale.</p>
        <div class="chart"><div id="m-hist" style="height:300px"></div></div>
      </div>
      <div>
        <h3>Most affected by your changes</h3>
        <p class="note">Counties that move most versus the default 60/40 weighting.</p>
        <div class="chart tight"><div class="scroll" style="max-height:300px"><table id="tbl-shift"></table></div></div>
      </div>
    </div>
  </section>

  <section class="method">
    <h3>How the desert score is calculated</h3>
    <p>Each indicator is converted to a national percentile rank in SQL,
    oriented so higher always means more legal-aid need &mdash; low median income
    and low educational attainment therefore rank high. The two halves are then
    combined:</p>
    <ul>
      <li><strong>Need index</strong> &mdash; the average percentile across six
      indicators: poverty rate, median household income, unemployment rate,
      share of adults with a bachelor's degree or higher, share of
      limited-English-speaking households, and foreign-born share.</li>
      <li><strong>Supply gap index</strong> &mdash; the percentile of how
      <em>few</em> legal-services establishments exist per 10,000 residents,
      from County Business Patterns.</li>
    </ul>
    <div class="formula">desert_score = (0.6 &times; need_index) + (0.4 &times; supply_gap_index)</div>
    <p>Counties under 1,000 residents are excluded, because their survey
    estimates are too noisy to rank fairly. The whole calculation lives in
    <code>sql/desert_scores.sql</code> using window functions, and that query
    keeps all seven percentile components in its output &mdash; which is what lets
    this page re-weight everything in the browser.</p>

    <h3>Why supply is measured, not just need</h3>
    <p>A wealthy county with no lawyers and a poor county with fifty lawyers are
    not the same problem. County Business Patterns counts legal-services
    establishments (NAICS 5411) per county, which turns "need" into "unmet
    need". <strong>__ZEROPROV__ counties have no legal-services establishment of
    any kind.</strong></p>

    <h3>Communities</h3>
    <p>The __NGROUPS__ communities come from ACS tables B02015 (Asian groups),
    B02024 (Middle Eastern and North African origins), B03001 (Hispanic or
    Latino origin) and B02001 (broad race categories). They are stored in their
    own <code>county_populations</code> table rather than as one column per
    group, so adding a community is a data change, not a schema change &mdash; edit
    <code>config.json</code> and re-run.</p>

    <div class="caveat">
    <strong>Reading the numbers carefully.</strong> Establishment counts cover
    all legal-services businesses, so they proxy total legal capacity rather
    than counting free or reduced-cost legal aid providers specifically &mdash; a
    county with commercial firms but no legal aid office will look better served
    than it is. ACS figures are 5-year survey estimates with margins of error
    that widen in small counties. The Middle Eastern and North African and
    Pacific Islander tables count "groups tallied", so someone reporting two
    origins is counted in both and those totals can exceed a county's
    population. And a desert score is a prioritisation aid, not a measure of
    any individual's access to justice.
    </div>
  </section>
</div>

<footer>
  Access to Justice Analytics &middot; Python, SQL and Plotly &middot; data pulled
  directly from the US Census Bureau API. Every figure on this page is derived
  from queries in <code>sql/</code> against a SQLite database built by
  <code>build_database.py</code>.
</footer>
</div>
"""

# JavaScript is kept as a plain (non-f) string so that its braces and template
# literals need no escaping; data reaches it through the JSON script tags.
JS = r"""
const D = JSON.parse(document.getElementById('payload').textContent);
const GEO = JSON.parse(document.getElementById('geojson').textContent);
const N = D.fips.length;
const IND = D.indicators.map(o => o.k);

// Desert scores are percentile ranks, so they spread evenly from 0 to 100 and a
// plain linear red ramp renders most of the map mid-red, burying the severe
// deserts. This ramp breaks cool-to-warm at the midpoint (warm = above-average
// need at a glance) and saves the darkest reds for the top ~15%.
const SCALE = [[0,'#f7fafb'],[0.25,'#e4ecf2'],[0.45,'#ffe9c4'],[0.60,'#fcc276'],
               [0.72,'#f79245'],[0.83,'#e85a25'],[0.92,'#c02418'],[1,'#6b0a10']];
const PLOT_FONT = {family:'-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif',
                   size:12, color:'#151719'};
const CFG = {displayModeBar:false, responsive:true};

// ---- model state -----------------------------------------------------------
const DEFAULT_W = 60;
let wNeed = DEFAULT_W;                 // percent weight on need
let onInd = new Set(IND);              // which need indicators are active
let need = [], desert = [], baseDesert = [];

function recompute() {
  const active = IND.filter(k => onInd.has(k));
  const w = wNeed / 100;
  need = new Array(N);
  desert = new Array(N);
  for (let i = 0; i < N; i++) {
    let s = 0, c = 0;
    for (const k of active) {
      const v = D['pr_' + k][i];
      if (v !== null) { s += v; c++; }
    }
    const nd = c ? (s / c) * 100 : 0;
    const sup = (D.prSupply[i] ?? 0) * 100;
    need[i] = nd;
    // With no indicators selected there is no need signal, so fall back to
    // supply gap alone rather than reporting a misleading zero.
    desert[i] = c ? w * nd + (1 - w) * sup : sup;
  }
}
function isModified() { return wNeed !== DEFAULT_W || onInd.size !== IND.length; }

// ---- helpers ---------------------------------------------------------------
const fmt = n => n === null || n === undefined || isNaN(n) ? '—' : n.toLocaleString('en-US');
const fmt1 = n => n === null || n === undefined || isNaN(n) ? '—' : n.toFixed(1);
const fmt2 = n => n === null || n === undefined || isNaN(n) ? '—' : n.toFixed(2);
const money = n => n === null || n === undefined || isNaN(n) ? '—' : '$' + Math.round(n).toLocaleString('en-US');
const compact = n => n >= 1e6 ? (n/1e6).toFixed(1)+'M' : n >= 1e3 ? (n/1e3).toFixed(0)+'k' : String(n);
const groupByKey = {};
D.groups.forEach(g => { groupByKey[g.k] = g; });

// Expand a sparse community group into a dense per-county array
const denseCache = {};
function dense(key) {
  if (denseCache[key]) return denseCache[key];
  const g = groupByKey[key], a = new Float64Array(N);
  for (let j = 0; j < g.i.length; j++) a[g.i[j]] = g.v[j];
  return denseCache[key] = a;
}
function pctOf(key) {
  const a = dense(key), out = new Float64Array(N);
  for (let i = 0; i < N; i++) out[i] = D.pop[i] ? a[i] / D.pop[i] * 100 : 0;
  return out;
}

function fillGroupSelect(sel, initial) {
  const cats = [];
  D.groups.forEach(g => { if (!cats.includes(g.c)) cats.push(g.c); });
  sel.innerHTML = cats.map(c =>
    `<optgroup label="${c}">` +
    D.groups.filter(g => g.c === c)
      .map(g => `<option value="${g.k}">${g.l} (${compact(g.total)} nationally)</option>`)
      .join('') + '</optgroup>'
  ).join('');
  sel.value = initial;
}

function kpi(n, l, hl) {
  return `<div class="kpi${hl ? ' hl' : ''}"><div class="n">${n}</div><div class="l">${l}</div></div>`;
}

// Sort indices by a value array, descending, nulls last
function orderBy(vals, idx) {
  return idx.slice().sort((a, b) => {
    const x = vals[a], y = vals[b];
    if (x === null || x === undefined || isNaN(x)) return 1;
    if (y === null || y === undefined || isNaN(y)) return -1;
    return y - x;
  });
}

// ---- map -------------------------------------------------------------------
const COLOR_DEFS = {
  desert: {t:'Desert score',    get:()=>desert,           rng:[0,100], rev:false,
           note:'Combined score: socioeconomic need weighted against how few legal-services offices exist per resident.'},
  need:   {t:'Need index',      get:()=>need,             rng:[0,100], rev:false,
           note:'The socioeconomic half of the score only — poverty, income, unemployment, education, limited English and foreign-born share, ignoring how many lawyers are present.'},
  supply: {t:'Supply gap',      get:()=>D.prSupply.map(v=>(v??0)*100), rng:[0,100], rev:false,
           note:'The supply half only. High values mean very few legal-services offices per resident, regardless of need.'},
  pov:    {t:'Poverty %',       get:()=>D.pov,            rng:null, rev:false,
           note:'Share of the population below the federal poverty line.'},
  inc:    {t:'Median income',   get:()=>D.inc,            rng:null, rev:true,
           note:'Median household income. The scale is reversed so darker still means more disadvantage.'},
  unemp:  {t:'Unemployment %',  get:()=>D.unemp,          rng:null, rev:false,
           note:'Share of the civilian labour force that is unemployed.'},
  lep:    {t:'Limited-Eng. %',  get:()=>D.lep,            rng:null, rev:false,
           note:'Share of households where no adult speaks English "very well" — a direct barrier to navigating legal processes.'},
  fb:     {t:'Foreign-born %',  get:()=>D.fb,             rng:null, rev:false,
           note:'Share of residents born outside the United States.'},
  p10k:   {t:'Offices / 10k',   get:()=>D.p10k,           rng:[0,12], rev:true,
           note:'Legal-services offices per 10,000 residents. The scale is reversed so darker means fewer lawyers.'},
  group:  {t:'% of county',     get:()=>null,             rng:null, rev:false, note:''}
};

function hoverText(i, extraLabel, extraVal) {
  let s = `<b>${D.name[i]}, ${D.state[i]}</b><br>`
    + `Desert score: <b>${fmt1(desert[i])}</b> / 100<br>`
    + `&nbsp;&nbsp;need ${fmt1(need[i])} &middot; supply gap ${fmt1((D.prSupply[i]??0)*100)}<br><br>`
    + `Population: ${fmt(D.pop[i])}<br>`
    + `Poverty: ${fmt1(D.pov[i])}%<br>`
    + `Median income: ${money(D.inc[i])}<br>`
    + `Unemployment: ${fmt1(D.unemp[i])}%<br>`
    + `Limited-English households: ${fmt1(D.lep[i])}%<br>`
    + `Legal services offices: ${D.est[i] === 0 ? '<b>none</b>' : fmt(D.est[i])}`;
  if (extraLabel) s += `<br>${extraLabel}: ${extraVal}`;
  return s;
}

function drawMap(divId, colorKey, stateFilter, groupKey) {
  const def = COLOR_DEFS[colorKey];
  let vals, title = def.t, gpct = null;
  if (colorKey === 'group') {
    gpct = pctOf(groupKey);
    vals = Array.from(gpct);
    title = groupByKey[groupKey].l + '<br>% of county';
  } else {
    vals = Array.from(def.get());
  }

  let idx = Array.from({length:N}, (_, i) => i);
  if (stateFilter) idx = idx.filter(i => D.state[i] === stateFilter);

  const gk = groupKey && colorKey !== 'group' ? dense(groupKey) : null;
  const text = idx.map(i => hoverText(i,
    gk ? groupByKey[groupKey].l : (gpct ? groupByKey[groupKey].l : null),
    gk ? fmt(gk[i]) : (gpct ? fmt2(gpct[i]) + '% of county' : null)));

  // A reversed scale keeps "darker = worse" true for indicators where a low raw
  // value is the bad outcome (income, offices per capita).
  const scale = def.rev ? SCALE.map(([p, c], k, arr) =>
    [p, arr[arr.length - 1 - k][1]]) : SCALE;

  const trace = {
    type:'choropleth', geojson:GEO, featureidkey:'id',
    locations: idx.map(i => D.fips[i]),
    z: idx.map(i => vals[i]),
    text, hoverinfo:'text',
    colorscale: scale,
    zmin: def.rng ? def.rng[0] : undefined,
    zmax: def.rng ? def.rng[1] : undefined,
    marker:{line:{width:0.15, color:'rgba(255,255,255,0.5)'}},
    colorbar:{title:{text:title, font:{size:11}}, thickness:13, len:0.72, x:0.99,
              tickfont:{size:10}},
  };
  // Plotly's fitbounds does not take effect for geojson choropleths, so a
  // zoomed view sets an explicit lon/lat window from the state's bounding box
  // (precomputed in Python) instead of relying on it.
  const geoBase = {visible:false, showsubunits:true,
                   subunitcolor:'rgba(45,45,45,0.75)', subunitwidth:1};
  const b = stateFilter ? D.stateBounds[stateFilter] : null;
  const layout = {
    geo: b
      ? Object.assign({
          projection:{type:'mercator'},
          lonaxis:{range:[b[0], b[2]]}, lataxis:{range:[b[1], b[3]]},
        }, geoBase)
      : Object.assign({scope:'usa'}, geoBase),
    margin:{l:0,r:0,t:0,b:0}, font:PLOT_FONT, dragmode:'pan',
    paper_bgcolor:'rgba(0,0,0,0)',
  };
  Plotly.react(divId, [trace], layout, CFG);
}

// ---- overview --------------------------------------------------------------
function renderOverview() {
  const severePop = D.pop.reduce((a, p, i) => a + (desert[i] >= 75 ? p : 0), 0);
  const nSevere = desert.filter(d => d >= 75).length;
  document.getElementById('kpis-overview').innerHTML =
      kpi(fmt(D.meta.nCounties), `Counties scored across ${D.meta.nStates} states &amp; DC`)
    + kpi(fmt(D.meta.zeroProvider), 'Counties with <em>zero</em> legal services offices', true)
    + kpi(compact(D.meta.popNoProvider), 'People in a county with no legal services office', true)
    + kpi(fmt(nSevere), 'Severe deserts (score 75+)')
    + kpi(compact(severePop), 'People living in a severe desert')
    + kpi(String(D.meta.nGroups), 'Communities you can filter by');

  drawMap('map-overview', 'desert', '', null);

  // need vs supply scatter
  const sizeRef = 2 * Math.max(...D.pop) / (38 * 38);
  Plotly.react('scatter', [{
    type:'scattergl', mode:'markers',
    x: D.p10k.map(v => Math.min(v ?? 0, 12)),
    y: need,
    text: Array.from({length:N}, (_, i) => hoverText(i)),
    hoverinfo:'text',
    marker:{
      size: D.pop, sizemode:'area', sizeref:sizeRef, sizemin:2.5,
      color: desert, colorscale:SCALE, cmin:0, cmax:100, opacity:0.62,
      line:{width:0.4, color:'rgba(0,0,0,0.35)'},
      colorbar:{title:{text:'Desert<br>score', font:{size:11}}, thickness:13, len:0.8, tickfont:{size:10}},
    },
  }], {
    margin:{l:56,r:10,t:26,b:52}, font:PLOT_FONT,
    xaxis:{title:{text:'Legal services offices per 10,000 residents (capped at 12)'},
           gridcolor:'#eef1f4', zeroline:false},
    yaxis:{title:{text:'Socioeconomic need index (0–100)'}, gridcolor:'#eef1f4', zeroline:false},
    annotations:[{x:0.4, y:98, text:'highest need, fewest lawyers', showarrow:false,
                  font:{size:11, color:'#a3231b'}, xanchor:'left'}],
    paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
  }, CFG);

  const top = orderBy(desert, Array.from({length:N}, (_, i) => i)).slice(0, 20);
  document.getElementById('tbl-worst').innerHTML =
    `<thead><tr><th class="l">County</th><th class="l">State</th><th>Population</th>
     <th>Poverty</th><th>Median income</th><th>Legal offices</th><th>Desert score</th></tr></thead><tbody>`
    + top.map(i => `<tr><td class="l">${D.name[i]}</td><td class="l">${D.state[i]}</td>
      <td class="num">${fmt(D.pop[i])}</td><td class="num">${fmt1(D.pov[i])}%</td>
      <td class="num">${money(D.inc[i])}</td>
      <td class="num ${D.est[i] === 0 ? 'zero' : ''}">${D.est[i]}</td>
      <td class="score">${fmt1(desert[i])}</td></tr>`).join('')
    + '</tbody>';
}

// ---- map tab ---------------------------------------------------------------
function renderMapTab() {
  const ck = document.getElementById('m-color').value;
  const st = document.getElementById('m-state').value;
  const gk = document.getElementById('m-group').value;
  document.getElementById('m-group').disabled = false;
  document.getElementById('map-note').innerHTML =
    ck === 'group'
      ? `Share of each county that is ${groupByKey[gk].l} &mdash; ${fmt(groupByKey[gk].total)}
         people nationally. Compare this against the desert score view to find
         the overlap. Community population also appears in every hover tooltip.`
      : COLOR_DEFS[ck].note;
  drawMap('map-main', ck, st, gk);
}

// ---- communities -----------------------------------------------------------
function communityRows(gk, minPop) {
  const a = dense(gk);
  const rows = [];
  for (let i = 0; i < N; i++) if (a[i] >= minPop && a[i] > 0) {
    rows.push({i, cpop:a[i], cpct: D.pop[i] ? a[i] / D.pop[i] * 100 : 0});
  }
  return rows;
}

function renderCommunities() {
  const gk = document.getElementById('c-group').value;
  const rank = document.getElementById('c-rank').value;
  const minPop = +document.getElementById('c-min').value;
  const g = groupByKey[gk];
  const rows = communityRows(gk, minPop);

  // National exposure stats for this community (all counties, unfiltered)
  const a = dense(gk);
  let tot = 0, wsum = 0, sev = 0, noOffice = 0;
  for (let i = 0; i < N; i++) {
    if (!a[i]) continue;
    tot += a[i];
    wsum += a[i] * desert[i];
    if (desert[i] >= 75) sev += a[i];
    if (D.est[i] === 0) noOffice += a[i];
  }
  document.getElementById('kpis-community').innerHTML =
      kpi(fmt(g.total), `${g.l} residents nationally`)
    + kpi(fmt1(tot ? wsum / tot : 0), 'Population-weighted desert score', tot && wsum / tot >= 50)
    + kpi(fmt1(tot ? sev / tot * 100 : 0) + '%', 'Live in a severe desert (75+)')
    + kpi(fmt(Math.round(noOffice)), 'Live in a county with no legal office', noOffice > 0)
    + kpi(fmt(rows.length), `Counties with ${fmt(minPop)}+ ${g.l} residents`);

  // Bar chart of top counties
  const keyFor = {desert:r => desert[r.i], pop:r => r.cpop, pct:r => r.cpct};
  const label = {desert:'desert score', pop:'community population', pct:'community share of county'}[rank];
  const sorted = rows.slice().sort((x, y) => keyFor[rank](y) - keyFor[rank](x)).slice(0, 22).reverse();
  document.getElementById('c-title-1').textContent =
    `Top ${g.l} counties by ${label}`;
  document.getElementById('c-note-1').innerHTML = rank === 'desert'
    ? `Counties with at least ${fmt(minPop)} ${g.l} residents, ranked by desert
       severity &mdash; the shortlist of where this community faces the widest
       legal-aid gap. Bars are coloured by desert score.`
    : `Counties with at least ${fmt(minPop)} ${g.l} residents, ranked by
       ${label}. Bars are coloured by desert score, so a dark bar means a large
       community that is <em>also</em> underserved.`;

  if (!sorted.length) {
    Plotly.purge('c-bars');
    document.getElementById('c-bars').innerHTML =
      `<div class="loading">No county has ${fmt(minPop)}+ ${g.l} residents. Try a lower threshold.</div>`;
  } else {
    Plotly.react('c-bars', [{
      type:'bar', orientation:'h',
      y: sorted.map(r => `${D.name[r.i]}, ${D.state[r.i]}`),
      x: sorted.map(r => rank === 'desert' ? desert[r.i] : rank === 'pop' ? r.cpop : r.cpct),
      marker:{color: sorted.map(r => desert[r.i]), colorscale:SCALE, cmin:0, cmax:100,
              line:{width:0.5, color:'rgba(0,0,0,0.15)'}},
      text: sorted.map(r => `${D.name[r.i]}, ${D.state[r.i]}<br>`
        + `Desert score: <b>${fmt1(desert[r.i])}</b><br>`
        + `${g.l}: ${fmt(r.cpop)} (${fmt2(r.cpct)}% of county)<br>`
        + `County population: ${fmt(D.pop[r.i])}<br>`
        + `Legal offices: ${D.est[r.i] === 0 ? '<b>none</b>' : D.est[r.i]}`),
      hoverinfo:'text',
    }], {
      margin:{l:190,r:24,t:8,b:46}, font:PLOT_FONT, bargap:0.26,
      xaxis:{title:{text: rank === 'desert' ? 'Desert score'
              : rank === 'pop' ? `${g.l} population` : `${g.l} share of county (%)`},
             gridcolor:'#eef1f4', zeroline:false},
      yaxis:{automargin:true},
      paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
    }, CFG);
  }

  // Exposure across all communities
  const exp = D.groups.filter(x => x.total >= 50000).map(x => {
    const arr = dense(x.k);
    let t = 0, w = 0;
    for (let i = 0; i < N; i++) if (arr[i]) { t += arr[i]; w += arr[i] * desert[i]; }
    return {k:x.k, l:x.l, c:x.c, total:x.total, score: t ? w / t : 0};
  }).sort((p, q) => p.score - q.score);
  Plotly.react('c-exposure', [{
    type:'bar', orientation:'h',
    y: exp.map(e => e.l), x: exp.map(e => e.score),
    marker:{
      color: exp.map(e => e.k === gk ? '#0b6b7a' : e.score),
      colorscale:SCALE, cmin:0, cmax:100,
      line:{width: 0.5, color:'rgba(0,0,0,0.15)'},
    },
    text: exp.map(e => `<b>${e.l}</b> <i>(${e.c})</i><br>`
      + `Population-weighted desert score: <b>${fmt1(e.score)}</b><br>`
      + `National population: ${fmt(e.total)}`
      + (e.k === gk ? '<br><i>— your current selection</i>' : '')),
    hoverinfo:'text',
  }], {
    margin:{l:210,r:24,t:8,b:46}, font:PLOT_FONT, bargap:0.24,
    xaxis:{title:{text:'Population-weighted average desert score'}, gridcolor:'#eef1f4', zeroline:false},
    yaxis:{automargin:true, tickfont:{size:11}},
    paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
  }, CFG);

  // Full table
  document.getElementById('c-title-3').textContent = `All ${g.l} counties`;
  const cols = [
    {h:'County', cls:'l', v:r => D.name[r.i], s:r => D.name[r.i]},
    {h:'State', cls:'l', v:r => D.state[r.i], s:r => D.state[r.i]},
    {h:g.l, v:r => fmt(r.cpop), s:r => r.cpop},
    {h:'% of county', v:r => fmt2(r.cpct) + '%', s:r => r.cpct},
    {h:'County pop.', v:r => fmt(D.pop[r.i]), s:r => D.pop[r.i]},
    {h:'Poverty', v:r => fmt1(D.pov[r.i]) + '%', s:r => D.pov[r.i] ?? -1},
    {h:'Legal offices', v:r => `<span class="${D.est[r.i]===0?'zero':''}">${D.est[r.i]}</span>`, s:r => D.est[r.i]},
    {h:'Desert score', v:r => `<span class="score">${fmt1(desert[r.i])}</span>`, s:r => desert[r.i]},
  ];
  sortableTable('tbl-community', cols, rows, 7, 'community');
  document.getElementById('c-count').textContent =
    `${rows.length.toLocaleString()} counties with ${minPop.toLocaleString()}+ ${g.l} residents.`;
}

// ---- generic sortable table ------------------------------------------------
const sortState = {};
function sortableTable(tblId, cols, rows, defaultCol, stateKey) {
  if (!sortState[stateKey]) sortState[stateKey] = {col:defaultCol, asc:false};
  const st = sortState[stateKey];
  const sorted = rows.slice().sort((a, b) => {
    let x = cols[st.col].s(a), y = cols[st.col].s(b);
    if (typeof x === 'string') return st.asc ? x.localeCompare(y) : y.localeCompare(x);
    if (x === null || isNaN(x)) x = -Infinity;
    if (y === null || isNaN(y)) y = -Infinity;
    return st.asc ? x - y : y - x;
  });
  const shown = sorted.slice(0, 400);
  const tbl = document.getElementById(tblId);
  tbl.innerHTML =
    '<thead><tr>' + cols.map((c, k) =>
      `<th class="${c.cls || ''}${k === st.col ? ' sorted' + (st.asc ? ' asc' : '') : ''}" data-k="${k}">${c.h}</th>`
    ).join('') + '</tr></thead><tbody>'
    + shown.map(r => '<tr>' + cols.map(c =>
        `<td class="${c.cls || 'num'}">${c.v(r)}</td>`).join('') + '</tr>').join('')
    + '</tbody>';
  tbl.querySelectorAll('th').forEach(th => th.onclick = () => {
    const k = +th.dataset.k;
    if (st.col === k) st.asc = !st.asc; else { st.col = k; st.asc = false; }
    sortableTable(tblId, cols, rows, defaultCol, stateKey);
  });
  return sorted.length;
}

// ---- county lookup ---------------------------------------------------------
function renderCounties() {
  const q = document.getElementById('t-search').value.trim().toLowerCase();
  const st = document.getElementById('t-state').value;
  const gk = document.getElementById('t-group').value;
  const zeroOnly = document.getElementById('t-zero').classList.contains('on');
  const sevOnly = document.getElementById('t-severe').classList.contains('on');
  const a = dense(gk), g = groupByKey[gk];

  const rows = [];
  for (let i = 0; i < N; i++) {
    if (st && D.state[i] !== st) continue;
    if (zeroOnly && D.est[i] !== 0) continue;
    if (sevOnly && desert[i] < 75) continue;
    if (q && !(D.name[i].toLowerCase().includes(q) || D.state[i].toLowerCase().includes(q))) continue;
    rows.push({i, cpop:a[i], cpct: D.pop[i] ? a[i] / D.pop[i] * 100 : 0});
  }
  const cols = [
    {h:'County', cls:'l', v:r => D.name[r.i], s:r => D.name[r.i]},
    {h:'State', cls:'l', v:r => D.state[r.i], s:r => D.state[r.i]},
    {h:'Population', v:r => fmt(D.pop[r.i]), s:r => D.pop[r.i]},
    {h:'Poverty', v:r => fmt1(D.pov[r.i]) + '%', s:r => D.pov[r.i] ?? -1},
    {h:'Median income', v:r => money(D.inc[r.i]), s:r => D.inc[r.i] ?? -1},
    {h:'Unemp.', v:r => fmt1(D.unemp[r.i]) + '%', s:r => D.unemp[r.i] ?? -1},
    {h:'Ltd. English', v:r => fmt1(D.lep[r.i]) + '%', s:r => D.lep[r.i] ?? -1},
    {h:'Legal offices', v:r => `<span class="${D.est[r.i]===0?'zero':''}">${D.est[r.i]}</span>`, s:r => D.est[r.i]},
    {h:g.l, v:r => fmt(r.cpop), s:r => r.cpop},
    {h:'Need', v:r => fmt1(need[r.i]), s:r => need[r.i]},
    {h:'Desert score', v:r => `<span class="score">${fmt1(desert[r.i])}</span>`, s:r => desert[r.i]},
  ];
  const total = sortableTable('tbl-counties', cols, rows, 10, 'counties');
  document.getElementById('t-count').textContent =
    total > 400
      ? `Showing the top 400 of ${total.toLocaleString()} matching counties — narrow the search or sort to see others.`
      : `${total.toLocaleString()} matching ${total === 1 ? 'county' : 'counties'}.`;
}

// ---- method tab ------------------------------------------------------------
function renderMethod() {
  document.getElementById('w-need-val').textContent =
    `${wNeed}% need / ${100 - wNeed}% supply gap`;

  Plotly.react('m-hist', [{
    type:'histogram', x:desert, nbinsx:40,
    marker:{color:'#e05c2b', line:{width:0.5, color:'#fff'}},
    hovertemplate:'Score %{x}<br>%{y} counties<extra></extra>',
  }], {
    margin:{l:52,r:14,t:10,b:44}, font:PLOT_FONT,
    xaxis:{title:{text:'Desert score'}, gridcolor:'#eef1f4'},
    yaxis:{title:{text:'Counties'}, gridcolor:'#eef1f4'},
    bargap:0.03, paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
  }, CFG);

  const rows = Array.from({length:N}, (_, i) => i)
    .map(i => ({i, d: desert[i] - baseDesert[i]}))
    .sort((a, b) => Math.abs(b.d) - Math.abs(a.d)).slice(0, 60);
  const tbl = document.getElementById('tbl-shift');
  if (!isModified()) {
    tbl.innerHTML = '<tbody><tr><td class="l" style="color:#8b929c;padding:22px 11px">'
      + 'Using the default weighting — adjust the slider or turn an indicator off to see which counties move.'
      + '</td></tr></tbody>';
    return;
  }
  tbl.innerHTML = '<thead><tr><th class="l">County</th><th class="l">State</th>'
    + '<th>Default</th><th>Now</th><th>Change</th></tr></thead><tbody>'
    + rows.map(r => `<tr><td class="l">${D.name[r.i]}</td><td class="l">${D.state[r.i]}</td>
      <td class="num">${fmt1(baseDesert[r.i])}</td><td class="num">${fmt1(desert[r.i])}</td>
      <td class="num" style="color:${r.d > 0 ? '#a3231b' : '#0b6b7a'};font-weight:600">
      ${r.d > 0 ? '+' : ''}${fmt1(r.d)}</td></tr>`).join('') + '</tbody>';
}

// ---- orchestration ---------------------------------------------------------
let currentTab = 'overview';
const rendered = new Set();

function updateFlags() {
  const on = isModified();
  const msg = `Custom weighting active: <strong>${wNeed}% need / ${100 - wNeed}% supply gap</strong>`
    + (onInd.size !== IND.length
        ? `, using ${onInd.size} of ${IND.length} need indicators`
        : '')
    + '. All scores on this page reflect your changes, not the published 60/40 default.';
  ['overview','map','communities','counties','method'].forEach(t => {
    const el = document.getElementById('flag-' + t);
    el.classList.toggle('on', on);
    el.innerHTML = msg;
  });
}

const RENDER = {
  overview: renderOverview, map: renderMapTab, communities: renderCommunities,
  counties: renderCounties, method: renderMethod,
};

function render(tab) {
  RENDER[tab]();
  rendered.add(tab);
}

// Recompute invalidates every tab, so re-render the visible one now and mark
// the rest stale — they redraw when the user switches to them.
function modelChanged() {
  recompute();
  rendered.clear();
  updateFlags();
  render(currentTab);
}

document.getElementById('tabbar').onclick = e => {
  const btn = e.target.closest('.tab');
  if (!btn) return;
  currentTab = btn.dataset.t;
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('on', t === btn));
  document.querySelectorAll('.panel').forEach(p =>
    p.classList.toggle('on', p.id === 'p-' + currentTab));
  if (!rendered.has(currentTab)) render(currentTab);
  else window.dispatchEvent(new Event('resize'));   // Plotly needs a nudge when un-hidden
};

// init
recompute();
baseDesert = desert.slice();

const stateOpts = D.states.map(s => `<option value="${s}">${s}</option>`).join('');
document.getElementById('m-state').innerHTML = '<option value="">Whole country</option>' + stateOpts;
document.getElementById('t-state').innerHTML = '<option value="">All states</option>' + stateOpts;
['m-group','c-group','t-group'].forEach(id =>
  fillGroupSelect(document.getElementById(id), D.defaultGroup));

document.getElementById('ind-chips').innerHTML = D.indicators
  .map(o => `<button class="chip on" data-i="${o.k}">${o.label}</button>`).join('');

['m-color','m-state','m-group'].forEach(id =>
  document.getElementById(id).onchange = renderMapTab);
['c-group','c-rank','c-min'].forEach(id =>
  document.getElementById(id).onchange = renderCommunities);
['t-state','t-group'].forEach(id =>
  document.getElementById(id).onchange = renderCounties);
document.getElementById('t-search').oninput = renderCounties;
['t-zero','t-severe'].forEach(id => document.getElementById(id).onclick = e => {
  e.currentTarget.classList.toggle('on');
  renderCounties();
});

const slider = document.getElementById('w-need');
slider.oninput = () => {
  wNeed = +slider.value;
  document.getElementById('w-need-val').textContent =
    `${wNeed}% need / ${100 - wNeed}% supply gap`;
  modelChanged();
};
document.getElementById('ind-chips').onclick = e => {
  const btn = e.target.closest('.chip');
  if (!btn) return;
  const k = btn.dataset.i;
  // Keep at least one indicator on, otherwise "need" has no meaning at all.
  if (onInd.has(k) && onInd.size === 1) return;
  onInd.has(k) ? onInd.delete(k) : onInd.add(k);
  btn.classList.toggle('on', onInd.has(k));
  modelChanged();
};
document.getElementById('w-reset').onclick = () => {
  wNeed = DEFAULT_W; slider.value = DEFAULT_W;
  onInd = new Set(IND);
  document.querySelectorAll('#ind-chips .chip').forEach(c => c.classList.add('on'));
  modelChanged();
};

updateFlags();
render('overview');
"""

# ---------------------------------------------------------------------------
# Assemble
# ---------------------------------------------------------------------------

if requested == "most_exposed":
    default_note = (
        f"All {len(group_payload)} communities are treated identically: the "
        f"dropdown is ordered alphabetically, and none is weighted differently in "
        f"the score, which measures county-level need and legal-services supply "
        f"and so does not vary by population. It opens on "
        f"<strong>{default_label}</strong> only because that community currently "
        f"has the highest population-weighted desert score of any community of "
        f"250,000+ people — computed from the data at build time rather than "
        f"assumed. Switch freely.")
else:
    default_note = (
        f"Pinned to open on <strong>{default_label}</strong> via "
        f"<code>config.json</code>. The desert score itself is identical for "
        f"every community — it measures county-level need and legal-services "
        f"supply, which do not vary by population — and all "
        f"{len(group_payload)} communities remain available in the dropdown.")

body = (BODY
        .replace("__DEFAULTNOTE__", default_note)
        .replace("__NGROUPS__", str(len(group_payload)))
        .replace("__ACS__", str(CONFIG["acs_year"]))
        .replace("__CBP__", str(CONFIG["cbp_year"]))
        .replace("__NCOUNTIES__", f"{len(counties):,}")
        .replace("__ZEROPROV__", f"{payload['meta']['zeroProvider']:,}"))

html = (
    "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
    "<meta charset=\"utf-8\">\n"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
    "<title>Access to Justice Analytics — US Legal Aid Deserts</title>\n"
    "<meta name=\"description\" content=\"Interactive county-level map of legal aid "
    "deserts across all 50 US states, filterable by 48 communities.\">\n"
    f"<style>{CSS}</style>\n"
    "<script src=\"https://cdn.plot.ly/plotly-2.35.2.min.js\" charset=\"utf-8\"></script>\n"
    "</head>\n<body>\n"
    + body
    + f"\n<script id=\"payload\" type=\"application/json\">{payload_json}</script>\n"
    + f"<script id=\"geojson\" type=\"application/json\">{geo_json}</script>\n"
    + f"<script>\n{JS}\n</script>\n</body>\n</html>\n"
)

os.makedirs(os.path.join(HERE, "docs"), exist_ok=True)
out_path = os.path.join(HERE, "docs", "index.html")
with open(out_path, "w") as f:
    f.write(html)

print(f"  dashboard -> {out_path}  ({os.path.getsize(out_path) / 1e6:.1f} MB)")

# ---------------------------------------------------------------------------
# Static map for the README (GitHub cannot render the interactive page)
# ---------------------------------------------------------------------------

png_path = os.path.join(HERE, "docs", "preview.png")
try:
    import plotly.graph_objects as go

    DESERT_SCALE = [[0.00, "#f7fafb"], [0.25, "#e4ecf2"], [0.45, "#ffe9c4"],
                    [0.60, "#fcc276"], [0.72, "#f79245"], [0.83, "#e85a25"],
                    [0.92, "#c02418"], [1.00, "#6b0a10"]]
    still = go.Figure(go.Choropleth(
        geojson=geo, featureidkey="id",
        locations=counties["county_fips"], z=counties["desert_score"],
        colorscale=DESERT_SCALE, zmin=0, zmax=100,
        marker_line_width=0.15, marker_line_color="rgba(255,255,255,0.5)",
        colorbar=dict(title="Desert<br>score", thickness=14, len=0.75, x=0.99),
    ))
    still.update_geos(visible=False, scope="usa", showsubunits=True,
                      subunitcolor="rgba(45,45,45,0.75)", subunitwidth=1.0)
    still.update_layout(
        title=dict(text="Legal Aid Deserts by County — US Census ACS "
                        f"{CONFIG['acs_year']} + CBP {CONFIG['cbp_year']}",
                   x=0.5, xanchor="center", y=0.97, font=dict(size=17)),
        margin=dict(l=0, r=0, t=46, b=0), width=1200, height=700,
        paper_bgcolor="white",
    )
    still.write_image(png_path, scale=2)
    print(f"  preview   -> {png_path}")
except Exception as exc:
    print(f"  (skipped preview.png: {type(exc).__name__}: {exc})")

print("\nOpen docs/index.html in a browser, or publish with GitHub Pages")
print("(Settings -> Pages -> Source: main branch, /docs folder).")
