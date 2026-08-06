"""Tests for the deterministic route engine + artifact generation, on the bundled data."""
import os

import pytest

from eddm_cli import get_tools
from brief_parser import parse_brief, criteria_to_query_kwargs, criteria_to_score_criteria


@pytest.fixture(scope="module")
def tools():
    return get_tools()


def test_data_loads(tools):
    assert len(tools.routes) > 100
    assert tools.geometry is not None
    assert len(tools.geometry) > 100


def test_query_score_select_reaches_target(tools):
    criteria = parse_brief(
        "dental office, homeowners, mail 8000 homes", use_llm=False
    )["criteria"]
    routes = tools.query_routes(**criteria_to_query_kwargs(criteria))
    assert routes, "expected some routes from the bundled data"
    scored = tools.score_routes(routes, criteria["business_type"], criteria_to_score_criteria(criteria))
    assert scored[0]["_score"] >= scored[-1]["_score"]  # sorted desc
    selection = tools.select_campaign(scored, criteria["target_homes"], criteria.get("min_score", 20))
    assert selection["total_routes"] >= 1
    # With 8k target against ~186k homes available, target should be met.
    assert selection["target_met"] is True
    assert selection["shortfall"] == 0


def test_shortfall_reported_when_target_impossible(tools):
    # Ask for far more homes than exist -> shortfall, not a crash.
    routes = tools.query_routes(min_residential=200)
    scored = tools.score_routes(routes, "general_retail", {})
    selection = tools.select_campaign(scored, target_homes=10_000_000, min_score=0)
    assert selection["target_met"] is False
    assert selection["shortfall"] > 0


def test_order_sheet_generation(tools):
    routes = tools.query_routes(zip_codes=None, min_residential=200)
    scored = tools.score_routes(routes, "general_retail", {})
    selection = tools.select_campaign(scored, target_homes=5000, min_score=0)
    sheet = tools.generate_order_sheet(selection["route_ids"])
    assert isinstance(sheet, str) and len(sheet) > 0
    # order sheet groups by ZIP; a selected route's ZIP should appear
    first_zip = selection["routes_by_zip"] and list(selection["routes_by_zip"].keys())[0]
    if first_zip:
        assert first_zip in sheet


def test_map_generation(tmp_path, tools):
    matplotlib = pytest.importorskip("matplotlib")
    routes = tools.query_routes(min_residential=200)
    scored = tools.score_routes(routes, "general_retail", {})
    selection = tools.select_campaign(scored, target_homes=5000, min_score=0)
    out = tmp_path / "map.png"
    path = tools.generate_map(
        selected_route_ids=selection["route_ids"],
        store_lat=27.806, store_lon=-82.638, radius_miles=8,
        output_path=str(out), title="Test",
    )
    assert os.path.exists(path)
    assert os.path.getsize(path) > 1000
