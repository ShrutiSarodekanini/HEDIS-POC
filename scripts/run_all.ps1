# Start the whole project end to end: RabbitMQ, the observability stack, the backend on 8000, the outbox dispatcher,
# the ten pipeline workers and the React UI. Each long-running part opens in its own window so its log stays visible.
#
#   powershell -ExecutionPolicy Bypass -File scripts/run_all.ps1            start everything
#   powershell -ExecutionPolicy Bypass -File scripts/run_all.ps1 -Setup     first time on an empty schema: also load
#                                                                           the reference data (measures, cut points...)
#   powershell -ExecutionPolicy Bypass -File scripts/run_all.ps1 -NoObservability   skip Prometheus, Grafana, Loki
#   powershell -ExecutionPolicy Bypass -File scripts/run_all.ps1 -Install   reinstall Python and npm packages and
#                                                                           rebuild the measure engine (after a pull)
#   powershell -ExecutionPolicy Bypass -File scripts/run_all.ps1 -Stop      stop everything this script started
#
# Needs: Rancher Desktop (or Docker Desktop) running, the local Postgres from .env's DATABASE_URL, Python 3.11+,
# Node 20+, and a repo-root .env (copy .env.example). On a fresh clone the Python packages, the measure engine build
# and the UI packages are installed automatically when missing. Migrations always run; they are a no-op when up to
# date. For AI evidence, .env also needs INTEGRATION_EVIDENCE_DIR (data/evidence_store) and an EXTRACTION_MODEL that
# is serving on OpenRouter. Key values are never printed.

param([switch]$Setup, [switch]$NoObservability, [switch]$Install, [switch]$Stop)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$Venv = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Python = if (Test-Path $Venv) { $Venv } else { "python" }
$Shell = (Get-Process -Id $PID).Path
$EnvText = if (Test-Path ".env") { Get-Content ".env" -Raw } else { "" }

function Test-Docker {
    # Windows PowerShell turns a native command's stderr into an error under "Stop", so relax it for this check.
    $previous = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & docker info *> $null; return $LASTEXITCODE -eq 0 } finally { $ErrorActionPreference = $previous }
}

function Start-Window([string]$Title, [string]$Command) {
    Write-Host "Starting: $Title" -ForegroundColor Green
    $full = "`$Host.UI.RawUI.WindowTitle = 'HEDIS $Title'; Set-Location '$RepoRoot'; $Command"
    Start-Process $Shell -ArgumentList @("-NoExit", "-Command", $full) | Out-Null
}

function Invoke-Checked([string]$Title, [scriptblock]$Command) {
    Write-Host "  $Title" -ForegroundColor DarkGray
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Failed: $Title" }
}

function Test-PythonPackages {
    $previous = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & $Python -c "import fastapi, uvicorn, pika, psycopg, dotenv, alembic" *> $null; return $LASTEXITCODE -eq 0 }
    finally { $ErrorActionPreference = $previous }
}

# Containers keep fixed names, so ones created from another clone of this repo block compose here. Removing a
# container keeps its volume data.
function Remove-OtherCloneContainers {
    foreach ($name in "hedis-rabbitmq", "hedis-prometheus", "hedis-grafana", "hedis-loki", "hedis-alloy") {
        $previous = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        # The \" escapes are for Windows PowerShell 5.1, which drops bare inner quotes when calling native programs.
        $dir = & docker inspect -f '{{ index .Config.Labels \"com.docker.compose.project.working_dir\" }}' $name 2>$null
        $ErrorActionPreference = $previous
        if ($LASTEXITCODE -ne 0 -or -not $dir) { continue }
        # Exact match: D:\HEDISPOC is a prefix of D:\HEDISPOC_single, so a "starts with" test would misfire.
        if ($dir -notin @($RepoRoot, (Join-Path $RepoRoot "observability"))) {
            Write-Host "  Removing container $name created from $dir" -ForegroundColor DarkGray
            & docker rm -f $name | Out-Null
        }
    }
}

function Wait-Until([string]$What, [scriptblock]$Check, [int]$Seconds = 180) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (& $Check) { Write-Host "  $What is ready" -ForegroundColor DarkGray; return }
        Start-Sleep -Seconds 2
    }
    throw "$What did not become ready within $Seconds seconds."
}

if ($Stop) {
    Write-Host "== Stopping HEDIS processes" -ForegroundColor Cyan
    $pattern = "uvicorn app:app|services\.outbox_dispatcher|services\.workers\.|run_workers\.py"
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match $pattern } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
        Where-Object { $_.CommandLine -match "vite" -and $_.CommandLine -like "*$RepoRoot*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    if (Test-Docker) {
        & docker compose -f observability/docker-compose.yml stop
        & docker compose stop rabbitmq
    }
    Write-Host "Stopped. The database and the containers' data are kept." -ForegroundColor Green
    return
}

Write-Host "== Checking prerequisites" -ForegroundColor Cyan
if (-not (Test-Path ".env")) { throw "No .env in $RepoRoot. Copy .env.example to .env (or your existing .env) and fill it in." }
if (-not (Test-Docker)) { throw "Docker is not reachable. Start Rancher Desktop (or Docker Desktop) and run this again." }

Write-Host "`n== Packages and builds" -ForegroundColor Cyan
if ($Install -or -not (Test-PythonPackages)) {
    Invoke-Checked "Python packages (pip install -r requirements.txt)" { & $Python -m pip install -r requirements.txt }
}
$EngineDir = "services/measure_calc/engine"
if ($Install -or -not (Test-Path "$EngineDir/node_modules")) {
    Invoke-Checked "Measure engine packages (npm install)" { & npm --prefix $EngineDir install }
}
if ($Install -or -not (Test-Path "$EngineDir/build/cli.js")) {
    Invoke-Checked "Measure engine build (npm run build)" { & npm --prefix $EngineDir run build }
}
if ($Install -or -not (Test-Path "ui/node_modules")) {
    Invoke-Checked "UI packages (npm install)" { & npm --prefix ui install }
}
Write-Host "  Everything is installed" -ForegroundColor DarkGray

& $Python -c "import os,psycopg;from dotenv import load_dotenv;load_dotenv('.env');psycopg.connect(os.environ['DATABASE_URL'].replace('+psycopg',''),connect_timeout=8).close()"
if ($LASTEXITCODE -ne 0) { throw "Postgres from .env's DATABASE_URL is not answering. Start or restart it and run this again." }

Write-Host "`n== Database" -ForegroundColor Cyan
& $Python -m alembic -c db/alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) { throw "Migrations failed." }
if ($Setup) {
    & $Python scripts/db/load_seed.py --reference-only
    if ($LASTEXITCODE -ne 0) { throw "Reference data load failed (it only runs on an empty schema)." }
}

Write-Host "`n== RabbitMQ" -ForegroundColor Cyan
Remove-OtherCloneContainers
& docker compose up -d rabbitmq
if ($LASTEXITCODE -ne 0) { throw "RabbitMQ did not start." }
Wait-Until "RabbitMQ" { (& docker inspect -f "{{.State.Health.Status}}" hedis-rabbitmq) -eq "healthy" }

if (-not $NoObservability) {
    Write-Host "`n== Prometheus, Grafana, Loki, Alloy" -ForegroundColor Cyan
    $dir = Join-Path $RepoRoot ".observability"
    New-Item -ItemType Directory -Force $dir, (Join-Path $RepoRoot "logs") | Out-Null
    $template = Get-Content "observability/prometheus.yml.template" -Raw
    [System.IO.File]::WriteAllText((Join-Path $dir "prometheus.yml"), $template.Replace("__TARGET__", "host.docker.internal:8000"))
    # docker compose does not read the root .env, so pass the Grafana passwords on for this process only.
    foreach ($key in "GRAFANA_ADMIN_PASSWORD", "GRAFANA_DB_PASSWORD") {
        if (-not [Environment]::GetEnvironmentVariable($key) -and $EnvText -match "(?m)^\s*$key\s*=\s*(\S.*?)\s*$") {
            [Environment]::SetEnvironmentVariable($key, $Matches[1].Trim('"', "'"), "Process")
        }
    }
    & docker compose -f observability/docker-compose.yml up -d
    if ($LASTEXITCODE -ne 0) { Write-Host "Observability did not start; the app runs without it." -ForegroundColor Yellow }
}

Write-Host "`n== Application" -ForegroundColor Cyan
Start-Window "backend 8000" "& '$Python' -m uvicorn app:app --host 127.0.0.1 --port 8000"
Wait-Until "Backend" {
    try { (Invoke-WebRequest "http://127.0.0.1:8000/docs" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 } catch { $false }
}
Start-Window "outbox dispatcher" "& '$Python' -m services.outbox_dispatcher"
Start-Window "workers" "& '$Python' scripts/run_workers.py"
# Vite alone listens on localhost, which resolves to ::1 only on Windows; pin it to the address printed below.
Start-Window "react ui" "Set-Location ui; npm run dev -- --host 127.0.0.1 --port 5173"

Write-Host "`nEverything is starting. Each part has its own window." -ForegroundColor Green
Write-Host "  React UI           http://127.0.0.1:5173"
Write-Host "  API docs           http://127.0.0.1:8000/docs"
Write-Host "  Pipeline monitor   http://127.0.0.1:8000/ingestion"
Write-Host "  RabbitMQ           http://localhost:15672"
if (-not $NoObservability) {
    Write-Host "  Grafana            http://localhost:3000"
    Write-Host "  Prometheus         http://localhost:9090/targets"
}
Write-Host "Stop it all with: powershell -ExecutionPolicy Bypass -File scripts/run_all.ps1 -Stop" -ForegroundColor Cyan
