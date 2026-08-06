"""Tests for the natural-language brief -> criteria layer."""
import json

import brief_parser
from brief_parser import parse_brief, criteria_to_query_kwargs, criteria_to_score_criteria


def test_fallback_detects_business_type():
    out = parse_brief("high-end furniture showroom downtown", use_llm=False)
    assert out["source"] == "fallback"
    assert out["criteria"]["business_type"] == "luxury_retail"


def test_fallback_money_disambiguation():
    # "$400k+ homes" is a home VALUE; "8000 homes" is a mail COUNT.
    c = parse_brief("target $400k+ homes, mail 8000 homes", use_llm=False)["criteria"]
    assert c["min_home_value"] == 400_000
    assert c["target_homes"] == 8000


def test_fallback_plain_count_is_not_home_value():
    c = parse_brief("dentist, homeowners, mail 10000 homes", use_llm=False)["criteria"]
    assert c.get("min_home_value") is None
    assert c["target_homes"] == 10000
    assert c["housing_type"] == "homes"


def test_fallback_income_and_radius():
    c = parse_brief("gym, household income $120k+, within 3 miles", use_llm=False)["criteria"]
    assert c["business_type"] == "fitness_wellness"
    assert c["min_income"] == 120_000
    assert c["max_radius"] == 3.0


def test_defaults_applied():
    c = parse_brief("a plain brief with nothing useful", use_llm=False)["criteria"]
    assert c["business_type"] == "general_retail"   # default
    assert c["target_homes"] == 10000               # default
    assert c["min_score"] == 20                      # default


def test_llm_path_mocked(monkeypatch):
    fake = {"business_type": "real_estate", "min_home_value": 600000, "target_homes": 12000}
    monkeypatch.setattr(brief_parser, "_llm_parse", lambda text: dict(fake))
    out = parse_brief("realtor farming a wealthy area", use_llm=True)
    assert out["source"] == "llm"
    assert out["criteria"]["business_type"] == "real_estate"
    assert out["criteria"]["min_home_value"] == 600000


def test_llm_invalid_falls_back(monkeypatch):
    # LLM returns an invalid business_type -> coerced, still valid criteria.
    monkeypatch.setattr(brief_parser, "_llm_parse", lambda text: {"business_type": "spaceship_sales"})
    out = parse_brief("something", use_llm=True)
    assert out["criteria"]["business_type"] == "general_retail"


def test_llm_none_uses_fallback(monkeypatch):
    monkeypatch.setattr(brief_parser, "_llm_parse", lambda text: None)
    out = parse_brief("pizza shop", use_llm=True)
    assert out["source"].startswith("fallback")
    assert out["criteria"]["business_type"] == "restaurant_casual"


def test_schema_validation_runs():
    # jsonschema may or may not be installed; when it is, valid criteria produce no schema warnings.
    out = parse_brief("dentist, $500k homes, mail 5000 homes", use_llm=False)
    schema_warnings = [w for w in out["warnings"] if w.startswith("schema:")]
    assert schema_warnings == []


def test_criteria_adapters_drop_none():
    criteria = {"business_type": "luxury_retail", "min_home_value": 400000,
                "center_lat": None, "target_homes": 5000}
    q = criteria_to_query_kwargs(criteria)
    s = criteria_to_score_criteria(criteria)
    assert q["min_home_value"] == 400000
    assert "center_lat" not in q
    assert "business_type" not in q  # not a query kwarg
    assert "min_home_value" in s
