# Tract

**Turn a plain-language brief into a USPS Every Door Direct Mail (EDDM) campaign.** Tract interprets a
campaign brief into structured targeting criteria, selects the carrier routes that best match the
audience (by income, home value, ownership, household type, distance, and a business-type playbook),
hits a target home count, and produces a **coverage map** and a **USPS-ready order sheet**.

It ships with a precomputed route database for **St. Petersburg, FL** (16 ZIP codes, ~318 active
residential carrier routes, ~186k deliveries) built from public USPS + Census data, so you can clone
and run the full workflow immediately.

> Built by an operator using AI-assisted development. Shared as a portfolio project.
> **No customer, recipient, or pricing data is included** — only public aggregate route + census statistics.

---

## What it does

```
brief → parse to criteria → query routes → score → select to target → map + USPS order sheet
```

- **Interpret** a natural-language brief into validated criteria (business type, location/radius, ZIPs,
  neighborhoods, income, home value, ownership, housing type, age, target homes).
- **Select** the carrier routes that match, using a per-business-type scoring playbook, and either hit
  the target home count or explain the shortfall.
- **Map** the selected routes and generate a **USPS EDDM order sheet** ready for the USPS portal.

See [`docs/architecture.md`](docs/architecture.md) for the full diagram.

## Install

```bash
pip install -r requirements.txt
```

Requires Python 3.9+. `matplotlib` is used for maps; `jsonschema` validates parsed criteria.

## Quick start

```bash
# 1. See how a brief becomes structured criteria (deterministic, no API key needed)
python scripts/eddm_cli.py parse --brief "upscale dental practice, homeowners, $450k+ homes, mail 9000 homes"

# 2. Run the whole pipeline: brief → routes → map → USPS order sheet
python scripts/eddm_cli.py campaign \
  --brief "upscale dental practice near downtown, homeowners in $450k+ homes, families, mail 9000 homes" \
  --store-lat 27.771 --store-lon -82.639

# Or drive each step by hand:
python scripts/eddm_cli.py query --center-lat 27.806 --center-lon -82.638 --max-radius 8 --min-home-value 400000
python scripts/eddm_cli.py score  <routes_file>   --business-type luxury_retail --center-lat 27.806 --center-lon -82.638
python scripts/eddm_cli.py select <scored_file>   --target-homes 9000
python scripts/eddm_cli.py map    <selection_file> --store-lat 27.806 --store-lon -82.638 --radius 8
python scripts/eddm_cli.py order-sheet <selection_file> --save
python scripts/eddm_cli.py stats
```

Every command prints JSON to stdout; intermediate files and artifacts are written to `output/`.

### Optional LLM parsing
By default the brief parser runs a deterministic keyword/regex extractor (no network). If you set
`OPENAI_API_KEY`, it will use an OpenAI-compatible chat model to extract criteria instead, then validate
the result against the schema and fall back automatically if the model output is invalid.

```bash
export OPENAI_API_KEY=...            # optional
export OPENAI_BASE_URL=...           # optional, defaults to https://api.openai.com/v1
export OPENAI_MODEL=gpt-4o-mini      # optional
```

## Data

Everything needed to run route selection is bundled and derived from **public** sources:

- `scripts/route_db_compact.json` — per-route demographics + delivery counts (USPS + Census ACS)
- `scripts/route_db_geometry.json.gz` — route boundary geometry (for maps), gzipped, loaded transparently
- `scripts/neighborhoods.json` — named neighborhoods → route groupings
- `scripts/build_route_db.py` — rebuilds the DB from USPS GIS carrier-route data + Census ACS block groups

To target a different metro, edit the ZIP list in `build_route_db.py` and re-run it.

**No personal, customer, or recipient-level data is included** — only aggregate route and census
statistics. See [`SANITIZATION.md`](SANITIZATION.md) for exactly what was removed to make this repo public.

## Examples

[`examples/`](examples/) contains a full synthetic run: a brief, the parsed criteria, the campaign
result JSON, a generated map (`example_map.png`), and a USPS order sheet.

## Tests

```bash
pip install pytest
pytest
```

Covers brief parsing (deterministic + mocked LLM), schema validation, scoring, selection to
target/shortfall, and map + order-sheet generation — all on the bundled data.

## License

MIT — see [LICENSE](LICENSE).
