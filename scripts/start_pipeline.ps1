#!/usr/bin/env powershell
<#
.SYNOPSIS
Start all 10 pipeline workers and the API server.

.DESCRIPTION
This script starts the complete async ingestion pipeline:
    - PostgreSQL and RabbitMQ containers
    - OutboxDispatcher - Publishes committed messages to RabbitMQ
  - API Server (port 8780) - Accepts file uploads
  - ConversionWorker - Converts CSV/JSON/FHIR to FHIR format
  - LoadWorker - Loads FHIR resources into PostgreSQL
  - QualityWorker - Validates data quality
    - ValueSetsWorker - Validates configured measure value sets
  - MeasuresWorker - Calculates HEDIS quality measures
    - GapIdentifierWorker - Checkpoints identified care gaps
  - GapsWorker - Prioritizes care gaps
  - DocumentsWorker - Attaches evidence documents
    - EvidenceWorker - Extracts proposed evidence for human review
    - StarsWorker - Calculates the batch star summary

All workers listen to RabbitMQ and process messages asynchronously.

.USAGE
    powershell -ExecutionPolicy Bypass -File scripts/start_pipeline.ps1

Each component runs in its own terminal window.
#>

param(
    [switch]$Verbose = $false
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$shell = (Get-Process -Id $PID).Path

if (-not (Test-Path $python)) {
    throw "Python virtual environment not found at $python"
}

Set-Location $projectRoot

$postgresContainer = docker ps -a --filter "name=^/hedis-postgres$" --format "{{.Names}}"
if ($postgresContainer -eq "hedis-postgres") {
    docker start hedis-postgres | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not start PostgreSQL container" }
} else {
    Write-Warning "hedis-postgres container was not found; DATABASE_URL must point to a running PostgreSQL server."
}

docker compose up -d rabbitmq
if ($LASTEXITCODE -ne 0) { throw "Could not start RabbitMQ container" }

$rabbitReady = $false
for ($attempt = 1; $attempt -le 30; $attempt++) {
    docker exec hedis-rabbitmq rabbitmq-diagnostics -q ping 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $rabbitReady = $true
        break
    }
    Start-Sleep -Seconds 1
}
if (-not $rabbitReady) { throw "RabbitMQ did not become ready within 30 seconds" }

$workers = @(
    @{ name = "OutboxDispatcher"; module = "services.outbox_dispatcher" },
    @{ name = "ConversionWorker"; module = "services.workers.conversion_worker" },
    @{ name = "LoadWorker"; module = "services.workers.load_worker" },
    @{ name = "QualityWorker"; module = "services.workers.quality_worker" },
    @{ name = "ValueSetsWorker"; module = "services.workers.valuesets_worker" },
    @{ name = "MeasuresWorker"; module = "services.workers.measures_worker" },
    @{ name = "GapIdentifierWorker"; module = "services.workers.gap_identifier_worker" },
    @{ name = "GapsWorker"; module = "services.workers.gaps_worker" },
    @{ name = "DocumentsWorker"; module = "services.workers.documents_worker" },
    @{ name = "EvidenceWorker"; module = "services.workers.evidence_worker" },
    @{ name = "StarsWorker"; module = "services.workers.stars_worker" }
)

Write-Host "`nHEDIS POC - Async Pipeline Startup`n" -ForegroundColor Green

Write-Host "Available components:" -ForegroundColor Yellow
$workers | ForEach-Object { Write-Host "  $($_.name)" -ForegroundColor Cyan }

Write-Host "`nStarting all components..." -ForegroundColor Yellow
Write-Host "(Each runs in a separate terminal window)`n" -ForegroundColor Gray

foreach ($worker in $workers) {
    $window_title = $worker.name
    Write-Host "Starting: $window_title" -ForegroundColor Green
    Start-Process $python -ArgumentList @("-m", $worker.module) -WorkingDirectory $projectRoot -WindowStyle Normal

    Start-Sleep -Milliseconds 500  # Stagger startup
}

Write-Host "Starting: API Server" -ForegroundColor Green
Start-Process $shell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location `"$projectRoot`"; powershell -ExecutionPolicy Bypass -File scripts/run_demo.ps1 -Integration"
) -WindowStyle Normal

Write-Host "`nAll components launched!`n" -ForegroundColor Green
Write-Host "Monitor pipeline status:" -ForegroundColor Yellow
Write-Host "   python check_queues.py`n" -ForegroundColor Cyan

Write-Host "Test file upload:" -ForegroundColor Yellow
Write-Host "   python -c 'import requests; requests.post(\"http://localhost:8780/api/ingestion/upload-async\", files={\"file\": open(\"data/seed/members/members.csv\", \"rb\")})'" -ForegroundColor Cyan

Write-Host "`nRabbitMQ Management:" -ForegroundColor Yellow
Write-Host "   http://localhost:15672 (guest/guest)`n" -ForegroundColor Cyan

Write-Host "Waiting for components to stabilize..." -ForegroundColor Gray
Start-Sleep -Seconds 5

Write-Host "Pipeline is running! Check individual terminal windows for logs." -ForegroundColor Green
