# Sanitization Report — Tract

This repository is a sanitized public snapshot of an internal EDDM route-planning tool. This document
records what was removed, generalized, or replaced so the public version carries no commercially
sensitive or private data.

## Removed (commercial / pricing logic)
- **All quoting and pricing code.** `generate_quote()`, `_lookup_print_price()`, `_lookup_service_rate()`,
  the hardcoded fallback rate tables (`SIZE_ALIASES`, `FALLBACK_PRINT_COSTS`), the postage/mailing/banding
  constants, margin/markup and design-fee calculations, and the SQLite pricing-database dependency.
- The `quote` and `list-products` CLI subcommands and every argument that fed them.
- The pricing step inside the `campaign` command (route selection now ends at map + order sheet).

## Removed (brand / proposal / vendor)
- The branded "SPSL" map renderer (`generate_map_spsl`), its brand palette, badge asset path, and
  hardcoded proposal-specific neighborhood labels.
- Vendor-specific product/design-service identifiers.
- Internal pricing-data loader scripts (`load_additional_pricing.py`, `extract_xlsx_tabs.py`) and a
  scratch basemap test.

## Removed (private data / paths)
- Hardcoded Windows/user paths and the private pricing-database location. Any pricing database is now
  external and user-supplied; none is included.
- Client-tied default store coordinates in `campaign` were made explicit inputs rather than a baked-in
  business location.

## Added / generalized
- A model-agnostic natural-language **brief parser** (`brief_parser.py`) with a deterministic offline
  fallback and JSON-Schema validation.
- A gzip-aware geometry loader (route geometry ships compressed).
- Tests, an architecture diagram, and a synthetic example campaign.

## What remains (and why it is safe)
- **Route database for St. Petersburg, FL** — aggregate USPS carrier-route delivery counts joined to
  US Census ACS block-group statistics (median income, home value, ownership, household size). These are
  **public aggregate figures**; the data contains **no names, addresses, emails, phone numbers, or any
  recipient-level or personal information**.
- `build_route_db.py`, which documents how that data is assembled from public USPS GIS + Census APIs.

## Verification
Full-history secret scans (gitleaks, TruffleHog) are run against the recreated repository and expected to
return zero findings. Example data in `examples/` is synthetic (a fabricated campaign brief); the route
statistics it selects over are public aggregates.
