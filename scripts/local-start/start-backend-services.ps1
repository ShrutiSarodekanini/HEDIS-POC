param(
    [string]$ProjectRoot,
    [int]$ApiPort = 8000,
    [int]$IntegrationPort = 8780,
    [switch]$InstallDeps,
    [switch]$SkipPrelaunchCleanup
)

$ErrorActionPreference = "Stop"

if (-not $ProjectRoot) {
    $ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}

$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$pythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $ProjectRoot)) {
    throw "Project root not found: $ProjectRoot"
}

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Python executable not found: $pythonExe. Create .venv and install dependencies first."
}

if (-not $SkipPrelaunchCleanup) {
    Write-Host "Stopping existing backend Python/Uvicorn processes for this project..."
    $escapedProjectRoot = [Regex]::Escape($ProjectRoot)
    $existingProcesses = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        ($_.Name -in @("python.exe", "pythonw.exe", "uvicorn.exe")) -and
        $_.CommandLine -and
        ($_.CommandLine -match $escapedProjectRoot)
    }
    $processesById = @{}
    foreach ($proc in $existingProcesses) {
        $processesById[$proc.ProcessId] = $proc
    }

    do {
        $childProcesses = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $processesById.ContainsKey($_.ParentProcessId) -and -not $processesById.ContainsKey($_.ProcessId)
        })
        foreach ($proc in $childProcesses) {
            $processesById[$proc.ProcessId] = $proc
        }
    } while ($childProcesses.Count -gt 0)

    foreach ($proc in $processesById.Values) {
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }

    Write-Host "Clearing __pycache__ folders under project root..."
    Get-ChildItem -Path $ProjectRoot -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}

$pythonVersion = & $pythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if (-not $?) {
    throw "Unable to determine Python version from $pythonExe"
}

if ([version]$pythonVersion -ge [version]"3.14") {
    throw "Detected Python $pythonVersion in .venv. This repository's FastAPI/Pydantic stack is currently incompatible with Python 3.14. Recreate .venv with Python 3.12 and reinstall requirements."
}

$requirementsFile = Join-Path $ProjectRoot "requirements.txt"
if ($InstallDeps) {
    if (-not (Test-Path -LiteralPath $requirementsFile)) {
        throw "requirements.txt not found at $requirementsFile"
    }
    & $pythonExe -m pip install -r $requirementsFile
    if (-not $?) {
        throw "Dependency installation failed."
    }
}

& $pythonExe -c "import sqlalchemy, fastapi, uvicorn" 2>$null
if (-not $?) {
    throw "Required packages are missing in .venv. Run: `"$pythonExe`" -m pip install -r `"$requirementsFile`" or start script with -InstallDeps."
}

$apiCommand = "Set-Location '$ProjectRoot'; & '$pythonExe' -m uvicorn services.multi_source_ingestion.main:app --host 0.0.0.0 --port $ApiPort --reload"
$workerCommand = "Set-Location '$ProjectRoot'; & '$pythonExe' -m services.data_quality_rules.main"
$integrationUiDir = Join-Path $ProjectRoot "services\integration\ui\redesign"
if (-not (Test-Path -LiteralPath (Join-Path $integrationUiDir "index.html"))) {
    throw "Latest integration UI not found at $integrationUiDir"
}
$integrationCommand = "Set-Location '$ProjectRoot'; `$env:INTEGRATION_PORT = '$IntegrationPort'; " +
    "`$env:INTEGRATION_UI_DIR = '$integrationUiDir'; & '$pythonExe' -m services.integration.app"

Start-Process powershell -ArgumentList @("-NoExit", "-Command", $apiCommand)
Start-Sleep -Seconds 1
Start-Process powershell -ArgumentList @("-NoExit", "-Command", $workerCommand)
Start-Process powershell -ArgumentList @("-NoExit", "-Command", $integrationCommand)

Write-Host "Started backend API, DQ worker, and HEDIS Ops Console in separate PowerShell windows."
Write-Host "API URL: http://localhost:$ApiPort"
Write-Host "HEDIS Ops Console URL: http://localhost:$IntegrationPort"
