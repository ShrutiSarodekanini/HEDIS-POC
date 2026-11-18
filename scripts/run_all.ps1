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
# Node 20+, and a repo-root .env (copy .env.example). The Python packages install on a fresh clone and again whenever
# requirements.txt changes; the measure engine build and the UI packages install when missing. Migrations always
# run; they are a no-op when up to date. For AI evidence, .env also needs EXTRACTION_DATA_CLASS=synthetic and an
# EXTRACTION_MODEL that is serving on OpenRouter. Key values are never printed.

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

# The Python packages are reinstalled whenever requirements.txt (or the Python in use) changes since the last
# install, so a pull that adds a package installs it. The marker holds a hash of both and lives in logs/ (ignored).
$RequirementsMarker = Join-Path $RepoRoot "logs\python-requirements.sha256"

function Get-RequirementsHash {
    $text = (Get-Content "requirements.txt" -Raw) + "|" + $Python
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "") } finally { $sha.Dispose() }
}

function Test-PythonPackages {
    if (-not (Test-Path $RequirementsMarker)) { return $false }
    if ((Get-Content $RequirementsMarker -Raw).Trim() -ne (Get-RequirementsHash)) { return $false }
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
    New-Item -ItemType Directory -Force (Split-Path $RequirementsMarker) | Out-Null
    [System.IO.File]::WriteAllText($RequirementsMarker, (Get-RequirementsHash))
}
$EngineDir = "services/measure_calc/engine"
# npm runs inside each folder: "npm --prefix <dir> install" from the repo root links the root package into the
# folder's package.json as a dependency.
function Invoke-Npm([string]$Dir, [string[]]$NpmArgs) {
    Push-Location $Dir
    try { & npm @NpmArgs } finally { Pop-Location }
}
if ($Install -or -not (Test-Path "$EngineDir/node_modules")) {
    Invoke-Checked "Measure engine packages (npm install)" { Invoke-Npm $EngineDir @("install") }
}
if ($Install -or -not (Test-Path "$EngineDir/build/cli.js")) {
    Invoke-Checked "Measure engine build (npm run build)" { Invoke-Npm $EngineDir @("run", "build") }
}
if ($Install -or -not (Test-Path "ui/node_modules")) {
    Invoke-Checked "UI packages (npm install)" { Invoke-Npm "ui" @("install") }
}
# npm sometimes skips a package's optional platform binary (npm/cli#4828); without it Vite exits on start.
if (-not (Test-Path "ui/node_modules/@rolldown/binding-win32-x64-msvc")) {
    $rolldown = (Get-Content "ui/node_modules/rolldown/package.json" -Raw | ConvertFrom-Json).version
    Invoke-Checked "UI build tool binary (rolldown $rolldown for Windows)" {
        Invoke-Npm "ui" @("install", "@rolldown/binding-win32-x64-msvc@$rolldown", "--no-save")
    }
}
Write-Host "  Everything is installed" -ForegroundColor DarkGray

& $Python -c "import os,psycopg;from dotenv import load_dotenv;load_dotenv('.env');psycopg.connect(os.environ['DATABASE_URL'].replace('+psycopg',''),connect_timeout=8).close()"
if ($LASTEXITCODE -ne 0) { throw "Postgres from .env's DATABASE_URL is not answering. Start or restart it and run this again." }

Write-Host "`n== Database" -ForegroundColor Cyan
& $Python scripts/db/ensure_schema.py
if ($LASTEXITCODE -ne 0) { throw "The schema in .env's DATABASE_URL is not usable (see the message above)." }
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
    # docker compose sometimes starts every container and then never returns (seen on Rancher Desktop), so give it
    # two minutes and judge by the containers instead of by the command.
    $compose = Start-Process docker -ArgumentList "compose", "-f", "observability/docker-compose.yml", "up", "-d" `
        -NoNewWindow -PassThru
    if (-not $compose.WaitForExit(120000)) {
        Get-CimInstance Win32_Process -Filter "ParentProcessId=$($compose.Id)" |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        $compose.Kill()
    }
    $running = & docker ps --filter "name=hedis-" --filter "status=running" --format "{{.Names}}"
    $missing = @("hedis-prometheus", "hedis-grafana", "hedis-loki", "hedis-alloy") | Where-Object { $_ -notin $running }
    if ($missing) { Write-Host "Not running: $($missing -join ', '). The app runs without them." -ForegroundColor Yellow }
    else { Write-Host "  Observability is running" -ForegroundColor DarkGray }
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
