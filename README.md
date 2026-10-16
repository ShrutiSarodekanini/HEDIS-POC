# HEDIS-POC (Local Aligned Workspace)

This workspace is aligned to the structure of:
https://github.com/ShrutiSarodekanini/HEDIS-POC

Top-level layout:
- common/
- scripts/
- services/
- tests/
- ui/

## Where the working implementation lives

- Backend engine: services/measure_calc/engine
- Frontend app: ui/frontend

## Quick commands (from workspace root)

```bash
npm run backend:install
npm run backend:test
npm run backend:build
npm run backend:cli -- detailed --measure-bundle <path> --patient-bundles <path1> <path2>
npm run ui:install
npm run ui:dev
```

## Notes

- The referenced GitHub repository is a skeleton structure.
- This local workspace keeps your full implementation while matching that structure for easier merge and collaboration.
