#!/usr/bin/env python3
"""
Natural-language brief -> structured, validated targeting criteria.

parse_brief(text) returns {"criteria": {...}, "source": "llm"|"fallback", "warnings": [...]}.
The criteria dict is validated against schema/brief_criteria.schema.json and then feeds the
deterministic route engine (query_routes / score_routes / select_campaign).

Two backends:
  * LLM (optional): if OPENAI_API_KEY is set (and use_llm is True or None), an OpenAI-compatible
    chat completion is asked to emit criteria JSON. Configurable via OPENAI_BASE_URL / OPENAI_MODEL.
  * Deterministic fallback: keyword + regex extraction, so the parser ALWAYS returns usable criteria
    with no API key and no network.

Whatever the backend, the result is schema-validated; invalid/incomplete LLM output falls back safely.
"""
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from eddm_tools import BUSINESS_PLAYBOOK

SCHEMA_PATH = os.path.join(os.path.dirname(_HERE), "schema", "brief_criteria.schema.json")
BUSINESS_TYPES = list(BUSINESS_PLAYBOOK.keys())

# Keyword -> business_type. First match wins (longer/more-specific phrases first).
BUSINESS_KEYWORDS = [
    (r"\b(furniture|jewelry|jeweler|designer|art gallery|luxury|high[- ]end|upscale retail)\b", "luxury_retail"),
    (r"\b(fine dining|steakhouse|wine bar|upscale (?:seafood|restaurant)|bistro)\b", "restaurant_upscale"),
    (r"\b(pizza|burger|casual dining|fast[- ]casual|bakery|cafe|coffee|diner|taco|sandwich)\b", "restaurant_casual"),
    (r"\b(dentist|dental|orthodont|oral surge)\b", "dental_orthodontics"),
    (r"\b(hvac|plumb|roofing|roofer|electrician|electrical|painting|painter|handyman|landscap|remodel|contractor)\b", "home_services"),
    (r"\b(lawn|pest control|pool service|pressure wash|exterminat)\b", "lawn_pest_pool"),
    (r"\b(realtor|real estate|property manage|mortgage|lender)\b", "real_estate"),
    (r"\b(doctor|urgent care|chiropract|physical therapy|optometr|clinic|medical|healthcare|physician)\b", "medical_healthcare"),
    (r"\b(gym|yoga|pilates|crossfit|personal train|fitness|spa|wellness|massage)\b", "fitness_wellness"),
    (r"\b(auto repair|tire shop|car wash|detailing|oil change|mechanic|automotive)\b", "auto_services"),
    (r"\b(tutor|learning center|private school|music lesson|martial art|dance studio|education|childcare|daycare)\b", "education"),
    (r"\b(clothing|boutique|gift shop|pet store|retail|store|shop)\b", "general_retail"),
]

_ZIP_RE = re.compile(r"\b(\d{5})\b")
_RADIUS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[- ]?\s*mile", re.I)


def _money(match_text):
    """Convert '$1.5M' / '400k' / '120,000' -> int dollars."""
    s = match_text.lower().replace("$", "").replace(",", "").strip()
    mult = 1
    if s.endswith("m"):
        mult, s = 1_000_000, s[:-1]
    elif s.endswith("k"):
        mult, s = 1_000, s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return None


def _count(match_text):
    s = match_text.lower().replace(",", "").strip()
    mult = 1
    if s.endswith("k"):
        mult, s = 1_000, s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return None


def fallback_parse(text):
    """Deterministic keyword/regex extraction. Always returns a criteria dict + warnings."""
    t = text.lower()
    criteria = {}
    warnings = []

    # business_type
    for pattern, bt in BUSINESS_KEYWORDS:
        if re.search(pattern, t):
            criteria["business_type"] = bt
            break
    if "business_type" not in criteria:
        warnings.append("business_type not detected; defaulting to general_retail")
        criteria["business_type"] = "general_retail"

    # home value: a $ amount near home/house/property/value
    hv = re.search(r"\$?\s*(\d[\d,]*\.?\d*\s*[mk]?)\s*(?:\+|plus)?\s*(?:home|house|propert|value)", t)
    if not hv:
        hv = re.search(r"(?:home|house|propert)\w*\s*(?:value|worth|priced)?\s*(?:of|over|above|at least)?\s*\$?\s*(\d[\d,]*\.?\d*\s*[mk]?)", t)
    if hv:
        raw, tok = hv.group(0), hv.group(1)
        v = _money(tok)
        # Only treat as a home VALUE when there's a monetary signal, so "10000 homes"
        # (a mail-count) is not mistaken for a $ figure.
        if v and ("$" in raw or re.search(r"[mk]", tok, re.I) or v >= 100000):
            criteria["min_home_value"] = v

    # income: a $ amount near income/earn/household
    inc = re.search(r"\$?\s*(\d[\d,]*\.?\d*\s*[mk]?)\s*(?:\+|plus)?\s*(?:income|earners?|household income)", t)
    if not inc:
        inc = re.search(r"income\s*(?:of|over|above|at least)?\s*\$?\s*(\d[\d,]*\.?\d*\s*[mk]?)", t)
    if inc:
        v = _money(inc.group(1))
        if v:
            criteria["min_income"] = v

    # target homes: a plain count near homes/households/mailers/pieces (no $)
    th = re.search(r"(\d[\d,]*\s*k?)\s*(?:homes|households|mailers?|pieces|addresses)", t)
    if th and "$" not in th.group(0):
        c = _count(th.group(1))
        if c and c > 100:  # avoid catching "$400k homes" style money (handled above)
            criteria["target_homes"] = c

    # housing type / ownership
    if re.search(r"\b(homeowners?|owner[- ]occupied|single[- ]family|houses?)\b", t):
        criteria["housing_type"] = "homes"
        criteria.setdefault("min_own_pct", 60)
    elif re.search(r"\b(apartment|renter|multi[- ]family|condo)\b", t):
        criteria["housing_type"] = "apartments"

    # radius
    rad = _RADIUS_RE.search(t)
    if rad:
        criteria["max_radius"] = float(rad.group(1))

    # target age
    if re.search(r"\bfamil|\b(kids|children|parents)\b", t):
        criteria["target_age"] = "family"
    elif re.search(r"\b(senior|retire|55\+|elderly)\b", t):
        criteria["target_age"] = "senior"
    elif re.search(r"\b(young|millennial|students?)\b", t):
        criteria["target_age"] = "young_adult"

    # ZIP codes (avoid catching money like 33701 only if adjacent to 'zip')
    zips = _ZIP_RE.findall(text)
    if zips and re.search(r"\bzip", t):
        criteria["zip_codes"] = sorted(set(zips))

    if not warnings:
        warnings.append("parsed deterministically (no LLM)")
    return criteria, warnings


def _llm_parse(text):
    """Ask an OpenAI-compatible chat model for criteria JSON. Returns dict or None on any failure."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        return None
    import urllib.request
    import urllib.error

    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    schema_hint = {
        "business_type": "one of " + ", ".join(BUSINESS_TYPES),
        "center_lat": "float|null", "center_lon": "float|null", "max_radius": "float miles|null",
        "zip_codes": "list[str]|null", "neighborhoods": "list[str]|null",
        "min_income": "int|null", "max_income": "int|null",
        "min_home_value": "int|null", "max_home_value": "int|null", "min_own_pct": "int|null",
        "housing_type": "homes|apartments|any|null",
        "target_age": "family|senior|young_adult|any|null",
        "target_homes": "int|null", "min_score": "int|null",
    }
    sys_prompt = (
        "Extract EDDM direct-mail targeting criteria from the brief. "
        "Return ONLY a JSON object with these keys (use null when unknown, omit nothing):\n"
        + json.dumps(schema_hint, indent=2)
        + "\nDo not invent a location; only set center_lat/center_lon if explicit coordinates are given."
    )
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": text},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request(
        base + "/chat/completions", data=payload,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
        content = data["choices"][0]["message"]["content"]
        obj = json.loads(content)
        return {k: v for k, v in obj.items() if v is not None}
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
        return None


def _load_schema():
    try:
        return json.load(open(SCHEMA_PATH, encoding="utf-8"))
    except FileNotFoundError:
        return None


def _validate(criteria):
    """Validate against JSON Schema if jsonschema is available; return list of error strings."""
    schema = _load_schema()
    if not schema:
        return []
    try:
        import jsonschema
    except ImportError:
        return []
    errors = []
    v = jsonschema.Draft7Validator(schema)
    for e in v.iter_errors(criteria):
        errors.append(e.message)
    return errors


def _coerce_and_default(criteria):
    """Drop invalid business_type, coerce enums, ensure a usable minimum criteria set."""
    warnings = []
    bt = criteria.get("business_type")
    if bt and bt not in BUSINESS_TYPES:
        warnings.append(f"unknown business_type '{bt}'; defaulting to general_retail")
        criteria["business_type"] = "general_retail"
    criteria.setdefault("business_type", "general_retail")
    criteria.setdefault("target_homes", 10000)
    criteria.setdefault("min_score", 20)
    if criteria.get("housing_type") not in (None, "homes", "apartments", "any"):
        warnings.append(f"invalid housing_type '{criteria['housing_type']}' dropped")
        criteria.pop("housing_type")
    return criteria, warnings


def parse_brief(text, use_llm=None):
    """Parse a brief into validated criteria.

    use_llm: True forces LLM, False forces fallback, None = auto (LLM if OPENAI_API_KEY set).
    """
    source = "fallback"
    criteria = None
    warnings = []

    want_llm = use_llm if use_llm is not None else bool(os.environ.get("OPENAI_API_KEY"))
    if want_llm:
        llm = _llm_parse(text)
        if llm is not None:
            criteria, source = llm, "llm"
        else:
            warnings.append("LLM unavailable or returned invalid output; used deterministic fallback")

    if criteria is None:
        criteria, fw = fallback_parse(text)
        warnings.extend(fw)

    criteria, cw = _coerce_and_default(criteria)
    warnings.extend(cw)

    errs = _validate(criteria)
    if errs:
        warnings.extend("schema: " + e for e in errs)
        # On a hard schema failure from the LLM, retry deterministically.
        if source == "llm":
            criteria, fw = fallback_parse(text)
            criteria, cw = _coerce_and_default(criteria)
            warnings.extend(fw + cw)
            source = "fallback (llm output failed schema)"

    return {"criteria": criteria, "source": source, "warnings": warnings}


# ── adapters into the deterministic engine ──

_QUERY_KEYS = ["center_lat", "center_lon", "max_radius", "min_income", "max_income",
               "min_home_value", "max_home_value", "min_own_pct", "housing_type",
               "min_residential", "zip_codes", "neighborhoods"]
_SCORE_KEYS = ["center_lat", "center_lon", "max_radius", "min_home_value", "min_income",
               "housing_type", "target_age"]


def criteria_to_query_kwargs(criteria):
    return {k: criteria[k] for k in _QUERY_KEYS if criteria.get(k) is not None}


def criteria_to_score_criteria(criteria):
    return {k: criteria[k] for k in _SCORE_KEYS if criteria.get(k) is not None}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Parse an EDDM brief into structured criteria")
    ap.add_argument("--brief", required=True)
    ap.add_argument("--llm", choices=["auto", "on", "off"], default="auto")
    a = ap.parse_args()
    out = parse_brief(a.brief, use_llm=(None if a.llm == "auto" else a.llm == "on"))
    print(json.dumps(out, indent=2))
