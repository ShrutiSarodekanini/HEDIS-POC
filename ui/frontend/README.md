# HEDIS Measure Evaluation Platform Frontend

Production-oriented React frontend for interacting with the FQM execution workflow through a typed API boundary.

Important scope note:
- This frontend does not certify or validate official HEDIS compliance.

## Prerequisites

- Node.js 20+
- npm 10+

## Installation

From repository root:

```bash
npm run frontend:install
```

Or from this folder:

```bash
npm install
```

## Environment Configuration

Copy `.env.example` to `.env` and adjust values:

```bash
VITE_API_MODE=mock
VITE_API_BASE_URL=http://localhost:8000
VITE_APP_VERSION=0.1.0
```

Variables:
- `VITE_API_MODE`
  - `mock`: explicit demo adapter for development when backend APIs are not available.
  - `real`: call backend APIs using `VITE_API_BASE_URL`.
- `VITE_API_BASE_URL`: base URL for FastAPI backend.
- `VITE_APP_VERSION`: optional app version label.

## Development Server

From repository root:

```bash
npm run frontend:dev
```

From `frontend/`:

```bash
npm run dev
```

## Production Build

```bash
npm run build
```

## Lint, Type Check, and Tests

```bash
npm run lint
npm run typecheck
npm run test
npm run test:coverage
```

## API Contract

Expected backend endpoint contract is documented in:
- `docs/API_CONTRACT.md`

This frontend implements a real adapter for that contract and an explicit mock adapter for development.

## Mock Mode vs Real API Mode

- Mock mode:
  - Uses deterministic demonstration data.
  - Shows an in-app banner indicating demonstration mode.
  - Never claims data is production output.
- Real mode:
  - Calls the configured backend directly.
  - Does not silently fall back to mock mode on API failure.

## Connecting to FastAPI Later

1. Implement the endpoints in `docs/API_CONTRACT.md` in the FastAPI backend.
2. Enable CORS for the frontend origin.
3. Set `VITE_API_MODE=real`.
4. Set `VITE_API_BASE_URL` to the backend host.
5. Validate upload and asynchronous evaluation behavior with real bundles.

## Security Notes

- Do not place credentials, tokens, or API secrets in frontend environment variables.
- Treat patient-level identifiers carefully and minimize displayed PHI.
