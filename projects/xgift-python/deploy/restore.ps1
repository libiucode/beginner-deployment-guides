param([Parameter(Mandatory=$true)][string]$Source)
$ErrorActionPreference='Stop'
$Root=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
foreach ($Name in @('XGiftWeb','XGiftWorker')) {
    $Service=Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($Service -and $Service.Status -ne 'Stopped') { throw 'Stop both services before restoring' }
}
if (-not (Test-Path -LiteralPath (Join-Path $Root 'private\server.env'))) { throw 'Create private/server.env first' }
$env:XGIFT_ENV_FILE=Join-Path $Root 'private\server.env'
Push-Location -LiteralPath $Root
try {
    & (Join-Path $Root '.venv\Scripts\python.exe') -m deploy.restore --source $Source --replace
    if ($LASTEXITCODE) { throw 'Restore failed' }
    & (Join-Path $Root '.venv\Scripts\python.exe') manage.py check
    if ($LASTEXITCODE) { throw 'Restored data failed the site check' }
} finally { Pop-Location }
Write-Host 'Restore complete; start the services and verify the site before retiring any backup.'
