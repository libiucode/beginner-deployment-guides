param([string]$ServiceAccount='NT AUTHORITY\LocalService')
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Private = Join-Path $Root 'private'
if (-not (Test-Path -LiteralPath (Join-Path $Private 'server.env'))) { throw 'Create private/server.env first' }
if ($ServiceAccount -ne 'NT AUTHORITY\LocalService') { throw 'Install as LocalService; configure a dedicated account privately in services.msc afterwards' }
# No password appears in arguments.
& icacls $Private '/inheritance:r' '/grant:r' "${ServiceAccount}:(OI)(CI)M" 'SYSTEM:(OI)(CI)F' 'BUILTIN\Administrators:(OI)(CI)F'
if ($LASTEXITCODE) { throw 'Private directory ACL failed' }
& icacls $Root '/grant' "${ServiceAccount}:(OI)(CI)RX"
if ($LASTEXITCODE) { throw 'Application directory ACL failed' }
$env:XGIFT_ENV_FILE=Join-Path $Private 'server.env'
Push-Location -LiteralPath $Root
try {
    & $Python -m deploy.restrict_data --account $ServiceAccount
    if ($LASTEXITCODE) { throw 'Data ACL failed' }
foreach ($Kind in @('web','worker')) {
    & $Python -m deploy.windows_service $Kind --startup auto --username $ServiceAccount install
    if ($LASTEXITCODE) { throw 'Service installation failed' }
}
} finally { Pop-Location }
Write-Host 'Registered as LocalService; review paths and permissions before starting.'
