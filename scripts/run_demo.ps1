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
#
# End-to-end integration app (Synthea upload, ingestion, measure engine, work list, documents, AI evidence, review):
#   powershell -ExecutionPolicy Bypass -File scripts/run_demo.ps1 -Integration -Setup    (first time)
#   powershell -ExecutionPolicy Bypass -File scripts/run_demo.ps1 -Integration
# It uses its own schema (-Schema, default integ) in the .env database, so the demo data in public is untouched,
# and listens on http://localhost:8780 (INTEGRATION_PORT changes it). -Setup there creates the schema and tables,
# loads reference data only (no patients) and builds the measure engine (needs Node 20). -EvidenceDir <folder> sets
# INTEGRATION_EVIDENCE_DIR for the app: after a batch is prioritized, the folder's documents (listed in its
# manifest.json) are attached to their patients. -UiDir <folder> picks the screen set the app serves (for example
# services/integration/ui/redesign); without it the original page in services/integration/static is served.

param([switch]$Setup, [switch]$Integration, [string]$Schema = "integ", [string]$EvidenceDir = "", [string]$UiDir = "")

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
    Write-Host "Warning: .env has no OPENROUTER_API_KEY. The page will work, but the AI evidence review will fail." `
        -ForegroundColor Yellow
}

# A DATABASE_URL already set in Windows (for example by another project) wins over .env, so drop it for this run.
if ($env:DATABASE_URL) {
    Write-Host "Note: ignoring the DATABASE_URL set in Windows for this run; using the one in .env." -ForegroundColor Yellow
    Remove-Item Env:DATABASE_URL
}

if ($Integration) {
    if ($Schema -notmatch '^[a-z_][a-z0-9_]*$') { throw "-Schema must be a plain lower-case name." }
    if ($Schema -eq "public") { throw "Use a separate schema for the integration app; public holds the demo data." }
    # Same database as .env, with this schema first on the search path. The URL is never printed.
    $env:INTEGRATION_SCHEMA = $Schema
    $env:DATABASE_URL = & python -c ("import os, dotenv; u = dotenv.dotenv_values('.env')['DATABASE_URL']; " +
        "print(u + ('&' if '?' in u else '?') + 'options=-csearch_path%3D' + os.environ['INTEGRATION_SCHEMA'])")
    if ($LASTEXITCODE -ne 0 -or -not $env:DATABASE_URL) { throw "Could not read DATABASE_URL from .env." }
    $Plain = "import os, psycopg; c = psycopg.connect(os.environ['DATABASE_URL'].replace('postgresql+psycopg://', " +
        "'postgresql://', 1), autocommit=True); "

    if ($Setup) {
        Invoke-Step "Install packages" @("-m", "pip", "install", "-q", "-r", "requirements.txt")
        Invoke-Step "Create schema $Schema" @("-c", ($Plain + "from psycopg import sql; " +
            "c.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(os.environ['INTEGRATION_SCHEMA'])))"))
        Invoke-Step "Create the tables in $Schema" @("-m", "alembic", "-c", "db/alembic.ini", "upgrade", "head")
        $orgs = & python -c ($Plain + "print(c.execute('SELECT count(*) FROM organization').fetchone()[0])")
        if ($LASTEXITCODE -ne 0) { throw "Could not count organizations in $Schema." }
        if ([int]$orgs -eq 0) {
            Invoke-Step "Load reference data (no patients)" @("scripts/db/load_seed.py", "--reference-only")
        } else {
            Write-Host "`n== Reference data already loaded in $Schema" -ForegroundColor Cyan
        }
        # Run inside the engine folder: "npm --prefix <dir> install" from the root also adds the root as a dependency.
        Write-Host "`n== Build the measure engine" -ForegroundColor Cyan
        Push-Location services/measure_calc/engine
        try {
            & npm install --no-fund --no-audit; if ($LASTEXITCODE -ne 0) { throw "Step failed: engine install" }
            & npm run build; if ($LASTEXITCODE -ne 0) { throw "Step failed: engine build" }
        } finally { Pop-Location }
    }

    $actual = & python -c ($Plain + "print(c.execute('SELECT current_schema()').fetchone()[0])")
    if ($actual -ne $Schema) { throw "Schema $Schema not found. Run with -Integration -Setup first." }
    if (-not (Test-Path services/measure_calc/engine/build/cli.js)) {
        throw "The measure engine is not built. Run with -Integration -Setup first."
    }
    if ($EvidenceDir) {
        if (-not (Test-Path (Join-Path $EvidenceDir "manifest.json"))) { throw "No manifest.json in $EvidenceDir." }
        $env:INTEGRATION_EVIDENCE_DIR = (Resolve-Path $EvidenceDir).Path
        Write-Host "Stored evidence: $env:INTEGRATION_EVIDENCE_DIR" -ForegroundColor Cyan
    }
    if ($UiDir) {
        if (-not (Test-Path (Join-Path $UiDir "index.html"))) { throw "No index.html in $UiDir." }
        $env:INTEGRATION_UI_DIR = (Resolve-Path $UiDir).Path
        Write-Host "Screens: $env:INTEGRATION_UI_DIR" -ForegroundColor Cyan
    }
    if ($envText -notmatch "(?m)^\s*EXTRACTION_MODEL\s*=\s*\S" -and -not $env:EXTRACTION_MODEL) {
        Write-Host ("Warning: EXTRACTION_MODEL is not set in .env, so the code default model is used. " +
            "The tested model is minimax/minimax-m3.1-flash:free.") -ForegroundColor Yellow
    }
    $port = if ($env:INTEGRATION_PORT) { $env:INTEGRATION_PORT } else { "8780" }
    Write-Host "`n== Starting the integration app on schema $Schema. Open http://localhost:$port (Ctrl+C to stop)" `
        -ForegroundColor Green
    & python -m services.integration.app
    exit $LASTEXITCODE
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
