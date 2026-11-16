# Start or stop the app as containers, one per component (docker-compose.deploy.yml).
#
#   powershell -ExecutionPolicy Bypass -File scripts/run_containers.ps1            build and start, with monitoring
#   powershell -ExecutionPolicy Bypass -File scripts/run_containers.ps1 -Setup     also load the reference data (new schema)
#   powershell -ExecutionPolicy Bypass -File scripts/run_containers.ps1 -NoBuild   start without rebuilding the images
#   powershell -ExecutionPolicy Bypass -File scripts/run_containers.ps1 -Stop      stop, keep containers and data
#   powershell -ExecutionPolicy Bypass -File scripts/run_containers.ps1 -Reset     remove containers and volumes
#   powershell -ExecutionPolicy Bypass -File scripts/run_containers.ps1 -NoObservability   the app only
#
# Prometheus, Grafana, Loki and Alloy run in one container, monitoring (docker/monitoring/docker-compose.yml), which
# starts with the app unless -NoObservability is given; every service then ships its logs to Alloy. -Stop and -Reset
# always include it, so nothing is left running (-Reset also removes its volumes).
#
# The database schema is not removed by -Reset. To start clean, put a new schema name in .env.deploy and use -Setup.

param(
    [switch]$Setup,
    [switch]$NoBuild,
    [switch]$Stop,
    [switch]$Reset,
    [switch]$NoObservability
)

# Continue, not Stop: Windows PowerShell 5.1 turns any stderr line from docker (progress, warnings) into a fatal
# error. Failures are caught through $LASTEXITCODE and throw instead.
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)
$Compose = @("compose", "-p", "hedis-deploy", "-f", "docker-compose.deploy.yml")
# Monitoring is on by default; -Stop and -Reset always include it so its containers are never left behind.
$Observability = $Stop -or $Reset -or -not $NoObservability
if ($Observability) { $Compose += @("-f", "docker/monitoring/docker-compose.yml") }

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
if (-not $NoBuild) {
    # The component images start FROM hedis-base and copy from hedis-engine; compose cannot order that itself.
    Invoke-Compose @("--profile", "build", "build", "base", "engine")
    Invoke-Compose @("build")
}

if ($Observability) {
    # Containers from the earlier four-service monitoring would hold ports 3001 and 9091; their volumes are reused.
    foreach ($old in "prometheus", "grafana", "loki", "alloy") {
        $ids = & docker ps -aq --filter "label=com.docker.compose.project=hedis-deploy" `
            --filter "label=com.docker.compose.service=$old"
        if ($ids) { & docker rm -f $ids | Out-Null }
    }
    # The monitoring tools run as uid 472; volumes made by the earlier four monitoring containers belong to other users.
    & docker @Compose run --rm --no-deps --user root --entrypoint chown monitoring -R 472:0 /prometheus /loki `
        /var/lib/alloy/data /var/lib/grafana/state
    if ($LASTEXITCODE -ne 0) { Write-Host "Warning: could not hand the monitoring volumes to its user." -ForegroundColor Yellow }
}

Invoke-Compose @("up", "-d")

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
if ($Observability) {
    Write-Host "  Grafana    http://127.0.0.1:3001"
    Write-Host "  Prometheus http://127.0.0.1:9091/targets"
    Write-Host "  Logs       Grafana > Explore > HEDIS logs"
    Write-Host "  Monitoring docker logs hedis-deploy-monitoring-1"
}
Write-Host "Stop it with: powershell -ExecutionPolicy Bypass -File scripts/run_containers.ps1 -Stop"
