$ErrorActionPreference='Stop'
Stop-Service -Name XGiftWorker
Stop-Service -Name XGiftWeb
Get-Service -Name XGiftWeb,XGiftWorker
