# Frontend API Contract (Expected FastAPI Boundary)

This document defines the frontend's expected contract with the Python FastAPI application backend.

Status notes:
- These endpoints are expected by the React frontend.
- They are not claimed to exist until implemented and verified in the backend.
- The frontend can run in explicit mock mode when these endpoints are unavailable.

## Conventions

- Base URL: `VITE_API_BASE_URL`
- Content type: `application/json` unless noted
- Pagination fields:
  - `page` (1-based)
  - `page_size`
- Paginated response shape:

```json
{
  "items": [],
  "page": 1,
  "pageSize": 10,
  "total": 0
}
```

## 1. Health Check

- Method: `GET`
- Path: `/health`

Expected response:

```json
{
  "status": "ok",
  "message": "Service reachable",
  "version": "1.0.0",
  "checkedAt": "2026-10-15T12:00:00.000Z"
}
```

## 2. List Measures

- Method: `GET`
- Path: `/measures`
- Query params:
  - `search` (optional)
  - `status` (optional: `ready|retired|unknown|unavailable`)
  - `page`
  - `page_size`

Expected item fields:

```json
{
  "id": "AAB",
  "name": null,
  "description": null,
  "measurementPeriod": null,
  "executionStatus": "ready",
  "metadataAvailable": false
}
```

## 3. Get Measure Details

- Method: `GET`
- Path: `/measures/{measure_id}`

Expected response fields:

```json
{
  "id": "AAB",
  "name": null,
  "description": null,
  "measurementPeriod": null,
  "executionStatus": "ready",
  "metadataAvailable": false,
  "lastUpdated": null,
  "source": null
}
```

## 4. Submit Evaluation

- Method: `POST`
- Path: `/evaluations`
- Content type: `multipart/form-data`
- Fields:
  - `measure_id` (string, required)
  - `measurement_period_start` (string date, optional)
  - `measurement_period_end` (string date, optional)
  - `measure_bundle` (file, required)
  - `patient_bundles` (file[], required)

Expected response:

```json
{
  "evaluationId": "run-123",
  "status": "pending",
  "submittedAt": "2026-10-15T12:03:00.000Z"
}
```

## 5. Get Evaluation Status/Result

- Method: `GET`
- Path: `/evaluations/{evaluation_id}`

Expected response (example shape):

```json
{
  "evaluationId": "run-123",
  "status": "completed",
  "measureId": "AAB",
  "measurementPeriodStart": "2026-01-01",
  "measurementPeriodEnd": "2026-12-31",
  "submittedAt": "2026-10-15T12:03:00.000Z",
  "completedAt": "2026-10-15T12:03:05.000Z",
  "populationCounts": {
    "initialPopulation": 10,
    "denominator": 9,
    "numerator": 7
  },
  "unresolvedReasons": [],
  "warnings": [],
  "errors": [],
  "patientResults": [],
  "resultPayload": {}
}
```

## 6. List Evaluation History

- Method: `GET`
- Path: `/evaluations`
- Query params:
  - `search` (optional)
  - `status` (optional)
  - `page`
  - `page_size`

Expected item fields:

```json
{
  "evaluationId": "run-123",
  "measureId": "AAB",
  "status": "completed",
  "submittedAt": "2026-10-15T12:03:00.000Z",
  "completedAt": "2026-10-15T12:03:05.000Z"
}
```

## 7. List Patient-Level Results

- Method: `GET`
- Path: `/patients/results`
- Query params:
  - `search` (optional)
  - `evaluation_id` (optional)
  - `page`
  - `page_size`

Expected item fields:

```json
{
  "patientId": "patient-001",
  "status": "completed",
  "populations": {
    "initialPopulation": true,
    "denominator": true,
    "numerator": false
  },
  "warnings": []
}
```

## Notes on Asynchronous Execution

- Frontend assumes submission is asynchronous.
- Frontend polls `GET /evaluations/{evaluation_id}` while status is `pending` or `running`.
- Frontend treats `unresolved` as distinct from `failed` and `completed`.

## Compliance and Clinical Interpretation Note

This UI surfaces computational outputs and operational status only. It does not certify official HEDIS compliance or substitute for clinical governance review.
