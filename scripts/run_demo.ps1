# One script to set up and run the HEDIS POC demo page (gap prioritization + AI evidence).
#
# First time, or to reset all test data:
#   powershell -ExecutionPolicy Bypass -File scripts/run_demo.ps1 -Setup
# Every other time:
#   powershell -ExecutionPolicy Bypass -File scripts/run_demo.ps1
#
# -Setup installs packages, creates the tables, loads the test data, converts the test documents (a few
# minutes) and scores the gaps. Without -Setup it only starts the page. Then open http://localhost:8771.
# Needs a repo-root .env with DATABASE_URL and OPENROUTER_API_KEY (see the run guide). Key values are never printed.

param([switch]$Setup)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $RepoRoot ".env"
Set-Location $RepoRoot

function Invoke-Step([string]$Title, [string[]]$PyArgs) {
    Write-Host "`n== $Title" -ForegroundColor Cyan
    & python @PyArgs
    if ($LASTEXITCODE -ne 0) { throw "Step failed: $Title" }
}

if (-not (Test-Path $EnvFile)) {
    throw ".env not found in $RepoRoot. Create it first (run guide step 1), or run scripts/db/create_local_db.ps1."
}
$envText = Get-Content $EnvFile -Raw
if ($envText -notmatch "(?m)^\s*DATABASE_URL\s*=\s*\S") { throw ".env has no DATABASE_URL line (run guide step 1)." }
if ($envText -notmatch "(?m)^\s*OPENROUTER_API_KEY\s*=\s*\S") {
    Write-Host "Warning: .env has no OPENROUTER_API_KEY. The page will work, but 'Find evidence with AI' will fail." `
        -ForegroundColor Yellow
}

if ($Setup) {
    Invoke-Step "Install packages" @("-m", "pip", "install", "-q", "-r", "requirements.txt")
    Invoke-Step "Create the tables" @("-m", "alembic", "-c", "db/alembic.ini", "upgrade", "head")
    Invoke-Step "Load the test data" @("scripts/db/load_seed.py", "--reset")
    Invoke-Step "Convert the test documents (a few minutes)" @("-m", "services.document_intake.cli", "process-pending")
    Invoke-Step "Score and rank the gaps" @("-m", "services.gap_priority.cli", "score")
}

Write-Host "`n== Starting the demo page. Open http://localhost:8771 (Ctrl+C to stop)" -ForegroundColor Green
& python scripts/demo_priority/server.py
