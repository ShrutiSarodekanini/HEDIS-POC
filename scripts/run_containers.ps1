# Start or stop the app as containers, one per component (docker-compose.deploy.yml).
#
#   powershell -ExecutionPolicy Bypass -File scripts/run_containers.ps1            build and start
#   powershell -ExecutionPolicy Bypass -File scripts/run_containers.ps1 -Setup     also load the reference data (new schema)
#   powershell -ExecutionPolicy Bypass -File scripts/run_containers.ps1 -NoBuild   start without rebuilding the images
#   powershell -ExecutionPolicy Bypass -File scripts/run_containers.ps1 -Stop      stop, keep containers and data
#   powershell -ExecutionPolicy Bypass -File scripts/run_containers.ps1 -Reset     remove containers and volumes
#
# The database schema is not removed by -Reset. To start clean, put a new schema name in .env.deploy and use -Setup.

param(
    [switch]$Setup,
    [switch]$NoBuild,
    [switch]$Stop,
    [switch]$Reset
)

# Continue, not Stop: Windows PowerShell 5.1 turns any stderr line from docker (progress, warnings) into a fatal
# error. Failures are caught through $LASTEXITCODE and throw instead.
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)
$Compose = @("compose", "-p", "hedis-deploy", "-f", "docker-compose.deploy.yml")

function Invoke-Compose([string[]]$Arguments) {
    & docker @Compose @Arguments
    if ($LASTEXITCODE -ne 0) { throw "docker compose $($Arguments -join ' ') failed." }
}

function Get-ContainerId([string]$Service) {
    $id = & docker @Compose ps -a -q $Service
    if ($LASTEXITCODE -ne 0) { throw "Could not list the $Service container." }
    return $id
}

& docker info *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker is not running. Start Rancher Desktop or Docker Desktop first." }

if ($Stop) {
    Invoke-Compose @("stop")
    Write-Host "Stopped. Containers and data are kept; run the script again to start." -ForegroundColor Green
    return
}

if ($Reset) {
    Invoke-Compose @("--profile", "setup", "down", "-v")
    Write-Host "Removed the containers and volumes. The database schema was not touched." -ForegroundColor Green
    return
}

foreach ($file in ".env", ".env.deploy") {
    if (-not (Test-Path $file)) { throw "$file is missing. See docs/deployment/CONTAINERS.md." }
}

# up -d (not start) so changes to .env or .env.deploy reach the containers.
if ($NoBuild) { Invoke-Compose @("up", "-d") } else { Invoke-Compose @("up", "-d", "--build") }

$migrate = Get-ContainerId "migrate"
$migrateExit = & docker inspect -f "{{.State.ExitCode}}" $migrate
if ($migrateExit -ne "0") {
    & docker logs --tail 30 $migrate
    throw "The migrate job failed (exit $migrateExit). Its log is above."
}

Write-Host "Waiting for the API to become healthy..." -ForegroundColor Green
$api = Get-ContainerId "api"
$deadline = (Get-Date).AddMinutes(5)
while ((& docker inspect -f "{{.State.Health.Status}}" $api) -ne "healthy") {
    if ((Get-Date) -gt $deadline) {
        & docker logs --tail 30 $api
        throw "The API did not become healthy within 5 minutes. Its log is above."
    }
    Start-Sleep -Seconds 3
}

if ($Setup) {
    Write-Host "Loading the reference data..." -ForegroundColor Green
    $output = & docker @Compose --profile setup run --rm seed 2>&1 | Out-String
    $seedExit = $LASTEXITCODE
    if ($seedExit -eq 0) {
        Write-Host "Reference data loaded."
    } elseif ($output -match "source_batch_pkey") {
        Write-Host "Reference data was already loaded in this schema; loading only the evidence library." -ForegroundColor Yellow
        Invoke-Compose @("--profile", "setup", "run", "--rm", "seed", "python", "scripts/db/load_seed.py", "--evidence-only")
    } else {
        Write-Host $output
        throw "Loading the reference data failed. The output is above."
    }
}

Write-Host "`nThe app is running." -ForegroundColor Green
Write-Host "  App        http://127.0.0.1:8080"
Write-Host "  RabbitMQ   http://localhost:15673"
Write-Host "Stop it with: powershell -ExecutionPolicy Bypass -File scripts/run_containers.ps1 -Stop"
