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
# End-to-end app (Synthea upload, ingestion, data quality, measure engine, stars, work list, documents, AI evidence):
#   powershell -ExecutionPolicy Bypass -File scripts/run_demo.ps1 -Integration -Setup    (first time)
#   powershell -ExecutionPolicy Bypass -File scripts/run_demo.ps1 -Integration
# Then open http://localhost:8780 (INTEGRATION_PORT changes it). The app uses one schema: the first one on the search
# path in .env's DATABASE_URL (?options=-csearch_path%3Dhedis, see .env.example). It refuses public. -Schema <name>
# overrides that search path for one run. -Setup installs packages, creates the schema if missing, upgrades the tables
# to the latest migration, loads reference data only (no patients) when the organization table is empty, and builds
# the measure engine (needs Node 20).
# -EvidenceDir (default data/evidence_store): after a batch is prioritized, the folder's documents (listed in its
# manifest.json) are attached to their patients. -UiDir (default services/integration/ui/redesign) picks the screens.
# -AllowReset switches on the page's "Start over" button, which empties every data table and keeps reference data.
# EXTRACTION_MODEL and EXTRACTION_MAX_OUTPUT_TOKENS come from .env; the token limit defaults to 32768.
# -Observability (with -Integration): the app's metrics are at http://localhost:<port>/metrics; this also starts
# Prometheus on http://localhost:9090 in Docker when it is running, else PROMETHEUS_EXE or prometheus.exe on PATH.
# In Docker it also starts Grafana on http://localhost:3000 (dashboard HEDIS pipeline); Grafana needs Docker.
# Nothing is downloaded. Stop both Docker ones with: docker compose -f observability/docker-compose.yml down

param([switch]$Setup, [switch]$Integration, [string]$Schema = "", [string]$EvidenceDir = "data/evidence_store",
      [string]$UiDir = "services/integration/ui/redesign", [switch]$AllowReset, [switch]$Observability)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $RepoRoot ".env"
Set-Location $RepoRoot

function Invoke-Step([string]$Title, [string[]]$PyArgs) {
    Write-Host "`n== $Title" -ForegroundColor Cyan
    & python @PyArgs
    if ($LASTEXITCODE -ne 0) { throw "Step failed: $Title" }
}

# Prometheus for the integration app: Docker when it runs, else a local prometheus.exe, else a note. Never blocks the app.
function Start-Observability([string]$Port) {
    $dir = Join-Path $RepoRoot ".observability"
    New-Item -ItemType Directory -Force $dir | Out-Null
    $useDocker = $false
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        # Windows PowerShell turns a native command's stderr into an error under "Stop", so relax it for this check.
        $previous = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        try { & docker info *> $null; $useDocker = $LASTEXITCODE -eq 0 } finally { $ErrorActionPreference = $previous }
    }
    $exe = if ($env:PROMETHEUS_EXE -and (Test-Path $env:PROMETHEUS_EXE)) { (Resolve-Path $env:PROMETHEUS_EXE).Path }
           else { (Get-Command prometheus.exe -ErrorAction SilentlyContinue).Source }
    $target = if ($useDocker) { "host.docker.internal:$Port" } else { "localhost:$Port" }
    $config = Join-Path $dir "prometheus.yml"
    $template = Get-Content (Join-Path $RepoRoot "observability/prometheus.yml.template") -Raw
    [System.IO.File]::WriteAllText($config, $template.Replace("__TARGET__", $target))   # UTF-8 without a BOM
    Write-Host "`n== Metrics: http://localhost:$Port/metrics (Prometheus target $target)" -ForegroundColor Cyan
    if ($useDocker) {
        # docker compose does not read the root .env, so pass the Grafana passwords on for this process only.
        foreach ($key in "GRAFANA_ADMIN_PASSWORD", "GRAFANA_DB_PASSWORD") {
            if (-not [Environment]::GetEnvironmentVariable($key) -and $envText -match "(?m)^\s*$key\s*=\s*(\S.*?)\s*$") {
                [Environment]::SetEnvironmentVariable($key, $Matches[1].Trim('"', "'"), "Process")
            }
        }
        if (-not $env:GRAFANA_DB_PASSWORD) {
            Write-Host "Run history dashboard: set GRAFANA_DB_PASSWORD in .env (see observability/grafana/readonly_role.sql)" `
                -ForegroundColor Yellow
        }
        & docker compose -f observability/docker-compose.yml up -d
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Warning: Prometheus did not start in Docker; the app starts anyway." -ForegroundColor Yellow
        } else {
            Write-Host "Prometheus: http://localhost:9090" -ForegroundColor Cyan
            Write-Host ("Grafana: http://localhost:3000 (dashboards HEDIS pipeline and HEDIS run history; admin login " +
                "only to edit)") -ForegroundColor Cyan
            Write-Host "Stop both: docker compose -f observability/docker-compose.yml down" -ForegroundColor Cyan
        }
    } elseif ($exe) {
        Copy-Item (Join-Path $RepoRoot "observability/alerts.yml") (Join-Path $dir "alerts.yml") -Force
        $data = Join-Path $dir "data"
        $proc = Start-Process -FilePath $exe -WindowStyle Hidden -PassThru -ArgumentList @(
            "--config.file=`"$config`"", "--storage.tsdb.path=`"$data`"", "--storage.tsdb.retention.time=15d")
        Write-Host "Prometheus: http://localhost:9090 (stop: Stop-Process -Id $($proc.Id))" -ForegroundColor Cyan
        Write-Host "Grafana is not started: it needs Docker (Rancher Desktop or Docker Desktop)." -ForegroundColor Yellow
    } else {
        Write-Host ("Prometheus not started: start Rancher Desktop or Docker Desktop, or install Prometheus and set " +
            "PROMETHEUS_EXE. The app starts anyway.") -ForegroundColor Yellow
    }
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
    if ($Schema) {
        if ($Schema -notmatch '^[a-z_][a-z0-9_]*$') { throw "-Schema must be a plain lower-case name." }
        # Override for this run: .env's URL with this schema on the search path instead. The URL is never printed.
        $env:INTEGRATION_SCHEMA = $Schema
        $env:DATABASE_URL = & python -c ("import os, re, dotenv; u = dotenv.dotenv_values('.env')['DATABASE_URL']; " +
            "s = 'search_path%3D' + os.environ['INTEGRATION_SCHEMA']; " +
            "print(re.sub('search_path%3D[A-Za-z0-9_]+', s, u) if 'search_path%3D' in u " +
            "else u + ('&' if '?' in u else '?') + 'options=-c' + s)")
        if ($LASTEXITCODE -ne 0 -or -not $env:DATABASE_URL) { throw "Could not read DATABASE_URL from .env." }
    }
    # The app's database: the -Schema URL above when set, otherwise DATABASE_URL from .env as it is.
    $Plain = "import os, dotenv, psycopg; u = os.environ.get('DATABASE_URL') or " +
        "dotenv.dotenv_values('.env')['DATABASE_URL']; " +
        "c = psycopg.connect(u.replace('postgresql+psycopg://', 'postgresql://', 1), autocommit=True); "
    $target = & python -c ($Plain + "print(c.execute('SHOW search_path').fetchone()[0].split(',')[0].strip().strip(chr(34)))")
    if ($LASTEXITCODE -ne 0) { throw "Could not connect to the database in .env (DATABASE_URL)." }
    if ($target -notmatch '^[a-z_][a-z0-9_]*$' -or $target -eq 'public') {
        throw ('DATABASE_URL in .env does not name the app schema (it would use public). Add ' +
            '?options=-csearch_path%3Dhedis to it, as in .env.example.')
    }
    $env:INTEGRATION_SCHEMA = $target

    if ($Setup) {
        Invoke-Step "Install packages" @("-m", "pip", "install", "-q", "-r", "requirements.txt")
        Invoke-Step "Create schema $target if missing" @("-c", ($Plain + "from psycopg import sql; " +
            "c.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(os.environ['INTEGRATION_SCHEMA'])))"))
        Invoke-Step "Create or upgrade the tables (migrations to head)" @("-m", "alembic", "-c", "db/alembic.ini",
            "upgrade", "head")
        $orgs = & python -c ($Plain + "print(c.execute('SELECT count(*) FROM organization').fetchone()[0])")
        if ($LASTEXITCODE -ne 0) { throw "Could not count organizations." }
        if ([int]$orgs -eq 0) {
            Invoke-Step "Load reference data (no patients)" @("scripts/db/load_seed.py", "--reference-only")
        } else {
            Write-Host "`n== Reference data already loaded" -ForegroundColor Cyan
        }
        # Run inside the engine folder: "npm --prefix <dir> install" from the root also adds the root as a dependency.
        Write-Host "`n== Build the measure engine" -ForegroundColor Cyan
        Push-Location services/measure_calc/engine
        try {
            & npm install --no-fund --no-audit; if ($LASTEXITCODE -ne 0) { throw "Step failed: engine install" }
            & npm run build; if ($LASTEXITCODE -ne 0) { throw "Step failed: engine build" }
        } finally { Pop-Location }
    }

    $schemaName = & python -c ($Plain + "print(c.execute('SELECT current_schema()').fetchone()[0])")
    if ($LASTEXITCODE -ne 0) { throw "Could not connect to the database in .env (DATABASE_URL)." }
    if ($schemaName -ne $target) { throw "Schema $target not found. Run with -Integration -Setup first." }
    $migration = & python -c ($Plain + "from alembic.config import Config; from alembic.script import ScriptDirectory; " +
        "head = ScriptDirectory.from_config(Config('db/alembic.ini')).get_current_head(); " +
        "found = c.execute('SELECT to_regclass(%s)', ('alembic_version',)).fetchone()[0]; " +
        "now = found and c.execute('SELECT version_num FROM alembic_version').fetchone()[0]; " +
        "print('ok' if now == head else 'it is at ' + str(now or 'no migration') + ', the latest is ' + head)")
    if ($LASTEXITCODE -ne 0) { throw "Could not read the migration state of the database." }
    if ($migration -ne "ok") {
        throw "The tables in schema $schemaName are not at the latest migration ($migration). Run with -Integration -Setup."
    }
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
    if ($AllowReset) {
        $env:INTEGRATION_ALLOW_RESET = "1"
        Write-Host ("Start over is on: the page can empty every data table in schema $schemaName " +
            "(reference data stays).") -ForegroundColor Yellow
    }
    if ($envText -notmatch "(?m)^\s*EXTRACTION_MODEL\s*=\s*\S" -and -not $env:EXTRACTION_MODEL) {
        Write-Host ("Warning: EXTRACTION_MODEL is not set in .env, so the code default model is used. " +
            "The last tested model is recursive/flint-v1.0:free with EXTRACTION_MAX_OUTPUT_TOKENS=32768.") `
            -ForegroundColor Yellow
    }
    if ($envText -notmatch "(?m)^\s*EXTRACTION_MAX_OUTPUT_TOKENS\s*=\s*\S" -and -not $env:EXTRACTION_MAX_OUTPUT_TOKENS) {
        $env:EXTRACTION_MAX_OUTPUT_TOKENS = "32768"
    }
    $port = if ($env:INTEGRATION_PORT) { $env:INTEGRATION_PORT } else { "8780" }
    if ($Observability) { Start-Observability $port }
    Write-Host "`n== Starting the app on schema $schemaName. Open http://localhost:$port (Ctrl+C to stop)" `
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
