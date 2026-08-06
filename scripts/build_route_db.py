#!/usr/bin/env python3
"""
EDDM Route Database Builder
============================
Builds the pre-computed route database by merging:
  1. USPS GIS carrier route data (demographics, delivery counts, geometry)
  2. Census ACS block-group data (home values, ownership, value distributions)
  3. Census Geocoder (maps route centroids → Census tracts)

Run quarterly to refresh when USPS updates routes.

Usage:
    python build_route_db.py
    python build_route_db.py --output /path/to/route_db.json
    python build_route_db.py --zips 33701,33702,33703
    python build_route_db.py --skip-geocode  # Use cached tract mappings
"""

import json
import time
import math
import os
import sys
import argparse
import urllib.request
import urllib.error
from collections import defaultdict
from pathlib import Path

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

DEFAULT_ZIPS = [
    "33701", "33702", "33703", "33704", "33705", "33706", "33707", "33708",
    "33709", "33710", "33711", "33712", "33713", "33714", "33715", "33716",
]

# Pinellas County FIPS
STATE_FIPS = "12"
COUNTY_FIPS = "103"

USPS_GIS_URL = "https://gis.usps.com/arcgis/rest/services/EDDM/selectZIP/GPServer/routes/execute"
CENSUS_ACS_URL = "https://api.census.gov/data/2022/acs/acs5"
CENSUS_GEO_URL = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"

# Neighborhood knowledge — your local expertise
NEIGHBORHOOD_KNOWLEDGE = {
    "33701": {
        "name": "Downtown / Old Southeast",
        "character": "Urban core, walkable, young professionals and retirees in condos, historic homes in Old SE. Arts, dining, and nightlife district. Waterfront parks.",
        "best_for": ["restaurants", "boutiques", "professional services", "nightlife", "arts", "fitness"],
        "notes": "Heavy condo presence skews household size down. Mix of very high income (waterfront) and moderate (inland condos). Strong foot traffic.",
    },
    "33702": {
        "name": "Shore Acres / Northeast",
        "character": "Established suburban, waterfront properties along Tampa Bay. Family-oriented with mid-century and newer homes. Quiet residential streets.",
        "best_for": ["home services", "marine services", "family dining", "healthcare", "lawn care", "pest control"],
        "notes": "Solid middle-to-upper-middle class. Good family density. Some flood zone areas near water.",
    },
    "33703": {
        "name": "Snell Isle / Venetian Isles / Riviera Bay",
        "character": "Premier affluent waterfront neighborhood. Historic estates on Snell Isle, upscale homes throughout. Older demographic skewing wealthy. Country club area.",
        "best_for": ["luxury services", "financial advisors", "high-end home improvement", "fine dining", "luxury auto", "wealth management"],
        "notes": "THE money ZIP in St Pete. Snell Isle routes have the highest home values in the city. Venetian Isles also strong. Some routes near Gandy are more moderate.",
    },
    "33704": {
        "name": "Old Northeast / Crescent Lake / Euclid-St Paul's",
        "character": "Historic bungalows, walkable to downtown, artsy and progressive. Young families and professionals. Crescent Lake is a gem. Active neighborhood association.",
        "best_for": ["boutiques", "yoga/wellness", "craft food/drink", "home renovation", "pet services", "landscaping"],
        "notes": "Gentrified significantly. High home values for the home sizes. Very engaged community — direct mail works well here.",
    },
    "33705": {
        "name": "Central St Pete / Greater Woodlawn Park",
        "character": "Diverse, rapidly gentrifying, mix of income levels. Emerging arts and food corridor. Historic Black neighborhoods with strong community identity.",
        "best_for": ["affordable services", "community events", "auto repair", "restaurants", "barbers/salons", "churches"],
        "notes": "Wide income range within this ZIP. Some routes near the water are surprisingly affluent. Inland routes more moderate.",
    },
    "33706": {
        "name": "St Pete Beach / Pass-a-Grille / Belle Vista",
        "character": "Beach community with tourist and permanent residents. Condos and vacation rentals. Seasonal population swings. Pass-a-Grille is upscale.",
        "best_for": ["tourism", "restaurants", "beach services", "real estate", "vacation rentals", "marine"],
        "notes": "Seasonal — snowbird population inflates winter counts. Some routes are almost all condos. Pass-a-Grille routes are high value.",
    },
    "33707": {
        "name": "Gulfport / Pasadena / Bear Creek",
        "character": "Eclectic, artsy-bohemian Gulfport core. Pasadena is more traditional suburban. Growing young family presence. Waterfront areas are premium.",
        "best_for": ["arts/culture", "wellness", "pet services", "casual dining", "home services", "yoga"],
        "notes": "Gulfport has a unique vibe — community-oriented, progressive. Pasadena waterfront routes are higher income. Bear Creek is more moderate.",
    },
    "33708": {
        "name": "Treasure Island / Madeira Beach",
        "character": "Beach barrier island communities. Mix of retirees and vacationers. Waterfront condos and older single-family. Laid-back beach town feel.",
        "best_for": ["tourism", "restaurants", "marine services", "healthcare", "home services", "real estate"],
        "notes": "Similar seasonal dynamics to 33706. Older demographic. Some routes are mostly condos/apartments.",
    },
    "33709": {
        "name": "Tyrone / Crossroads",
        "character": "Commercial corridor along Tyrone Blvd. Diverse demographics, high apartment density. Shopping centers and strip malls. Affordable area.",
        "best_for": ["fast food", "discount retail", "auto services", "apartments", "budget services", "cell phone stores"],
        "notes": "Highest apartment density in St Pete. Lower incomes but high population density = lots of doors. Good for mass-market offers.",
    },
    "33710": {
        "name": "Jungle Terrace / Seminole Park / Disston Ridge",
        "character": "Suburban residential heartland. Families, good schools nearby, mid-range incomes. Established neighborhoods with pride of ownership. Your home base.",
        "best_for": ["home services", "family dining", "dental/medical", "lawn care", "pest control", "HVAC", "roofing"],
        "notes": "Bread and butter for home services EDDM. Strong family density, high homeownership, engaged community. Jungle Terrace Community Card territory.",
    },
    "33711": {
        "name": "Coquina Key / Pinellas Point / Maximo",
        "character": "Waterfront residential peninsula. Mix of condos and single-family. Moderate incomes, older residents. Bayway Isles is upscale pocket.",
        "best_for": ["home services", "healthcare", "restaurants", "marine", "lawn care", "pest control"],
        "notes": "Bayway Isles routes are premium. Rest is solid middle class. Good for home services targeting older homeowners.",
    },
    "33712": {
        "name": "South St Pete / Midtown / Childs Park",
        "character": "Historically Black community with strong cultural identity. Rapidly developing with new investment. Deuces corridor. Growing creative scene.",
        "best_for": ["community services", "restaurants", "barbers/salons", "churches", "local retail", "fitness"],
        "notes": "Significant development happening. Income levels are rising in some pockets. Strong community engagement — local messaging resonates.",
    },
    "33713": {
        "name": "Disston Heights / Historic Kenwood / Woodlawn",
        "character": "Mixed residential styles. Moderate incomes. Kenwood section is gentrifying with bungalow renovations. Disston Heights is traditional suburban.",
        "best_for": ["home improvement", "auto services", "family restaurants", "retail", "lawn care", "handyman"],
        "notes": "Kenwood is the hot pocket — home values climbing fast. Disston Heights is more stable/affordable. Good mix for broad-appeal services.",
    },
    "33714": {
        "name": "Lealman / Gateway (unincorporated)",
        "character": "Unincorporated Pinellas County. Lower income, very diverse, high apartment density. Undergoing slow transformation. Commercial along US-19.",
        "best_for": ["affordable services", "auto repair", "discount retail", "food delivery", "cell phones", "tax prep"],
        "notes": "Lowest incomes in the St Pete area. High renter percentage. Mass-market only — premium services won't convert here.",
    },
    "33715": {
        "name": "Tierra Verde",
        "character": "Upscale island community accessible via bridge. Waterfront living, marina, nature preserve access. High home values, exclusive feel.",
        "best_for": ["luxury home services", "marine", "financial services", "high-end dining", "landscaping", "pool service"],
        "notes": "Small but wealthy. Only 8 routes but high value per route. Older demographic. Good add-on to any luxury campaign hitting 33703.",
    },
    "33716": {
        "name": "Gateway / Carillon / Feather Sound",
        "character": "Office park corridor with newer residential construction. Mix of apartments and newer subdivisions. Corporate area with Carillon complex.",
        "best_for": ["B2B services", "lunch restaurants", "fitness", "professional services", "dry cleaning", "car wash"],
        "notes": "Daytime population is much higher than residential (office workers). Newer construction = younger families in subdivisions. Apartments are newer/nicer.",
    },
}


# ─────────────────────────────────────────────
# UTILITY FUNCTIONS
# ─────────────────────────────────────────────

def safe_int(value):
    """Safely parse Census value to int, handling special codes."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    v = str(value).strip()
    if not v or v in ("null", "None", "N/A", "(X)"):
        return None
    try:
        n = int(float(v))
        # Census uses negative codes for missing/suppressed data
        if n <= -666666666:
            return None
        return n
    except (ValueError, TypeError):
        return None


def safe_float(value):
    """Safely parse to float."""
    if value is None:
        return None
    try:
        n = float(value)
        if n <= -666666666:
            return None
        return n
    except (ValueError, TypeError):
        return None


def fetch_json(url, retries=3, delay=1.0):
    """Fetch JSON from URL with retry logic."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "EDDM-Agent/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
            else:
                raise RuntimeError(f"Failed to fetch {url}: {e}")


def haversine_miles(lat1, lon1, lat2, lon2):
    """Distance between two points in miles."""
    R = 3959
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


# ─────────────────────────────────────────────
# STEP 1: FETCH USPS CARRIER ROUTES
# ─────────────────────────────────────────────

def fetch_usps_routes(zip_codes):
    """Fetch all carrier routes from USPS GIS endpoint."""
    print(f"\n{'='*60}")
    print("STEP 1: Fetching USPS carrier routes")
    print(f"{'='*60}")

    # The API accepts comma-delimited ZIPs (tested up to 16 at once)
    zip_str = ",".join(zip_codes)
    url = f"{USPS_GIS_URL}?f=json&env:outSR=4326&ZIP={zip_str}&Rte_Box=R&UserName=EDDM"

    print(f"  Querying {len(zip_codes)} ZIP codes: {zip_str}")
    t0 = time.time()
    data = fetch_json(url)
    elapsed = time.time() - t0

    features = data.get("results", [{}])[0].get("value", {}).get("features", [])
    print(f"  Got {len(features)} carrier routes in {elapsed:.1f}s")

    routes = []
    for f in features:
        a = f["attributes"]
        geom = f.get("geometry", {})
        paths = geom.get("paths", [])

        # Compute centroid from all path points
        all_points = [pt for path in paths for pt in path]
        if all_points:
            centroid_lat = sum(p[1] for p in all_points) / len(all_points)
            centroid_lon = sum(p[0] for p in all_points) / len(all_points)
        else:
            centroid_lat, centroid_lon = None, None

        # Compute bounding box
        if all_points:
            bbox = {
                "min_lat": min(p[1] for p in all_points),
                "max_lat": max(p[1] for p in all_points),
                "min_lon": min(p[0] for p in all_points),
                "max_lon": max(p[0] for p in all_points),
            }
        else:
            bbox = None

        # Age bracket totals
        age_total = sum(a.get(k, 0) or 0 for k in [
            "AGE_LT_19", "AGE_20_24", "AGE_25_34", "AGE_35_44",
            "AGE_45_54", "AGE_55_64", "AGE_65_74", "AGE_75_84", "AGE_GT_85"
        ])

        route = {
            "route_id": a["ZIP_CRID"],
            "zip_code": a["ZIP_CODE"],
            "carrier_route": a["CRID_ID"],
            "route_type": a["TYPE"],
            "city_state": a["CITY_STATE"],
            "facility_name": a.get("FACILITY_NAME", ""),
            "drop_ship_key": a.get("DROPSHIP_KEY", ""),

            # Delivery counts
            "residential_count": a["RES_CNT"],
            "business_count": a["BUS_CNT"],
            "total_count": int(a["TOT_CNT"]),
            "lt_200": a.get("LT_200_IND", "N") == "Y",  # Under EDDM minimum

            # USPS demographics
            "usps_med_income": a["MED_INCOME"],
            "usps_med_age": a["MED_AGE"],
            "usps_avg_hh_size": a["AVG_HH_SIZ"],
            "usps_age_brackets": {
                "lt_19": a.get("AGE_LT_19", 0),
                "20_24": a.get("AGE_20_24", 0),
                "25_34": a.get("AGE_25_34", 0),
                "35_44": a.get("AGE_35_44", 0),
                "45_54": a.get("AGE_45_54", 0),
                "55_64": a.get("AGE_55_64", 0),
                "65_74": a.get("AGE_65_74", 0),
                "75_84": a.get("AGE_75_84", 0),
                "85_plus": a.get("AGE_GT_85", 0),
            },
            "usps_age_total": age_total,

            # Computed from USPS age data
            "pct_under_19": round(a.get("AGE_LT_19", 0) / age_total * 100) if age_total > 0 else 0,
            "pct_family_age": round(sum(a.get(k, 0) or 0 for k in ["AGE_25_34", "AGE_35_44", "AGE_45_54"]) / age_total * 100) if age_total > 0 else 0,
            "pct_senior": round(sum(a.get(k, 0) or 0 for k in ["AGE_55_64", "AGE_65_74", "AGE_75_84", "AGE_GT_85"]) / age_total * 100) if age_total > 0 else 0,

            # Geography
            "centroid": {"lat": round(centroid_lat, 6), "lon": round(centroid_lon, 6)} if centroid_lat else None,
            "bounding_box": bbox,

            # Geometry stored separately (for map rendering)
            "geometry_paths": paths,

            # Census enrichment (filled in Step 3)
            "census_tract": None,
            "census_data": {},

            # Neighborhood knowledge (filled in Step 4)
            "neighborhood": {},
        }
        routes.append(route)

    # Summary by ZIP
    from collections import Counter
    zip_counts = Counter(r["zip_code"] for r in routes)
    print(f"\n  Routes by ZIP:")
    for z in sorted(zip_counts):
        zip_routes = [r for r in routes if r["zip_code"] == z]
        total_res = sum(r["residential_count"] for r in zip_routes)
        avg_inc = sum(r["usps_med_income"] for r in zip_routes) // len(zip_routes)
        print(f"    {z}: {zip_counts[z]:3d} routes, {total_res:6,} residential, avg income ${avg_inc:,}")

    total_res = sum(r["residential_count"] for r in routes)
    total_bus = sum(r["business_count"] for r in routes)
    print(f"\n  TOTAL: {len(routes)} routes, {total_res:,} residential, {total_bus:,} business")

    return routes


# ─────────────────────────────────────────────
# STEP 2: FETCH CENSUS ACS DATA
# ─────────────────────────────────────────────

def fetch_census_data(state_fips, county_fips):
    """Fetch Census ACS block-group data for the county."""
    print(f"\n{'='*60}")
    print("STEP 2: Fetching Census ACS block-group data")
    print(f"{'='*60}")

    # Variables we need:
    # B25077_001E = Median home value
    # B25075_001E = Total owner-occupied units (for value distribution)
    # B25075_025E = Units valued $1M to $1.5M
    # B25075_026E = Units valued $1.5M to $2M
    # B25075_027E = Units valued $2M+
    # B25003_002E = Owner-occupied housing units
    # B25003_003E = Renter-occupied housing units
    # B25024_002E = 1-unit detached (single family)
    # B25024_003E = 1-unit attached (townhome)
    # B25024_008E = 10-19 unit buildings
    # B25024_009E = 20+ unit buildings

    variables = [
        "B25077_001E",  # Median home value
        "B25075_001E",  # Total owner-occupied (value distribution base)
        "B25075_025E",  # $1M to $1.5M
        "B25075_026E",  # $1.5M to $2M
        "B25075_027E",  # $2M+
        "B25003_002E",  # Owner-occupied
        "B25003_003E",  # Renter-occupied
        "B25024_002E",  # 1-unit detached (single family homes)
        "B25024_003E",  # 1-unit attached (townhomes)
    ]

    var_str = ",".join(variables)
    url = f"{CENSUS_ACS_URL}?get={var_str}&for=block%20group:*&in=state:{state_fips}&in=county:{county_fips}&in=tract:*"

    print(f"  Fetching {len(variables)} variables for state={state_fips}, county={county_fips}")
    t0 = time.time()
    data = fetch_json(url)
    elapsed = time.time() - t0

    header = data[0]
    rows = data[1:]
    print(f"  Got {len(rows)} block groups in {elapsed:.1f}s")

    # Find column indices for geo fields
    # The geo fields come after the variables: state, county, tract, block group
    n_vars = len(variables)
    tract_idx = n_vars + 2   # state=n, county=n+1, tract=n+2
    bg_idx = n_vars + 3

    # Build tract-level summaries
    tracts = defaultdict(lambda: {"block_groups": [], "summary": {}})

    for row in rows:
        tract = row[tract_idx]
        bg = row[bg_idx]

        bg_data = {
            "block_group": bg,
            "med_home_value": safe_int(row[0]),
            "total_owner_val_units": safe_int(row[1]),
            "units_1m_to_1_5m": safe_int(row[2]),
            "units_1_5m_to_2m": safe_int(row[3]),
            "units_2m_plus": safe_int(row[4]),
            "owner_occupied": safe_int(row[5]),
            "renter_occupied": safe_int(row[6]),
            "single_family_detached": safe_int(row[7]),
            "single_family_attached": safe_int(row[8]),
        }
        tracts[tract]["block_groups"].append(bg_data)

    # Compute tract summaries
    for tract_id, tract_info in tracts.items():
        bgs = tract_info["block_groups"]

        home_vals = [b["med_home_value"] for b in bgs if b["med_home_value"] and b["med_home_value"] > 0]
        owners = sum(b["owner_occupied"] or 0 for b in bgs)
        renters = sum(b["renter_occupied"] or 0 for b in bgs)
        total_units = owners + renters
        sfh = sum((b["single_family_detached"] or 0) + (b["single_family_attached"] or 0) for b in bgs)
        u1m = sum((b["units_1m_to_1_5m"] or 0) + (b["units_1_5m_to_2m"] or 0) + (b["units_2m_plus"] or 0) for b in bgs)
        u15m = sum((b["units_1_5m_to_2m"] or 0) + (b["units_2m_plus"] or 0) for b in bgs)
        u2m = sum(b["units_2m_plus"] or 0 for b in bgs)

        tract_info["summary"] = {
            "avg_home_value": round(sum(home_vals) / len(home_vals)) if home_vals else None,
            "max_home_value": max(home_vals) if home_vals else None,
            "min_home_value": min(home_vals) if home_vals else None,
            "owner_occupied": owners,
            "renter_occupied": renters,
            "total_units": total_units,
            "own_pct": round(owners / total_units * 100) if total_units > 0 else None,
            "single_family_units": sfh,
            "sfh_pct": round(sfh / total_units * 100) if total_units > 0 else None,
            "units_1m_plus": u1m,
            "units_1_5m_plus": u15m,
            "units_2m_plus": u2m,
            "pct_1m_plus": round(u1m / owners * 100) if owners > 0 else 0,
        }

    print(f"  Computed summaries for {len(tracts)} Census tracts")

    # Quick stats
    high_val_tracts = sum(1 for t in tracts.values() if (t["summary"].get("avg_home_value") or 0) > 500000)
    luxury_tracts = sum(1 for t in tracts.values() if t["summary"].get("units_1m_plus", 0) > 50)
    print(f"  Tracts with avg home value > $500K: {high_val_tracts}")
    print(f"  Tracts with 50+ units valued $1M+: {luxury_tracts}")

    return dict(tracts)


# ─────────────────────────────────────────────
# STEP 3: GEOCODE ROUTES → CENSUS TRACTS
# ─────────────────────────────────────────────

def geocode_route_to_tract(lat, lon):
    """Geocode a lat/lon to its Census tract via Census Geocoder."""
    url = f"{CENSUS_GEO_URL}?x={lon}&y={lat}&benchmark=Public_AR_Current&vintage=Current_Current&format=json"
    try:
        data = fetch_json(url, retries=2, delay=0.5)
        blocks = data.get("result", {}).get("geographies", {}).get("2020 Census Blocks", [])
        if blocks:
            return blocks[0].get("TRACT")
    except Exception:
        pass
    return None


def enrich_routes_with_census(routes, census_tracts, tract_cache_path=None):
    """Map each route to its Census tract and attach Census data."""
    print(f"\n{'='*60}")
    print("STEP 3: Mapping routes to Census tracts (geocoding)")
    print(f"{'='*60}")

    # Load cached tract mappings if available
    tract_cache = {}
    if tract_cache_path and os.path.exists(tract_cache_path):
        with open(tract_cache_path, "r") as f:
            tract_cache = json.load(f)
        print(f"  Loaded {len(tract_cache)} cached tract mappings")

    total = len(routes)
    geocoded = 0
    cached = 0
    failed = 0

    for i, route in enumerate(routes):
        route_id = route["route_id"]

        # Check cache first
        if route_id in tract_cache:
            tract = tract_cache[route_id]
            cached += 1
        elif route["centroid"]:
            lat = route["centroid"]["lat"]
            lon = route["centroid"]["lon"]
            tract = geocode_route_to_tract(lat, lon)
            tract_cache[route_id] = tract
            geocoded += 1
            # Rate limit: Census geocoder is free but be polite
            if geocoded % 10 == 0:
                time.sleep(0.5)
        else:
            tract = None
            failed += 1

        route["census_tract"] = tract

        # Attach Census data summary
        if tract and tract in census_tracts:
            route["census_data"] = census_tracts[tract]["summary"]
        else:
            route["census_data"] = {}

        # Progress
        if (i + 1) % 25 == 0 or i == total - 1:
            print(f"  Progress: {i+1}/{total} ({cached} cached, {geocoded} geocoded, {failed} failed)")

    # Save cache for next run
    if tract_cache_path:
        with open(tract_cache_path, "w") as f:
            json.dump(tract_cache, f, indent=2)
        print(f"  Saved tract cache to {tract_cache_path}")

    enriched = sum(1 for r in routes if r["census_data"])
    print(f"\n  Results: {enriched}/{total} routes enriched with Census data")
    print(f"  ({cached} from cache, {geocoded} newly geocoded, {failed} failed)")

    return routes


# ─────────────────────────────────────────────
# STEP 4: ATTACH NEIGHBORHOOD KNOWLEDGE
# ─────────────────────────────────────────────

def attach_neighborhood_knowledge(routes):
    """Attach your local neighborhood knowledge to each route."""
    print(f"\n{'='*60}")
    print("STEP 4: Attaching neighborhood knowledge")
    print(f"{'='*60}")

    for route in routes:
        z = route["zip_code"]
        if z in NEIGHBORHOOD_KNOWLEDGE:
            route["neighborhood"] = NEIGHBORHOOD_KNOWLEDGE[z]
        else:
            route["neighborhood"] = {"name": route["city_state"], "character": "", "best_for": [], "notes": ""}

    print(f"  Attached knowledge for {len(NEIGHBORHOOD_KNOWLEDGE)} neighborhoods")
    return routes


# ─────────────────────────────────────────────
# STEP 5: SAVE DATABASE
# ─────────────────────────────────────────────

def save_database(routes, output_path):
    """Save the complete route database."""
    print(f"\n{'='*60}")
    print("STEP 5: Saving route database")
    print(f"{'='*60}")

    # Create two files:
    # 1. Full database with geometry (for map rendering)
    # 2. Compact database without geometry (for agent context)

    full_path = output_path
    compact_path = output_path.replace(".json", "_compact.json")

    # Full database
    db = {
        "metadata": {
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "zip_codes": sorted(set(r["zip_code"] for r in routes)),
            "total_routes": len(routes),
            "total_residential": sum(r["residential_count"] for r in routes),
            "total_business": sum(r["business_count"] for r in routes),
            "census_vintage": "2022 ACS 5-year",
            "usps_source": "USPS GIS ArcGIS REST",
        },
        "routes": routes,
    }

    with open(full_path, "w") as f:
        json.dump(db, f, indent=2)
    full_size = os.path.getsize(full_path) / (1024 * 1024)
    print(f"  Full database: {full_path} ({full_size:.1f} MB)")

    # Compact database (no geometry — for loading into LLM context)
    compact_routes = []
    for r in routes:
        cr = {k: v for k, v in r.items() if k not in ("geometry_paths", "bounding_box")}
        compact_routes.append(cr)

    compact_db = {
        "metadata": db["metadata"],
        "routes": compact_routes,
    }

    with open(compact_path, "w") as f:
        json.dump(compact_db, f, indent=2)
    compact_size = os.path.getsize(compact_path) / (1024 * 1024)
    print(f"  Compact database: {compact_path} ({compact_size:.1f} MB)")

    # Also save just the geometry for map rendering
    geo_path = output_path.replace(".json", "_geometry.json")
    geo_data = {}
    for r in routes:
        geo_data[r["route_id"]] = {
            "paths": r["geometry_paths"],
            "centroid": r["centroid"],
        }
    with open(geo_path, "w") as f:
        json.dump(geo_data, f)
    geo_size = os.path.getsize(geo_path) / (1024 * 1024)
    print(f"  Geometry file: {geo_path} ({geo_size:.1f} MB)")

    return full_path, compact_path, geo_path


# ─────────────────────────────────────────────
# STEP 6: VALIDATION REPORT
# ─────────────────────────────────────────────

def print_validation_report(routes):
    """Print a summary report for validation."""
    print(f"\n{'='*60}")
    print("VALIDATION REPORT")
    print(f"{'='*60}")

    total = len(routes)
    has_census = sum(1 for r in routes if r["census_data"])
    has_home_val = sum(1 for r in routes if r["census_data"].get("avg_home_value"))
    has_own_pct = sum(1 for r in routes if r["census_data"].get("own_pct") is not None)
    lt_200 = sum(1 for r in routes if r["lt_200"])
    no_centroid = sum(1 for r in routes if not r["centroid"])

    print(f"\n  Data completeness:")
    print(f"    Total routes:              {total}")
    print(f"    With Census enrichment:    {has_census} ({has_census/total*100:.0f}%)")
    print(f"    With home value data:      {has_home_val} ({has_home_val/total*100:.0f}%)")
    print(f"    With ownership data:        {has_own_pct} ({has_own_pct/total*100:.0f}%)")
    print(f"    Under 200 residential:      {lt_200} (below EDDM minimum)")
    print(f"    Missing centroid:           {no_centroid}")

    # Top routes by home value
    routes_with_val = [r for r in routes if r["census_data"].get("avg_home_value")]
    routes_with_val.sort(key=lambda r: -r["census_data"]["avg_home_value"])

    print(f"\n  Top 10 routes by home value:")
    for r in routes_with_val[:10]:
        cd = r["census_data"]
        print(f"    {r['route_id']}: ${cd['avg_home_value']:,} avg | "
              f"{cd.get('units_1m_plus', 0)} units $1M+ | "
              f"{cd.get('own_pct', '?')}% owners | "
              f"{r['residential_count']} homes | "
              f"income ${r['usps_med_income']:,}")

    # Routes that would match the furniture store scenario
    print(f"\n  Furniture store scenario (home val $500K+, income $100K+, within 5mi of 27.806,-82.638):")
    matched = []
    for r in routes:
        val = r["census_data"].get("avg_home_value") or 0
        inc = r["usps_med_income"] or 0
        if val >= 500000 and inc >= 100000 and r["centroid"]:
            dist = haversine_miles(27.806, -82.638, r["centroid"]["lat"], r["centroid"]["lon"])
            if dist <= 5:
                matched.append((r, dist))
    matched.sort(key=lambda x: -x[0]["census_data"]["avg_home_value"])
    total_homes = sum(r["residential_count"] for r, _ in matched)
    print(f"    Matching routes: {len(matched)}, total homes: {total_homes:,}")
    for r, dist in matched[:8]:
        cd = r["census_data"]
        print(f"    {r['route_id']}: ${cd['avg_home_value']:,} | "
              f"{r['residential_count']} homes | {dist:.1f}mi | "
              f"{r['neighborhood'].get('name', '')}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Build EDDM Route Database")
    parser.add_argument("--output", default="route_db.json", help="Output file path")
    parser.add_argument("--zips", default=None, help="Comma-separated ZIP codes (default: all St Pete)")
    parser.add_argument("--skip-geocode", action="store_true", help="Skip geocoding, use cache only")
    parser.add_argument("--cache-dir", default=".", help="Directory for cache files")
    args = parser.parse_args()

    zip_codes = args.zips.split(",") if args.zips else DEFAULT_ZIPS
    cache_path = os.path.join(args.cache_dir, "tract_cache.json")

    print(f"\n{'#'*60}")
    print(f"# EDDM ROUTE DATABASE BUILDER")
    print(f"# Building for {len(zip_codes)} ZIP codes")
    print(f"# Output: {args.output}")
    print(f"{'#'*60}")

    t_start = time.time()

    # Step 1: USPS routes
    routes = fetch_usps_routes(zip_codes)

    # Step 2: Census data
    census_tracts = fetch_census_data(STATE_FIPS, COUNTY_FIPS)

    # Step 3: Geocode and enrich
    if args.skip_geocode:
        print("\n  Skipping geocoding (--skip-geocode), using cache only")
        if os.path.exists(cache_path):
            with open(cache_path) as f:
                tract_cache = json.load(f)
            for route in routes:
                tract = tract_cache.get(route["route_id"])
                route["census_tract"] = tract
                if tract and tract in census_tracts:
                    route["census_data"] = census_tracts[tract]["summary"]
            enriched = sum(1 for r in routes if r["census_data"])
            print(f"  Enriched {enriched}/{len(routes)} from cache")
        else:
            print("  WARNING: No cache found. Run without --skip-geocode first.")
    else:
        routes = enrich_routes_with_census(routes, census_tracts, cache_path)

    # Step 4: Neighborhood knowledge
    routes = attach_neighborhood_knowledge(routes)

    # Step 5: Save
    full_path, compact_path, geo_path = save_database(routes, args.output)

    # Step 6: Validation
    print_validation_report(routes)

    elapsed = time.time() - t_start
    print(f"\n{'#'*60}")
    print(f"# BUILD COMPLETE in {elapsed:.0f}s")
    print(f"# Database: {full_path}")
    print(f"# Compact:  {compact_path}")
    print(f"# Geometry: {geo_path}")
    print(f"{'#'*60}\n")


if __name__ == "__main__":
    main()
