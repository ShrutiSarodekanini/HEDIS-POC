# E2E Test Suite

This folder contains end-to-end API workflow tests for the UI pages and backend profiles.

## Coverage scope

- Auth and admin workflow
- Dashboard and star summary workflow
- Prioritization and evidence workflow (including auth guards)
- Quality, calculation, ingestion, and flow workflow

## Run only e2e tests

```powershell
python -m pytest tests/e2e -q
```

## Run all tests in repository

```powershell
python -m pytest -q
```

## Optional: override base URL and credentials

```powershell
$env:E2E_BASE_URL = "http://127.0.0.1:8000"
$env:E2E_ADMIN_EMAIL = "admin@gmail.com"
$env:E2E_ADMIN_PASSWORD = "Admin@123"
$env:E2E_USER_EMAIL = "user@gmail.com"
$env:E2E_USER_PASSWORD = "User@123"
python -m pytest tests/e2e -q
```
