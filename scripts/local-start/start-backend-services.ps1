param(
    [string]$ProjectRoot = "D:\personal\HEDIS-Team-repo\HEDIS-POC",
    [int]$ApiPort = 8000,
    [switch]$InstallDeps,
    [switch]$SkipPrelaunchCleanup
)

$ErrorActionPreference = "Stop"

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

    foreach ($proc in $existingProcesses) {
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

Start-Process powershell -ArgumentList @("-NoExit", "-Command", $apiCommand)
Start-Sleep -Seconds 1
Start-Process powershell -ArgumentList @("-NoExit", "-Command", $workerCommand)

Write-Host "Started backend API and DQ worker in separate PowerShell windows."
Write-Host "API URL: http://localhost:$ApiPort"
