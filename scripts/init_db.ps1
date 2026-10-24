# One-time database setup. Reads role names/passwords from .env and runs
# init_db.sql as the PostgreSQL superuser (psql prompts for its password).
#
# Usage:  .\scripts\init_db.ps1            (superuser defaults to "postgres")
#         .\scripts\init_db.ps1 -Superuser myadmin

param(
    [string]$Superuser = "postgres",
    [string]$Psql = "C:\Program Files\PostgreSQL\18\bin\psql.exe"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) { throw ".env not found. Copy .env.example to .env and fill it in first." }

$cfg = @{}
foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*([A-Z_]+)\s*=\s*(.*)\s*$') { $cfg[$Matches[1]] = $Matches[2] }
}

& $Psql -h $cfg.DB_HOST -p $cfg.DB_PORT -U $Superuser -d postgres `
    -v owner_user=$($cfg.DB_OWNER_USER) -v owner_password=$($cfg.DB_OWNER_PASSWORD) `
    -v app_user=$($cfg.DB_APP_USER) -v app_password=$($cfg.DB_APP_PASSWORD) `
    -v db_name=$($cfg.DB_NAME) -v test_db_name=$($cfg.DB_TEST_NAME) `
    -f (Join-Path $PSScriptRoot "init_db.sql")

if ($LASTEXITCODE -ne 0) { throw "init_db.sql failed (exit $LASTEXITCODE)" }
Write-Host "Database setup complete: $($cfg.DB_NAME), $($cfg.DB_TEST_NAME)"
