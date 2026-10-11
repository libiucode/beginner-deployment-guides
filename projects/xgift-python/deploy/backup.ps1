param([Parameter(Mandatory=$true)][string]$Destination)
$ErrorActionPreference='Stop'
$Root=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
foreach ($Name in @('XGiftWeb','XGiftWorker')) {
    $Service=Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($Service -and $Service.Status -ne 'Stopped') { throw 'Stop both services before backing up' }
}
$env:XGIFT_ENV_FILE=Join-Path $Root 'private\server.env'
Push-Location -LiteralPath $Root
try {
    & (Join-Path $Root '.venv\Scripts\python.exe') -m deploy.backup --destination $Destination
    if ($LASTEXITCODE) { throw 'Backup failed' }
} finally { Pop-Location }
