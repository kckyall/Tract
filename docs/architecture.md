# Tract — Architecture

Tract turns a plain-language campaign brief into a selected set of USPS carrier routes plus the
paperwork to mail them. The AI/parsing layer only produces **structured criteria**; all route
selection is deterministic and reproducible.

```mermaid
flowchart TD
    A["Campaign brief (natural language)"] --> B{brief_parser}
    B -->|OPENAI_API_KEY set| C["LLM extract → JSON"]
    B -->|no key / offline| D["Deterministic keyword + regex extract"]
    C --> E["JSON Schema validation<br/>(schema/brief_criteria.schema.json)"]
    D --> E
    E -->|invalid LLM output| D
    E --> F["Structured criteria<br/>business_type, geo, demographics, target_homes"]

    F --> G["query_routes()<br/>filter by ZIP / radius / income /<br/>home value / ownership / housing"]
    G --> H["score_routes()<br/>business-type playbook weights"]
    H --> I["select_campaign()<br/>greedy to target_homes"]
    I --> J{"target met?"}
    J -->|yes| K["Selected routes"]
    J -->|no| L["Shortfall + explanation"]
    K --> M["generate_map() → PNG"]
    K --> N["generate_order_sheet() → USPS EDDM sheet"]

    subgraph DATA["Precomputed data (public sources)"]
      RC["route_db_compact.json<br/>USPS route demographics"]
      RG["route_db_geometry.json.gz<br/>route boundaries"]
      NB["neighborhoods.json"]
    end
    DATA -.-> G
    DATA -.-> M

    subgraph BUILD["build_route_db.py (offline, quarterly)"]
      U["USPS GIS carrier routes"] --> RB["merge + geocode"]
      CE["US Census ACS block groups"] --> RB
      RB --> DATA
    end
```

## Layers

| Layer | File | Role |
|---|---|---|
| Brief interpretation | `scripts/brief_parser.py` | NL → validated criteria (LLM optional, deterministic fallback) |
| Criteria schema | `schema/brief_criteria.schema.json` | Draft-07 schema the criteria must satisfy |
| Route engine | `scripts/eddm_tools.py` | `query_routes` → `score_routes` → `select_campaign`, map + order sheet |
| CLI | `scripts/eddm_cli.py` | `parse`, `campaign`, and each step as a subcommand |
| Data builder | `scripts/build_route_db.py` | Rebuilds the route DB from USPS GIS + Census ACS |

## Design notes
- The LLM only extracts criteria; it never selects routes. Selection is deterministic, so the same
  criteria always yield the same routes.
- The parser always returns usable criteria (schema-validated) even with no API key and no network.
- Explicit CLI flags override anything parsed from the brief.
- No pricing, margin, vendor, or proposal logic is present — this repo selects routes and produces
  mailing paperwork only.
