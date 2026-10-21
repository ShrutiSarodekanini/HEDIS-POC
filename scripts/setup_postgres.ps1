param(
    [string]$PostgresBin = "C:\Program Files\PostgreSQL\18\bin",
    [string]$HostName = "localhost",
    [int]$Port = 5432,
    [string]$UserName = "postgres",
    [string]$DatabaseName = "hedis",
    [string]$TestDatabaseName = "hedis_test"
)

$ErrorActionPreference = "Stop"

foreach ($name in @($DatabaseName, $TestDatabaseName)) {
    if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
        throw "Invalid PostgreSQL database name: $name"
    }
}

$psql = Join-Path $PostgresBin "psql.exe"
$createdb = Join-Path $PostgresBin "createdb.exe"
if (-not (Test-Path $psql) -or -not (Test-Path $createdb)) {
    throw "PostgreSQL client tools were not found in $PostgresBin"
}

$repositoryRoot = Split-Path $PSScriptRoot -Parent
$seedPath = Join-Path $repositoryRoot "database\seed.sql"
$securePassword = Read-Host "PostgreSQL password for $UserName" -AsSecureString
$passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
$previousPassword = $env:PGPASSWORD
$previousDatabaseUrl = $env:DATABASE_URL

try {
    $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
        $passwordPointer
    )

    function Ensure-Database([string]$Name) {
        $exists = & $psql `
            --host $HostName `
            --port $Port `
            --username $UserName `
            --dbname postgres `
            --tuples-only `
            --no-align `
            --command "SELECT 1 FROM pg_database WHERE datname = '$Name';"
        if ($LASTEXITCODE -ne 0) {
            throw "Could not query PostgreSQL databases."
        }
        if ($exists.Trim() -ne "1") {
            & $createdb `
                --host $HostName `
                --port $Port `
                --username $UserName `
                $Name
            if ($LASTEXITCODE -ne 0) {
                throw "Could not create PostgreSQL database '$Name'."
            }
        }
    }

    Ensure-Database $DatabaseName
    Ensure-Database $TestDatabaseName

    $encodedUser = [Uri]::EscapeDataString($UserName)
    $encodedPassword = [Uri]::EscapeDataString($env:PGPASSWORD)

    Push-Location $repositoryRoot
    try {
        foreach ($name in @($DatabaseName, $TestDatabaseName)) {
            $env:DATABASE_URL = (
                "postgresql+psycopg://${encodedUser}:${encodedPassword}" +
                "@${HostName}:${Port}/${name}"
            )
            python -m alembic upgrade head
            if ($LASTEXITCODE -ne 0) {
                throw "Alembic migration failed for database '$name'."
            }
        }
    }
    finally {
        Pop-Location
    }

    & $psql `
        --host $HostName `
        --port $Port `
        --username $UserName `
        --dbname $DatabaseName `
        --set ON_ERROR_STOP=1 `
        --file $seedPath
    if ($LASTEXITCODE -ne 0) {
        throw "Applying database\seed.sql failed."
    }

    Write-Host "PostgreSQL application and test databases are ready."
    Write-Host "Set DATABASE_URL and TEST_DATABASE_URL before starting or testing."
}
finally {
    if ($null -eq $previousPassword) {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }
    else {
        $env:PGPASSWORD = $previousPassword
    }
    if ($null -eq $previousDatabaseUrl) {
        Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
    }
    else {
        $env:DATABASE_URL = $previousDatabaseUrl
    }
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
}
