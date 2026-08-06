#!/usr/bin/env python3
"""
EDDM Campaign Agent — Tool Functions
======================================
These are the functions used to build a route-selection campaign from structured criteria.
Load the route database once at startup, then call these per-request.

Usage:
    from eddm_tools import EDDMTools
    tools = EDDMTools("route_db_compact.json", "route_db_geometry.json.gz")

    routes = tools.query_routes(center_lat=27.806, center_lon=-82.638, max_radius=5, min_home_value=500000)
    scored = tools.score_routes(routes, "luxury_retail", {"center_lat": 27.806, "center_lon": -82.638})
    selection = tools.select_campaign(scored, target_homes=10000)
    map_path = tools.generate_map(selection["route_ids"], store_lat=27.806, store_lon=-82.638, radius_miles=5)
    order_sheet = tools.generate_order_sheet(selection["route_ids"])
"""

import json
import math
import os
from collections import defaultdict
from typing import Optional


# ─────────────────────────────────────────────
# BUSINESS TYPE PLAYBOOK
# ─────────────────────────────────────────────
# This is the domain knowledge that makes the agent smart.
# The LLM reads this to understand how to weight scoring for each business type.

BUSINESS_PLAYBOOK = {
    "luxury_retail": {
        "description": "High-end furniture, jewelry, designer goods, art galleries",
        "primary_filter": "home_value",
        "weights": {"home_value": 0.35, "income": 0.25, "proximity": 0.15, "ownership": 0.15, "housing_type": 0.10},
        "defaults": {"min_home_value": 500000, "min_income": 100000, "housing_type": "homes", "radius": 8},
        "recommended_card": "9x12",
        "recommended_quality": "premium",
        "typical_campaign": "8,000-15,000 homes",
        "notes": "Home value is king. Ownership rate matters — renters don't buy furniture. Always premium stock.",
    },
    "restaurant_casual": {
        "description": "Pizza, burger joints, casual dining, fast-casual, bakeries",
        "primary_filter": "proximity",
        "weights": {"proximity": 0.45, "density": 0.25, "income": 0.15, "housing_type": 0.05, "home_value": 0.10},
        "defaults": {"radius": 3, "housing_type": "any"},
        "recommended_card": "6.5x12",
        "recommended_quality": "standard",
        "typical_campaign": "5,000-10,000 homes",
        "notes": "Proximity is everything. Apartments are fine. Suggest menu-card format. Multiple smaller drops beat one big blast.",
    },
    "restaurant_upscale": {
        "description": "Fine dining, wine bars, upscale seafood, steakhouses",
        "primary_filter": "income",
        "weights": {"income": 0.30, "proximity": 0.25, "home_value": 0.25, "ownership": 0.10, "housing_type": 0.10},
        "defaults": {"min_income": 100000, "radius": 7, "housing_type": "any"},
        "recommended_card": "6x11",
        "recommended_quality": "premium",
        "typical_campaign": "8,000-12,000 homes",
        "notes": "Income matters more than home value. Condos/apartments are fine (downtown diners). Premium feel on the card.",
    },
    "dental_orthodontics": {
        "description": "Dentists, orthodontists, oral surgeons, pediatric dental",
        "primary_filter": "family_demographics",
        "weights": {"family_score": 0.30, "income": 0.25, "proximity": 0.25, "ownership": 0.10, "housing_type": 0.10},
        "defaults": {"min_income": 75000, "housing_type": "homes", "radius": 7, "age_group": "family"},
        "recommended_card": "6x11",
        "recommended_quality": "premium",
        "typical_campaign": "8,000-15,000 homes",
        "notes": "Families with kids are the target. HH size > 2.3 and under-19 percentage are key signals. Exclude tourist/retirement areas.",
    },
    "home_services": {
        "description": "HVAC, plumbing, roofing, electrical, painting, handyman, landscaping",
        "primary_filter": "homeownership",
        "weights": {"ownership": 0.25, "housing_type": 0.25, "proximity": 0.20, "income": 0.15, "home_value": 0.15},
        "defaults": {"housing_type": "homes", "radius": 10},
        "recommended_card": "6x11",
        "recommended_quality": "standard",
        "typical_campaign": "10,000-20,000 homes",
        "notes": "Homeowners only — renters call their landlord. Single-family homes are ideal. Wide radius is OK for these services.",
    },
    "lawn_pest_pool": {
        "description": "Lawn care, pest control, pool service, pressure washing",
        "primary_filter": "homeownership",
        "weights": {"ownership": 0.30, "housing_type": 0.30, "proximity": 0.20, "income": 0.10, "home_value": 0.10},
        "defaults": {"housing_type": "homes", "radius": 7},
        "recommended_card": "6x9",
        "recommended_quality": "standard",
        "typical_campaign": "8,000-15,000 homes",
        "notes": "Single-family homes with yards. Ownership is critical. These are repeat-service businesses so coverage matters.",
    },
    "real_estate": {
        "description": "Realtors, property management, mortgage lenders",
        "primary_filter": "home_value",
        "weights": {"home_value": 0.30, "ownership": 0.20, "proximity": 0.20, "income": 0.15, "housing_type": 0.15},
        "defaults": {"radius": 5},
        "recommended_card": "6x11",
        "recommended_quality": "premium",
        "typical_campaign": "5,000-10,000 homes (farming area)",
        "notes": "Usually farming a specific area. Home value range should match the agent's typical listings. Consistency matters — monthly drops.",
    },
    "medical_healthcare": {
        "description": "Doctors, urgent care, chiropractors, physical therapy, optometrists",
        "primary_filter": "proximity",
        "weights": {"proximity": 0.30, "income": 0.25, "family_score": 0.20, "ownership": 0.15, "housing_type": 0.10},
        "defaults": {"radius": 5, "housing_type": "any"},
        "recommended_card": "6x11",
        "recommended_quality": "standard",
        "typical_campaign": "10,000-20,000 homes",
        "notes": "People go to nearby doctors. Income filter depends on insurance acceptance. Family practices weight family demographics.",
    },
    "fitness_wellness": {
        "description": "Gyms, yoga studios, pilates, CrossFit, personal training, spas",
        "primary_filter": "proximity",
        "weights": {"proximity": 0.35, "income": 0.25, "age_match": 0.20, "housing_type": 0.10, "home_value": 0.10},
        "defaults": {"radius": 4, "housing_type": "any"},
        "recommended_card": "6x9",
        "recommended_quality": "standard",
        "typical_campaign": "5,000-10,000 homes",
        "notes": "Tight radius — people won't drive far for a gym. Apartments are fine (often young, active). Age 20-45 is the sweet spot.",
    },
    "auto_services": {
        "description": "Auto repair, tire shops, car wash, detailing, oil change",
        "primary_filter": "proximity",
        "weights": {"proximity": 0.35, "density": 0.25, "income": 0.20, "housing_type": 0.10, "home_value": 0.10},
        "defaults": {"radius": 5, "housing_type": "any"},
        "recommended_card": "6x9",
        "recommended_quality": "standard",
        "typical_campaign": "10,000-20,000 homes",
        "notes": "Proximity and density. Everyone needs car service. Mass market — don't over-filter demographics.",
    },
    "general_retail": {
        "description": "Clothing stores, gift shops, pet stores, general retail",
        "primary_filter": "proximity",
        "weights": {"proximity": 0.35, "income": 0.25, "density": 0.20, "home_value": 0.10, "housing_type": 0.10},
        "defaults": {"radius": 5, "housing_type": "any"},
        "recommended_card": "6x11",
        "recommended_quality": "standard",
        "typical_campaign": "8,000-15,000 homes",
        "notes": "Depends heavily on the specific retailer. Adjust income filter based on price point.",
    },
    "education": {
        "description": "Tutoring, learning centers, private schools, music lessons, martial arts",
        "primary_filter": "family_demographics",
        "weights": {"family_score": 0.35, "proximity": 0.25, "income": 0.20, "ownership": 0.10, "housing_type": 0.10},
        "defaults": {"housing_type": "homes", "radius": 7, "age_group": "family"},
        "recommended_card": "6x11",
        "recommended_quality": "standard",
        "typical_campaign": "8,000-12,000 homes",
        "notes": "Families with school-age kids. HH size and under-19 percentage are the key signals.",
    },
}


class EDDMTools:
    """Tool functions for the EDDM Campaign Agent."""

    def __init__(self, compact_db_path, geometry_db_path=None, neighborhoods_path=None):
        """Load the route database and neighborhood definitions."""
        with open(compact_db_path) as f:
            db = json.load(f)
        self.metadata = db["metadata"]
        self.routes = db["routes"]
        self.routes_by_id = {r["route_id"]: r for r in self.routes}

        self.geometry = None
        if geometry_db_path and os.path.exists(geometry_db_path):
            import gzip
            _opener = gzip.open if geometry_db_path.endswith(".gz") else open
            with _opener(geometry_db_path, "rt", encoding="utf-8") as f:
                self.geometry = json.load(f)

        # Load neighborhood definitions
        if neighborhoods_path is None:
            neighborhoods_path = os.path.join(os.path.dirname(os.path.abspath(compact_db_path)), "neighborhoods.json")
        self.neighborhoods = {}
        self._neighborhood_index = {}  # lowercase name/alias → key
        if os.path.exists(neighborhoods_path):
            with open(neighborhoods_path) as f:
                raw = json.load(f)
            for key, nhood in raw.items():
                if key.startswith("_"):
                    continue
                self.neighborhoods[key] = nhood
                # Build fuzzy lookup index
                self._neighborhood_index[key] = key
                display = nhood.get("display_name", "").lower()
                if display:
                    self._neighborhood_index[display] = key
                for alias in nhood.get("aliases", []):
                    if alias:
                        self._neighborhood_index[alias.lower()] = key

    @staticmethod
    def _haversine(lat1, lon1, lat2, lon2):
        R = 3959
        dl = math.radians(lat2 - lat1)
        do = math.radians(lon2 - lon1)
        a = math.sin(dl/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(do/2)**2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

    def get_playbook(self, business_type: str) -> dict:
        """Get the business type playbook entry. Returns default if not found."""
        # Try exact match first
        if business_type in BUSINESS_PLAYBOOK:
            return BUSINESS_PLAYBOOK[business_type]
        # Try keyword matching
        bt_lower = business_type.lower()
        for key, playbook in BUSINESS_PLAYBOOK.items():
            if key.replace("_", " ") in bt_lower or any(w in bt_lower for w in key.split("_")):
                return playbook
            if any(w in bt_lower for w in playbook["description"].lower().split(", ")):
                return playbook
        # Return generic default
        return {
            "weights": {"proximity": 0.30, "income": 0.25, "home_value": 0.20, "ownership": 0.15, "housing_type": 0.10},
            "defaults": {"radius": 5, "housing_type": "any"},
            "recommended_card": "6x11",
            "recommended_quality": "standard",
            "notes": "No specific playbook found. Using balanced defaults.",
        }

    # ─── NEIGHBORHOOD LOOKUP ───

    def resolve_neighborhood(self, name: str) -> Optional[dict]:
        """Resolve a neighborhood name (fuzzy) to its definition. Returns None if not found."""
        normalized = name.lower().strip()
        # Exact match on key, display name, or alias
        if normalized in self._neighborhood_index:
            key = self._neighborhood_index[normalized]
            return {**self.neighborhoods[key], "_key": key}
        # Substring match — find best match
        matches = []
        for lookup, key in self._neighborhood_index.items():
            if normalized in lookup or lookup in normalized:
                matches.append(key)
        if len(matches) == 1:
            key = matches[0]
            return {**self.neighborhoods[key], "_key": key}
        if len(matches) > 1:
            # Deduplicate and prefer shortest key (most specific match)
            unique = list(dict.fromkeys(matches))
            if len(unique) == 1:
                key = unique[0]
                return {**self.neighborhoods[key], "_key": key}
            # Return first match (best effort)
            key = unique[0]
            return {**self.neighborhoods[key], "_key": key}
        return None

    def resolve_neighborhoods(self, names: list) -> list:
        """Resolve a list of neighborhood names. Returns list of {name, resolved, definition}."""
        results = []
        for name in names:
            defn = self.resolve_neighborhood(name)
            results.append({
                "input": name,
                "resolved": defn is not None,
                "key": defn["_key"] if defn else None,
                "display_name": defn["display_name"] if defn else None,
                "definition": defn,
            })
        return results

    def list_neighborhoods(self, zip_code: str = None, tier: str = None) -> list:
        """List all known neighborhoods with route counts. Optionally filter by ZIP or tier."""
        results = []
        for key, nhood in self.neighborhoods.items():
            if zip_code and zip_code not in nhood.get("zip_codes", []):
                continue
            if tier and nhood.get("tier") != tier:
                continue
            route_ids = self._routes_in_neighborhood(nhood)
            residential_count = sum(
                self.routes_by_id[rid]["residential_count"]
                for rid in route_ids if rid in self.routes_by_id
            )
            results.append({
                "key": key,
                "display_name": nhood["display_name"],
                "tier": nhood.get("tier", "association"),
                "zip_codes": nhood.get("zip_codes", []),
                "center": nhood.get("center"),
                "route_count": len(route_ids),
                "residential_count": residential_count,
                "description": nhood.get("description", ""),
            })
        results.sort(key=lambda x: x["display_name"])
        return results

    def _routes_in_neighborhood(self, nhood: dict) -> list:
        """Return route IDs for a neighborhood. Uses pre-matched route_ids from GIS
        boundaries when available, falls back to center+radius circle matching.
        If route_ids is explicitly empty (GIS polygon too small for any centroid),
        finds the nearest route(s) to the neighborhood center."""
        # Prefer explicit route_ids (from GIS polygon matching)
        if nhood.get("route_ids"):
            return [rid for rid in nhood["route_ids"] if rid in self.routes_by_id]

        center = nhood.get("center")
        if not center or not center.get("lat"):
            return []

        # If route_ids key exists but is empty, the GIS polygon was too small —
        # find nearest route(s) within 0.75 mi of the neighborhood center
        if "route_ids" in nhood and len(nhood["route_ids"]) == 0:
            nearest = []
            for r in self.routes:
                if r["centroid"]:
                    dist = self._haversine(center["lat"], center["lon"], r["centroid"]["lat"], r["centroid"]["lon"])
                    if dist <= 0.75:
                        nearest.append((dist, r["route_id"]))
            nearest.sort(key=lambda x: x[0])
            # Return at least 1, up to 3 nearest routes
            return [rid for _, rid in nearest[:max(1, min(3, len(nearest)))]]

        # Fallback: center + radius circle (for manually-defined neighborhoods)
        radius = nhood.get("radius", 1.0)
        matching = []
        for r in self.routes:
            if r["centroid"]:
                dist = self._haversine(center["lat"], center["lon"], r["centroid"]["lat"], r["centroid"]["lon"])
                if dist <= radius:
                    matching.append(r["route_id"])
        return matching

    # ─── QUERY ROUTES ───

    def query_routes(
        self,
        center_lat: float = None,
        center_lon: float = None,
        max_radius: float = None,
        min_income: int = None,
        max_income: int = None,
        min_home_value: int = None,
        max_home_value: int = None,
        min_own_pct: int = None,
        housing_type: str = None,  # "homes", "apartments", "any"
        min_residential: int = 200,
        zip_codes: list = None,
        neighborhoods: list = None,
    ) -> list:
        """Filter routes from the database. Returns list of route dicts with distance added.

        neighborhoods: list of neighborhood names (fuzzy matched). Routes must fall within
        at least one of the specified neighborhoods. Can be combined with other filters.
        """
        # Pre-compute allowed route IDs if neighborhoods specified
        neighborhood_route_ids = None
        neighborhood_matches = []
        if neighborhoods:
            neighborhood_route_ids = set()
            for name in neighborhoods:
                defn = self.resolve_neighborhood(name)
                if defn:
                    neighborhood_matches.append(defn["display_name"])
                    route_ids = self._routes_in_neighborhood(defn)
                    neighborhood_route_ids.update(route_ids)

        results = []
        for r in self.routes:
            # Neighborhood filter
            if neighborhood_route_ids is not None and r["route_id"] not in neighborhood_route_ids:
                continue
            # ZIP filter
            if zip_codes and r["zip_code"] not in zip_codes:
                continue
            # Minimum residential
            if r["residential_count"] < min_residential:
                continue
            # Distance
            dist = None
            if center_lat and center_lon and r["centroid"]:
                dist = self._haversine(center_lat, center_lon, r["centroid"]["lat"], r["centroid"]["lon"])
                if max_radius and dist > max_radius:
                    continue
            # Income
            if min_income and (r["usps_med_income"] or 0) < min_income:
                continue
            if max_income and (r["usps_med_income"] or 0) > max_income:
                continue
            # Home value
            hv = r["census_data"].get("avg_home_value") or 0
            if min_home_value and hv < min_home_value:
                continue
            if max_home_value and hv > max_home_value:
                continue
            # Ownership
            if min_own_pct and (r["census_data"].get("own_pct") or 0) < min_own_pct:
                continue
            # Housing type
            if housing_type == "homes" and r["usps_avg_hh_size"] < 1.8:
                continue  # Low HH size = likely apartments
            if housing_type == "apartments" and r["usps_avg_hh_size"] > 2.5:
                continue

            result = {**r, "_distance": dist}
            results.append(result)

        return results

    # ─── SCORE ROUTES ───

    def score_routes(self, routes: list, business_type: str = "general", criteria: dict = None) -> list:
        """Score routes based on business type playbook and criteria.
        
        criteria can include:
            - center_lat, center_lon: for proximity scoring
            - max_radius: max distance in miles
            - min_home_value: target home value
            - min_income: target income
            - target_age: "family", "senior", "young_adult", "any"
        """
        criteria = criteria or {}
        playbook = self.get_playbook(business_type)
        weights = playbook.get("weights", {})

        center_lat = criteria.get("center_lat")
        center_lon = criteria.get("center_lon")
        max_radius = criteria.get("max_radius", 10)
        min_hv = criteria.get("min_home_value", 0)
        min_inc = criteria.get("min_income", 0)
        target_age = criteria.get("target_age", "any")

        scored = []
        for r in routes:
            score = 0
            reasons = []
            dist = r.get("_distance")

            # Proximity score (0-100 contribution)
            if dist is not None and max_radius > 0:
                prox = max(0, 1 - dist / max_radius) * 100
                score += prox * weights.get("proximity", 0.25)
                if dist <= 2:
                    reasons.append(f"{dist:.1f}mi away")

            # Home value score
            hv = r["census_data"].get("avg_home_value") or 0
            if min_hv > 0 and hv > 0:
                hv_ratio = min(hv / min_hv, 1.5)
                hv_score = min(hv_ratio * 100, 100)
                w = weights.get("home_value", 0.20)
                score += hv_score * w
                if hv >= min_hv:
                    reasons.append(f"Home val ${hv:,}")
            elif hv > 0:
                # No filter, use as general quality signal
                hv_norm = min(hv / 500000, 1.0) * 100
                score += hv_norm * weights.get("home_value", 0.20) * 0.5

            # Income score
            inc = r["usps_med_income"] or 0
            if min_inc > 0 and inc > 0:
                inc_ratio = min(inc / min_inc, 1.5)
                inc_score = min(inc_ratio * 100, 100)
                score += inc_score * weights.get("income", 0.25)
                if inc >= min_inc:
                    reasons.append(f"${inc//1000}k income")
            elif inc > 0:
                inc_norm = min(inc / 100000, 1.0) * 100
                score += inc_norm * weights.get("income", 0.25) * 0.5

            # Ownership score
            own = r["census_data"].get("own_pct") or 0
            if own > 0:
                own_score = min(own / 80 * 100, 100)
                score += own_score * weights.get("ownership", 0.15)
                if own >= 80:
                    reasons.append(f"{own}% owners")

            # Housing type score
            hh = r["usps_avg_hh_size"]
            ht_weight = weights.get("housing_type", 0.10)
            if criteria.get("housing_type") == "homes":
                if hh >= 2.3:
                    score += 100 * ht_weight
                elif hh >= 2.0:
                    score += 60 * ht_weight
            elif criteria.get("housing_type") == "apartments":
                if hh < 2.0:
                    score += 100 * ht_weight
            else:
                score += 70 * ht_weight  # Neutral

            # Family score (for dental, education, etc.)
            if target_age == "family":
                fam_pct = r.get("pct_family_age", 0)
                kid_pct = r.get("pct_under_19", 0)
                fam_score = (fam_pct * 0.6 + kid_pct * 2 * 0.4)  # Kids weighted higher
                fam_score = min(fam_score, 100)
                w = weights.get("family_score", 0)
                score += fam_score * w
                if kid_pct >= 15:
                    reasons.append(f"{kid_pct}% under 19")
            elif target_age == "senior":
                sen_pct = r.get("pct_senior", 0)
                score += min(sen_pct * 1.5, 100) * weights.get("age_match", 0)
            elif target_age == "young_adult":
                ya = r["usps_age_brackets"].get("20_24", 0) + r["usps_age_brackets"].get("25_34", 0)
                total = sum(r["usps_age_brackets"].values())
                ya_pct = ya / total * 100 if total > 0 else 0
                score += min(ya_pct * 2, 100) * weights.get("age_match", 0)

            # Density bonus (useful for mass-market campaigns)
            density_w = weights.get("density", 0)
            if density_w > 0:
                density_score = min(r["residential_count"] / 600 * 100, 100)
                score += density_score * density_w

            final_score = round(min(score, 100))
            scored.append({
                **r,
                "_score": final_score,
                "_reasons": reasons,
            })

        scored.sort(key=lambda r: -r["_score"])
        return scored

    # ─── SELECT CAMPAIGN ───

    def select_campaign(self, scored_routes: list, target_homes: int, min_score: int = 20) -> dict:
        """Select routes to fill a target home count. Returns campaign summary."""
        selected = []
        total_res = 0
        total_bus = 0

        for r in scored_routes:
            if total_res >= target_homes:
                break
            if r["_score"] >= min_score:
                selected.append(r)
                total_res += r["residential_count"]
                total_bus += r["business_count"]

        route_ids = [r["route_id"] for r in selected]
        avg_score = round(sum(r["_score"] for r in selected) / len(selected)) if selected else 0
        avg_income = round(sum(r["usps_med_income"] for r in selected) / len(selected)) if selected else 0
        avg_dist = round(sum(r.get("_distance", 0) or 0 for r in selected) / len(selected), 1) if selected else 0

        # Group by ZIP for ordering
        by_zip = defaultdict(list)
        for r in selected:
            by_zip[r["zip_code"]].append(r["carrier_route"])

        return {
            "route_ids": route_ids,
            "routes": selected,
            "total_routes": len(selected),
            "total_residential": total_res,
            "total_business": total_bus,
            "avg_score": avg_score,
            "avg_income": avg_income,
            "avg_distance_miles": avg_dist,
            "target_met": total_res >= target_homes,
            "shortfall": max(0, target_homes - total_res),
            "routes_by_zip": dict(by_zip),
        }

    # ─── GENERATE MAP ───

    def generate_map(
        self,
        selected_route_ids: list,
        store_lat: float = None,
        store_lon: float = None,
        radius_miles: float = None,
        output_path: str = "campaign_map.png",
        title: str = "EDDM Campaign Map",
        subtitle: str = None,
        store_label: str = "Client location",
        show_available: bool = True,
    ) -> str:
        """Render a campaign map as PNG.

        Notes:
          - We NEVER assume/store a client location. If store_lat/store_lon are not provided,
            the map renders routes only (no pin, no radius).
          - Default matches the original dark map: all routes faint, selected routes highlighted.
        """
        if not self.geometry:
            return None

        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches

        # Dark styling (match original)
        plt.rcParams.update({
            "font.family": "DejaVu Sans",
        })

        fig, ax = plt.subplots(1, 1, figsize=(12, 10))
        fig.patch.set_facecolor("#ffffff")
        ax.set_facecolor("#ffffff")
        ax.grid(False)
        ax.tick_params(colors="#94a3b8", labelsize=7)
        for spine in ax.spines.values():
            spine.set_color("#e2e8f0")

        selected_set = set(selected_route_ids)

        # Helper: draw route paths.
        # Geometry can be line-like (len>=2) or polygon-ish (len>=3). We support both.
        def _draw_paths(paths, edge, fill=None, edge_alpha=1.0, fill_alpha=0.2, lw=1.2, z=2):
            drew_any = False
            for path in paths or []:
                if len(path) < 2:
                    continue
                lons = [p[0] for p in path]
                lats = [p[1] for p in path]

                # Outline always
                ax.plot(lons, lats, color=edge, alpha=edge_alpha, linewidth=lw, solid_capstyle="round", zorder=z)
                drew_any = True

                # Fill only when it looks like a polygon (3+ points)
                if fill and len(path) >= 3:
                    pts = list(zip(lons, lats))
                    poly = mpatches.Polygon(
                        pts,
                        closed=True,
                        edgecolor="none",
                        facecolor=fill,
                        linewidth=0,
                        alpha=fill_alpha,
                        zorder=max(0, z - 1),
                    )
                    ax.add_patch(poly)
            return drew_any

        # Draw routes: all available faint, selected highlighted (match original)
        drew = False
        for route_id, geo in self.geometry.items():
            is_selected = route_id in selected_set
            paths = geo.get("paths", [])
            if is_selected:
                # Selected: bright teal/green
                drew = _draw_paths(paths, edge="#10b981", fill=None, edge_alpha=0.95, lw=2.0, z=4) or drew
            else:
                # Available: dark slate
                if show_available:
                    # "City" silhouette layer
                    drew = _draw_paths(paths, edge="#94a3b8", fill=None, edge_alpha=0.22, lw=1.0, z=1) or drew

        # City outline: approximate boundary via convex hull of all known geometry points.
        # (No external GIS deps; hull gives a clean, client-friendly outline.)
        def _convex_hull(points):
            # Monotonic chain; points = [(x,y), ...]
            pts = sorted(set(points))
            if len(pts) <= 2:
                return pts

            def cross(o, a, b):
                return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])

            lower = []
            for p in pts:
                while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
                    lower.pop()
                lower.append(p)

            upper = []
            for p in reversed(pts):
                while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
                    upper.pop()
                upper.append(p)

            return lower[:-1] + upper[:-1]

        all_points = []
        for geo in self.geometry.values():
            for path in geo.get("paths", []) or []:
                for p in path or []:
                    if len(p) >= 2:
                        all_points.append((p[0], p[1]))

        if False and len(all_points) >= 3:
            hull = _convex_hull(all_points)
            if len(hull) >= 3:
                hx = [p[0] for p in hull] + [hull[0][0]]
                hy = [p[1] for p in hull] + [hull[0][1]]
                # High-contrast perimeter outline (client-facing)
                ax.plot(hx, hy, color="#0f172a", linewidth=3.6, alpha=0.95, zorder=6)
                ax.plot(hx, hy, color="#38bdf8", linewidth=1.6, alpha=0.65, zorder=7)

        # Ensure axes scale to data
        if drew:
            ax.relim()
            ax.autoscale_view()

        # Radius circle (ONLY when explicitly provided)
        if store_lat is not None and store_lon is not None and radius_miles is not None:
            deg_per_mile = 1 / 69
            circle = plt.Circle(
                (store_lon, store_lat),
                radius_miles * deg_per_mile,
                fill=True,
                facecolor="#22c55e",
                edgecolor="#16a34a",
                linewidth=1.2,
                linestyle="--",
                alpha=0.08,
                zorder=0,
            )
            ax.add_patch(circle)

        # Client marker (ONLY when explicitly provided)
        if store_lat is not None and store_lon is not None:
            ax.plot(
                store_lon,
                store_lat,
                "o",
                color="#ef4444",
                markersize=8,
                markeredgecolor="white",
                markeredgewidth=1.6,
                zorder=10,
            )
            ax.annotate(
                store_label,
                (store_lon, store_lat),
                textcoords="offset points",
                xytext=(10, 8),
                color="#0f172a",
                fontsize=9,
                fontweight="bold",
            )

        # Titles
        ax.set_title(title, color="#0f172a", fontsize=14, fontweight="bold", pad=14)
        if subtitle:
            ax.text(0.5, 1.01, subtitle, transform=ax.transAxes, ha="center", va="bottom", color="#94a3b8", fontsize=9)

        # Frame + aspect
        ax.set_aspect("equal")

        # Legend
        legend_elements = [
            mpatches.Patch(color="#10b981", label=f"Selected ({len(selected_route_ids)} routes)"),
        ]
        if show_available:
            legend_elements.append(mpatches.Patch(color="#1e293b", label="Available"))
        if store_lat is not None and store_lon is not None:
            legend_elements.append(plt.Line2D([0],[0], marker="o", color="#ef4444", markeredgecolor="white", markersize=8, linestyle="None", label=store_label))
        ax.legend(handles=legend_elements, loc="lower left", facecolor="#ffffff", edgecolor="#e2e8f0", labelcolor="#0f172a", fontsize=9)

        # Stats annotation (weighted income is more meaningful than simple avg)
        sel_routes = [self.routes_by_id[rid] for rid in selected_route_ids if rid in self.routes_by_id]
        total_homes = sum(r.get("residential_count", 0) for r in sel_routes)
        weighted_income = 0
        denom = 0
        for r in sel_routes:
            homes = int(r.get("residential_count", 0) or 0)
            inc = int(r.get("usps_med_income", 0) or 0)
            weighted_income += inc * homes
            denom += homes
        w_inc = round(weighted_income / denom) if denom else 0
        stats = f"{len(selected_route_ids)} routes | {total_homes:,} homes | est. med income ${w_inc:,}"
        ax.text(0.98, 0.02, stats, transform=ax.transAxes, color="#64748b", fontsize=8,
                ha="right", va="bottom")

        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="#ffffff")
        plt.close()

        return output_path

    # ─── GENERATE ORDER SHEET ───

    def generate_order_sheet(self, selected_route_ids: list) -> str:
        """Generate a formatted order sheet for USPS EDDM tool entry."""
        routes = [self.routes_by_id[rid] for rid in selected_route_ids if rid in self.routes_by_id]
        by_zip = defaultdict(list)
        for r in routes:
            by_zip[r["zip_code"]].append(r)

        lines = []
        lines.append("=" * 50)
        lines.append("USPS EDDM ORDER SHEET")
        lines.append("=" * 50)
        lines.append(f"Total routes: {len(routes)}")
        lines.append(f"Total homes: {sum(r['residential_count'] for r in routes):,}")
        lines.append(f"Drop-off: EDDM Retail at post office")
        lines.append("")

        for zip_code in sorted(by_zip):
            zip_routes = sorted(by_zip[zip_code], key=lambda r: r["carrier_route"])
            zip_homes = sum(r["residential_count"] for r in zip_routes)
            lines.append(f"--- ZIP {zip_code} ({len(zip_routes)} routes, {zip_homes:,} homes) ---")
            lines.append(f"  Facility: {zip_routes[0].get('facility_name', 'N/A')}")
            lines.append(f"  Routes: {', '.join(r['carrier_route'] for r in zip_routes)}")
            for r in zip_routes:
                lines.append(f"    {r['carrier_route']}: {r['residential_count']:,} res / {r['business_count']} bus")
            lines.append("")

        lines.append("INSTRUCTIONS:")
        lines.append("1. Go to https://eddm.usps.com/eddm/select-routes.htm")
        lines.append("2. Search each ZIP code listed above")
        lines.append("3. Check the routes listed for that ZIP")
        lines.append("4. Proceed to checkout")
        lines.append("=" * 50)

        return "\n".join(lines)


