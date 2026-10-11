$ErrorActionPreference='Stop'
Start-Service -Name XGiftWeb
Start-Service -Name XGiftWorker
Get-Service -Name XGiftWeb,XGiftWorker
