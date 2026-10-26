param(
  [ValidateSet('aab','bcse','bpd','cbp','ccse','cole','eed','gsd','ked','spd')]
  [string]$MeasureId = 'aab',
  [string]$PatientDir = '',
  [int]$MaxPatients = 10,
  [string]$OutputDir = '.\examples\_tmp_fqm_synthea_runs'
)

$ErrorActionPreference = 'Stop'

# Resolve patient directory in a portable way:
# 1) explicit -PatientDir argument
# 2) SYNTHETEA_PATIENT_DIR environment variable
# 3) workspace-relative fallback
if (-not $PatientDir) {
  if ($env:SYNTHETEA_PATIENT_DIR) {
    $PatientDir = $env:SYNTHETEA_PATIENT_DIR
  } else {
    $PatientDir = '.\examples\patients'
  }
}

$measureFolder = Join-Path '.\examples' ("independent_public_$MeasureId")
if (-not (Test-Path $measureFolder)) {
  throw "Measure folder not found: $measureFolder"
}

$bundlePath = Get-ChildItem -Path $measureFolder -Filter '*bundle.json' -File | Select-Object -First 1
if (-not $bundlePath) {
  throw "No measure bundle found under $measureFolder"
}

if (-not (Test-Path $PatientDir)) {
  throw "Patient directory not found: $PatientDir. Provide -PatientDir or set SYNTHETEA_PATIENT_DIR."
}

$patientBundles = Get-ChildItem -Path $PatientDir -Filter '*.json' -File |
  Where-Object { $_.Name -notmatch 'hospitalInformation|practitionerInformation' } |
  Select-Object -First $MaxPatients

if ($patientBundles.Count -eq 0) {
  throw "No patient bundle json files found in $PatientDir"
}

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$outputFile = Join-Path $OutputDir ("$MeasureId-$timestamp-output.json")

Write-Host "Running FQM CLI"
Write-Host "Measure: $MeasureId"
Write-Host "Bundle: $($bundlePath.FullName)"
Write-Host "Patients: $($patientBundles.Count) from $PatientDir"
Write-Host "Output: $outputFile"

$patientArgs = @()
foreach ($p in $patientBundles) {
  $patientArgs += $p.FullName
}

npm run cli -- detailed --measure-bundle $bundlePath.FullName --patient-bundles $patientArgs --out-file $outputFile

Write-Host "Done. Output written to $outputFile"
