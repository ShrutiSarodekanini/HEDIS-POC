# HEDIS-POC Structure Alignment Plan

This repository has been aligned to the top-level structure used by:
https://github.com/ShrutiSarodekanini/HEDIS-POC

Important: The reference repository is currently a skeleton (empty service files). Your local project contains the working implementation, so alignment is done in a non-destructive way.

## Current Status

- Project now follows HEDIS-POC top-level layout (`common`, `scripts`, `services`, `tests`, `ui`).
- Working backend implementation has been migrated under `services/measure_calc/engine`.
- Working frontend implementation has been migrated under `ui/frontend`.
- Existing functionality is preserved; test execution remains available through backend scripts.

## Folder Mapping

- `services/measure_calc/engine/src` -> measure execution engine source
- `services/measure_calc/engine/test` -> backend unit and integration tests
- `services/measure_calc/engine/examples` -> measure and patient sample bundles
- `services/gap_identifier` -> gaps identification service entrypoint placeholder
- `services/valuesets` -> valueset service entrypoint placeholder
- `services/data_ingestion` -> ingestion service entrypoint placeholder
- `services/data_quality` -> quality service entrypoint placeholder
- `services/gap_priority` -> future prioritization service
- `services/star_modeler` -> future star/rating modeling service
- `ui/frontend` -> existing frontend application
- `tests` -> top-level compatibility test folder

## Execution Notes

1. Backend scripts: run from `services/measure_calc/engine`.
2. Frontend scripts: run from `ui/frontend`.
3. Tests: backend tests are in `services/measure_calc/engine/test`.
4. Service wrappers are present to preserve the HEDIS-POC structure for future service splits.

## Why this is safest

- Zero-risk alignment first (folder-level compatibility).
- No behavior changes to the execution engine.
- Team can adopt target structure immediately without losing existing functionality.
