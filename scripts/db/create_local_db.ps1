# Prepares the local development database for the HEDIS POC and writes .env.
#
# Run:  powershell -ExecutionPolicy Bypass -File "D:\HEDISPOC\scripts\db\create_local_db.ps1" [-DbName HEDISPOCDB]
#
# - Uses the database named by -DbName (default HEDISPOCDB). If it does not exist, it is created.
#   An existing database is never changed or emptied; its current tables are listed for you to check.
# - Generates a random password for the app user hedis_app (stored only in .env, never printed).
# - Asks for YOUR postgres admin password once, through psql's own prompt.
# - Gives hedis_app access to that database only (connect, create in schema public).
# - Writes DATABASE_URL to the repo-root .env (git-ignored). An existing .env is not overwritten.

param([string]$DbName = "HEDISPOCDB")

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$EnvFile = Join-Path $RepoRoot ".env"
$Psql = "C:\Program Files\PostgreSQL\18\bin\psql.exe"
$AppUser = "hedis_app"

if (-not (Test-Path $Psql)) { throw "psql not found at $Psql" }
if (Test-Path $EnvFile) {
    Write-Host ".env already exists at $EnvFile. Delete it first if you want a new database user password."
    exit 1
}

$chars = [char[]]"ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$AppPassword = -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })

# psql variables keep the database name quoted exactly as given (it may be upper case).
$sql = @"
\set ON_ERROR_STOP on
DO `$`$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$AppUser') THEN
        CREATE ROLE $AppUser LOGIN PASSWORD '$AppPassword';
    ELSE
        ALTER ROLE $AppUser WITH LOGIN PASSWORD '$AppPassword';
    END IF;
END
`$`$;
-- Find an existing database with this name in any letter case (exact match preferred).
SELECT datname AS realname FROM pg_database
 WHERE lower(datname) = lower(:'dbname') ORDER BY (datname = :'dbname') DESC LIMIT 1 \gset
\if :{?realname}
    \echo Using existing database :realname
\else
    \echo Database :dbname not found; creating it.
    SELECT format('CREATE DATABASE %I OWNER $AppUser', :'dbname') \gexec
    \set realname :dbname
\endif
\o :namefile
\qecho :realname
\o
SELECT format('GRANT CONNECT, CREATE, TEMPORARY ON DATABASE %I TO $AppUser', :'realname') \gexec
\connect :"realname"
GRANT USAGE, CREATE ON SCHEMA public TO $AppUser;
\echo
\echo Tables already in this database (schema, table, owner):
SELECT schemaname, tablename, tableowner FROM pg_tables
 WHERE schemaname NOT IN ('pg_catalog', 'information_schema') ORDER BY 1, 2;
"@

$tmp = New-TemporaryFile
$nameFile = New-TemporaryFile
try {
    Set-Content -Path $tmp -Value $sql -Encoding ascii
    Write-Host "Looking for database '$DbName'. Enter the password for the PostgreSQL 'postgres' admin user when asked."
    & $Psql -h localhost -p 5432 -U postgres -d postgres -q -v "dbname=$DbName" -v "namefile=$($nameFile.FullName)" -f $tmp
    if ($LASTEXITCODE -ne 0) { throw "psql failed (exit $LASTEXITCODE). Nothing was written to .env." }
    $DbName = (Get-Content $nameFile -Raw).Trim()
    if (-not $DbName) { throw "Could not determine the database name. Nothing was written to .env." }
} finally {
    Remove-Item $tmp, $nameFile -Force -ErrorAction SilentlyContinue
}

Set-Content -Path $EnvFile -Encoding ascii -Value "DATABASE_URL=postgresql+psycopg://${AppUser}:${AppPassword}@localhost:5432/${DbName}"
Write-Host "Done. User '$AppUser' can use database '$DbName', and .env was written to $EnvFile"
