#!/usr/bin/env python3
"""
EDDM Campaign CLI
=================
Turns a plain-language campaign brief into a selected set of USPS carrier routes,
a coverage map, and a USPS-ready order sheet. Each subcommand maps to a step of the
deterministic route engine (query -> score -> select) and prints JSON to stdout.

Usage:
    python eddm_cli.py parse    --brief "furniture store near downtown, homeowners, $400k+ homes, 10k homes"
    python eddm_cli.py campaign --brief "..." [--store-lat 27.8 --store-lon -82.6] [--target-homes 10000]
    python eddm_cli.py query    --center-lat 27.806 --center-lon -82.638 --max-radius 8
    python eddm_cli.py score    routes.json --business-type luxury_retail --center-lat 27.806 --center-lon -82.638
    python eddm_cli.py select   scored.json --target-homes 10000
    python eddm_cli.py map      selection.json --store-lat 27.806 --store-lon -82.638 --radius 8
    python eddm_cli.py order-sheet selection.json
    python eddm_cli.py stats

No pricing, quoting, or vendor logic is included. This tool selects routes and produces
mailing paperwork from public USPS + Census data only.
"""

import argparse
import json
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "output")

# Ensure sibling modules are importable
sys.path.insert(0, SCRIPT_DIR)
from eddm_tools import EDDMTools, BUSINESS_PLAYBOOK
from brief_parser import parse_brief, criteria_to_query_kwargs, criteria_to_score_criteria


def get_tools():
    """Load EDDMTools with default data paths (gzipped geometry preferred)."""
    compact = os.path.join(SCRIPT_DIR, "route_db_compact.json")
    geometry = os.path.join(SCRIPT_DIR, "route_db_geometry.json.gz")
    if not os.path.exists(geometry):
        geometry = os.path.join(SCRIPT_DIR, "route_db_geometry.json")
    return EDDMTools(compact, geometry if os.path.exists(geometry) else None)


def ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return OUTPUT_DIR


def write_intermediate(data, name):
    """Write intermediate JSON to output dir, return path."""
    out_dir = ensure_output_dir()
    ts = int(time.time())
    path = os.path.join(out_dir, f"{name}_{ts}.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    return path


def load_json_file(path):
    with open(path) as f:
        return json.load(f)


def strip_internal_fields(routes):
    """Create a summary of routes suitable for JSON output (drop large nested data)."""
    summary = []
    for r in routes:
        summary.append({
            "route_id": r["route_id"],
            "zip_code": r["zip_code"],
            "carrier_route": r["carrier_route"],
            "residential_count": r["residential_count"],
            "business_count": r["business_count"],
            "usps_med_income": r["usps_med_income"],
            "usps_avg_hh_size": r["usps_avg_hh_size"],
            "avg_home_value": r.get("census_data", {}).get("avg_home_value", 0),
            "own_pct": r.get("census_data", {}).get("own_pct", 0),
            "sfh_pct": r.get("census_data", {}).get("sfh_pct", 0),
            "facility_name": r.get("facility_name", ""),
            "_distance": r.get("_distance"),
            "_score": r.get("_score"),
            "_reasons": r.get("_reasons"),
        })
    return summary


# ─── Subcommand: parse (brief -> structured criteria) ───

def cmd_parse(args):
    parsed = parse_brief(args.brief, use_llm=(None if args.llm == "auto" else args.llm == "on"))
    print(json.dumps(parsed, indent=2))


# ─── Subcommand: playbook ───

def cmd_playbook(args):
    if args.business_type == "list":
        result = {
            "business_types": list(BUSINESS_PLAYBOOK.keys()),
            "count": len(BUSINESS_PLAYBOOK),
            "descriptions": {k: v["description"] for k, v in BUSINESS_PLAYBOOK.items()},
        }
    else:
        tools = get_tools()
        result = tools.get_playbook(args.business_type)
        result["business_type"] = args.business_type
    print(json.dumps(result, indent=2))


# ─── Subcommand: list-neighborhoods ───

def cmd_list_neighborhoods(args):
    tools = get_tools()
    results = tools.list_neighborhoods(
        zip_code=args.zip if hasattr(args, "zip") and args.zip else None,
        tier=args.tier if hasattr(args, "tier") and args.tier else None,
    )
    output = {
        "total_neighborhoods": len(results),
        "total_routes_covered": sum(n["route_count"] for n in results),
        "neighborhoods": results,
    }
    print(json.dumps(output, indent=2))


# ─── Subcommand: resolve-neighborhoods ───

def cmd_resolve_neighborhoods(args):
    tools = get_tools()
    names = [n.strip() for n in args.names.split(",")]
    results = tools.resolve_neighborhoods(names)
    print(json.dumps(results, indent=2))


# ─── Subcommand: query ───

def cmd_query(args):
    tools = get_tools()
    kwargs = {
        "center_lat": args.center_lat,
        "center_lon": args.center_lon,
        "max_radius": args.max_radius,
        "min_income": args.min_income,
        "max_income": args.max_income,
        "min_home_value": args.min_home_value,
        "max_home_value": args.max_home_value,
        "min_own_pct": args.min_own_pct,
        "housing_type": args.housing_type,
        "min_residential": args.min_residential,
    }
    if args.zip_codes:
        kwargs["zip_codes"] = args.zip_codes.split(",")
    if args.neighborhoods:
        kwargs["neighborhoods"] = [n.strip() for n in args.neighborhoods.split(",")]

    routes = tools.query_routes(**kwargs)
    full_path = write_intermediate(routes, "query_routes")
    result = {
        "total_routes": len(routes),
        "total_residential": sum(r["residential_count"] for r in routes),
        "total_business": sum(r["business_count"] for r in routes),
        "routes_file": full_path,
        "routes": strip_internal_fields(routes),
    }
    print(json.dumps(result, indent=2))


# ─── Subcommand: score ───

def cmd_score(args):
    tools = get_tools()
    routes = load_json_file(args.routes_file)
    criteria = {
        "center_lat": args.center_lat,
        "center_lon": args.center_lon,
        "max_radius": args.max_radius,
        "min_home_value": args.min_home_value,
        "min_income": args.min_income,
        "housing_type": args.housing_type,
        "target_age": args.target_age,
    }
    criteria = {k: v for k, v in criteria.items() if v is not None}
    scored = tools.score_routes(routes, args.business_type, criteria)
    full_path = write_intermediate(scored, "scored_routes")
    result = {
        "total_scored": len(scored),
        "top_score": scored[0]["_score"] if scored else 0,
        "avg_score": round(sum(r["_score"] for r in scored) / len(scored)) if scored else 0,
        "scored_file": full_path,
        "top_20": strip_internal_fields(scored[:20]),
    }
    print(json.dumps(result, indent=2))


# ─── Subcommand: select ───

def cmd_select(args):
    tools = get_tools()
    scored = load_json_file(args.scored_file)
    selection = tools.select_campaign(scored, args.target_homes, args.min_score)
    full_path = write_intermediate(selection, "selection")
    result = {
        "route_ids": selection["route_ids"],
        "total_routes": selection["total_routes"],
        "total_residential": selection["total_residential"],
        "total_business": selection["total_business"],
        "avg_score": selection["avg_score"],
        "avg_income": selection["avg_income"],
        "avg_distance_miles": selection["avg_distance_miles"],
        "target_met": selection["target_met"],
        "shortfall": selection["shortfall"],
        "routes_by_zip": selection["routes_by_zip"],
        "selection_file": full_path,
    }
    print(json.dumps(result, indent=2))


# ─── Subcommand: map ───

def cmd_map(args):
    tools = get_tools()
    selection = load_json_file(args.selection_file)
    route_ids = selection.get("route_ids", selection) if isinstance(selection, dict) else selection

    out_dir = ensure_output_dir()
    ts = int(time.time())
    output_path = os.path.join(out_dir, f"campaign_map_{ts}.png")
    title = args.title or "EDDM Campaign Map"

    map_path = tools.generate_map(
        selected_route_ids=route_ids,
        store_lat=args.store_lat,
        store_lon=args.store_lon,
        radius_miles=args.radius,
        output_path=output_path,
        title=title,
    )
    print(json.dumps({"map_file": map_path, "route_count": len(route_ids)}, indent=2))


# ─── Subcommand: order-sheet ───

def cmd_order_sheet(args):
    tools = get_tools()
    selection = load_json_file(args.selection_file)
    route_ids = selection.get("route_ids", selection) if isinstance(selection, dict) else selection
    sheet = tools.generate_order_sheet(route_ids)
    if args.save:
        out_dir = ensure_output_dir()
        ts = int(time.time())
        path = os.path.join(out_dir, f"order_sheet_{ts}.txt")
        with open(path, "w") as f:
            f.write(sheet)
        result = {"order_sheet_file": path, "text": sheet}
    else:
        result = {"text": sheet}
    print(json.dumps(result, indent=2))


# ─── Subcommand: campaign (brief -> deliverables) ───

def cmd_campaign(args):
    tools = get_tools()
    out_dir = ensure_output_dir()
    ts = int(time.time())

    # Step 0: interpret the brief into structured, validated criteria.
    parsed = parse_brief(args.brief, use_llm=(None if args.llm == "auto" else args.llm == "on"))
    criteria = dict(parsed["criteria"])

    # Explicit CLI flags override anything parsed from the brief.
    cli_overrides = {
        "center_lat": args.store_lat,
        "center_lon": args.store_lon,
        "max_radius": args.radius,
        "target_homes": args.target_homes,
        "min_home_value": args.min_home_value,
        "min_income": args.min_income,
        "housing_type": args.housing_type,
        "business_type": args.business_type,
    }
    for k, v in cli_overrides.items():
        if v is not None:
            criteria[k] = v
    if args.neighborhoods:
        criteria["neighborhoods"] = [n.strip() for n in args.neighborhoods.split(",")]

    business_type = criteria.get("business_type") or "general_retail"
    playbook = tools.get_playbook(business_type)
    defaults = playbook.get("defaults", {})
    # Apply playbook defaults only where the brief/CLI left a gap.
    criteria.setdefault("max_radius", defaults.get("radius", 5))
    criteria.setdefault("housing_type", defaults.get("housing_type", "any"))
    if criteria.get("min_home_value") is None and defaults.get("min_home_value"):
        criteria["min_home_value"] = defaults["min_home_value"]
    if criteria.get("min_income") is None and defaults.get("min_income"):
        criteria["min_income"] = defaults["min_income"]
    target_homes = int(criteria.get("target_homes") or 10000)

    # Step 1: Query
    routes = tools.query_routes(**criteria_to_query_kwargs(criteria))

    # Step 2: Score
    scored = tools.score_routes(routes, business_type, criteria_to_score_criteria(criteria))

    # Step 3: Select
    selection = tools.select_campaign(scored, target_homes, int(criteria.get("min_score", 20)))

    # Shortfall explanation (Issue 2: reach target OR explain why not)
    shortfall = selection.get("shortfall", 0)
    if selection.get("target_met"):
        shortfall_explanation = f"Target of {target_homes:,} homes met with {selection['total_routes']} routes."
    else:
        shortfall_explanation = (
            f"Reached {selection['total_residential']:,} of {target_homes:,} homes "
            f"({shortfall:,} short) from {len(scored)} candidate routes. "
            "Widen the radius, relax income/home-value filters, add ZIPs/neighborhoods, "
            "or lower --target-homes to close the gap."
        )

    # Step 4: Map
    map_path = None
    if tools.geometry:
        map_file = os.path.join(out_dir, f"campaign_map_{ts}.png")
        map_path = tools.generate_map(
            selected_route_ids=selection["route_ids"],
            store_lat=criteria.get("center_lat"),
            store_lon=criteria.get("center_lon"),
            radius_miles=criteria.get("max_radius"),
            output_path=map_file,
            title=f"EDDM Campaign - {selection['total_residential']:,} homes",
        )

    # Step 5: Order sheet
    order_sheet = tools.generate_order_sheet(selection["route_ids"])
    sheet_file = os.path.join(out_dir, f"order_sheet_{ts}.txt")
    with open(sheet_file, "w") as f:
        f.write(order_sheet)

    sel_file = write_intermediate(selection, "selection")

    result = {
        "brief": args.brief,
        "parse_source": parsed["source"],
        "parse_warnings": parsed["warnings"],
        "parsed_criteria": criteria,
        "business_type": business_type,
        "playbook": {
            "description": playbook.get("description", ""),
            "recommended_card": playbook.get("recommended_card", ""),
            "typical_campaign": playbook.get("typical_campaign", ""),
            "notes": playbook.get("notes", ""),
        },
        "query": {
            "center": {"lat": criteria.get("center_lat"), "lon": criteria.get("center_lon")},
            "radius_miles": criteria.get("max_radius"),
            "housing_type": criteria.get("housing_type"),
            "min_home_value": criteria.get("min_home_value"),
            "min_income": criteria.get("min_income"),
            "routes_found": len(routes),
        },
        "selection": {
            "total_routes": selection["total_routes"],
            "total_residential": selection["total_residential"],
            "total_business": selection["total_business"],
            "avg_score": selection["avg_score"],
            "avg_income": selection["avg_income"],
            "avg_distance_miles": selection["avg_distance_miles"],
            "target_homes": target_homes,
            "target_met": selection["target_met"],
            "shortfall": shortfall,
            "shortfall_explanation": shortfall_explanation,
            "routes_by_zip": selection["routes_by_zip"],
        },
        "order_sheet_file": sheet_file,
        "map_file": map_path,
        "selection_file": sel_file,
        "top_routes": strip_internal_fields(scored[:15]),
    }
    print(json.dumps(result, indent=2))


# ─── Subcommand: stats ───

def cmd_stats(args):
    tools = get_tools()
    zips = {}
    for r in tools.routes:
        z = r["zip_code"]
        if z not in zips:
            zips[z] = {"routes": 0, "residential": 0, "business": 0}
        zips[z]["routes"] += 1
        zips[z]["residential"] += r["residential_count"]
        zips[z]["business"] += r["business_count"]
    result = {
        "total_routes": len(tools.routes),
        "total_residential": sum(r["residential_count"] for r in tools.routes),
        "total_business": sum(r["business_count"] for r in tools.routes),
        "zip_codes": len(zips),
        "has_geometry": tools.geometry is not None,
        "geometry_routes": len(tools.geometry) if tools.geometry else 0,
        "by_zip": dict(sorted(zips.items())),
    }
    print(json.dumps(result, indent=2))


# ─── Argument Parser ───

def build_parser():
    parser = argparse.ArgumentParser(
        prog="eddm_cli",
        description="EDDM Campaign CLI - brief interpretation, route selection, scoring, maps, and order sheets",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # parse
    p = sub.add_parser("parse", help="Parse a plain-language brief into structured targeting criteria")
    p.add_argument("--brief", required=True, help="Campaign brief text")
    p.add_argument("--llm", choices=["auto", "on", "off"], default="auto",
                   help="auto: use LLM if OPENAI_API_KEY is set, else deterministic fallback")
    p.set_defaults(func=cmd_parse)

    # playbook
    p = sub.add_parser("playbook", help="Get business type playbook (or 'list' for all types)")
    p.add_argument("business_type", help="Business type key (e.g. luxury_retail) or 'list'")
    p.set_defaults(func=cmd_playbook)

    # query
    p = sub.add_parser("query", help="Filter routes by location, demographics, housing")
    p.add_argument("--center-lat", type=float)
    p.add_argument("--center-lon", type=float)
    p.add_argument("--max-radius", type=float)
    p.add_argument("--min-income", type=int)
    p.add_argument("--max-income", type=int)
    p.add_argument("--min-home-value", type=int)
    p.add_argument("--max-home-value", type=int)
    p.add_argument("--min-own-pct", type=int)
    p.add_argument("--housing-type", choices=["homes", "apartments", "any"])
    p.add_argument("--min-residential", type=int, default=200)
    p.add_argument("--zip-codes", help="Comma-separated ZIP codes")
    p.add_argument("--neighborhoods", help="Comma-separated neighborhood names (fuzzy matched)")
    p.set_defaults(func=cmd_query)

    # list-neighborhoods
    p = sub.add_parser("list-neighborhoods", help="List all known neighborhoods with route counts")
    p.add_argument("--zip", help="Filter to neighborhoods in this ZIP code")
    p.add_argument("--tier", choices=["broad", "association"], help="Filter by tier")
    p.set_defaults(func=cmd_list_neighborhoods)

    # resolve-neighborhoods
    p = sub.add_parser("resolve-neighborhoods", help="Resolve neighborhood names to definitions")
    p.add_argument("names", help="Comma-separated neighborhood names to resolve")
    p.set_defaults(func=cmd_resolve_neighborhoods)

    # score
    p = sub.add_parser("score", help="Score filtered routes by business type")
    p.add_argument("routes_file", help="JSON file from query output (routes_file path)")
    p.add_argument("--business-type", default="general_retail")
    p.add_argument("--center-lat", type=float)
    p.add_argument("--center-lon", type=float)
    p.add_argument("--max-radius", type=float, default=10)
    p.add_argument("--min-home-value", type=int)
    p.add_argument("--min-income", type=int)
    p.add_argument("--housing-type", choices=["homes", "apartments", "any"])
    p.add_argument("--target-age", choices=["family", "senior", "young_adult", "any"])
    p.set_defaults(func=cmd_score)

    # select
    p = sub.add_parser("select", help="Auto-select top routes to hit target home count")
    p.add_argument("scored_file", help="JSON file from score output (scored_file path)")
    p.add_argument("--target-homes", type=int, required=True)
    p.add_argument("--min-score", type=int, default=20)
    p.set_defaults(func=cmd_select)

    # map
    p = sub.add_parser("map", help="Generate campaign map PNG")
    p.add_argument("selection_file", help="JSON file from select output (selection_file path)")
    p.add_argument("--store-lat", type=float)
    p.add_argument("--store-lon", type=float)
    p.add_argument("--radius", type=float)
    p.add_argument("--title", help="Map title")
    p.set_defaults(func=cmd_map)

    # order-sheet
    p = sub.add_parser("order-sheet", help="Generate USPS EDDM order sheet")
    p.add_argument("selection_file", help="JSON file from select output (selection_file path)")
    p.add_argument("--save", action="store_true", help="Save to file in addition to stdout")
    p.set_defaults(func=cmd_order_sheet)

    # campaign (brief -> deliverables)
    p = sub.add_parser("campaign", help="Full pipeline: brief -> routes -> map -> USPS order sheet")
    p.add_argument("--brief", required=True, help="Plain-language campaign brief")
    p.add_argument("--llm", choices=["auto", "on", "off"], default="auto",
                   help="auto: use LLM if OPENAI_API_KEY is set, else deterministic fallback")
    p.add_argument("--business-type", help="Override business type (else parsed from brief)")
    p.add_argument("--store-lat", type=float, help="Store latitude (override)")
    p.add_argument("--store-lon", type=float, help="Store longitude (override)")
    p.add_argument("--radius", type=float, help="Max radius in miles (override)")
    p.add_argument("--target-homes", type=int, help="Target home count (override)")
    p.add_argument("--min-home-value", type=int, help="Min home value filter (override)")
    p.add_argument("--min-income", type=int, help="Min income filter (override)")
    p.add_argument("--housing-type", choices=["homes", "apartments", "any"], help="Override")
    p.add_argument("--neighborhoods", help="Comma-separated neighborhood names (override)")
    p.set_defaults(func=cmd_campaign)

    # stats
    p = sub.add_parser("stats", help="Print database statistics")
    p.set_defaults(func=cmd_stats)

    return parser


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
