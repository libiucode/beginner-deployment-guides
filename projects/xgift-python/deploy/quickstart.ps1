param([int]$Port = 8000, [int]$Codes = 5, [switch]$NoWindows, [string]$AdminPassword)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $Root
$env:PYTHONUTF8 = '1'

if (-not (Test-Path -LiteralPath (Join-Path $Root '.venv\Scripts\python.exe'))) {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw '未找到 uv。请先按教程安装 uv，例如：winget install --id astral-sh.uv -e'
    }
    Write-Host '正在安装依赖（首次执行需要几分钟）...'
    uv sync --locked
    if ($LASTEXITCODE) { throw '依赖安装失败' }
}
$Python = Join-Path $Root '.venv\Scripts\python.exe'

Write-Host '1/4 初始化数据库...'
& $Python manage.py migrate
if ($LASTEXITCODE) { throw '数据库初始化失败' }

Write-Host '2/4 创建管理员（已存在则不会覆盖；密码输入时不显示）...'
if ($AdminPassword) {
    # 仅供自动化测试/CI；日常使用请省略该参数，改用隐藏交互输入。
    & $Python manage.py setup_xgift --password $AdminPassword
} elseif ([Console]::IsInputRedirected) {
    throw '当前窗口无法安全输入密码。请在普通 PowerShell 窗口运行本脚本，或手动执行：.venv\Scripts\python.exe manage.py setup_xgift'
} else {
    & $Python manage.py setup_xgift
}
if ($LASTEXITCODE) { throw '管理员初始化失败' }

Write-Host '3/4 准备模拟演示套餐与兑换码...'
& $Python manage.py demo_setup --count $Codes --only-if-empty
if ($LASTEXITCODE) { throw '演示数据创建失败' }

Write-Host '4/4 整理静态文件...'
& $Python manage.py collectstatic --noinput
if ($LASTEXITCODE) { throw '静态文件整理失败' }

if ($NoWindows) {
    Write-Host '完成。请分别在两个终端运行：'
    Write-Host "  .venv\Scripts\python.exe manage.py serve_xgift --port $Port"
    Write-Host '  .venv\Scripts\python.exe manage.py run_worker'
    exit 0
}

# 把当前使用的配置文件一起传给新窗口，保证网站和 worker 用同一份数据/密钥。
$EnvLine = if ($env:XGIFT_ENV_FILE) { "`$env:XGIFT_ENV_FILE='$($env:XGIFT_ENV_FILE)'; " } else { '' }
Write-Host '正在打开「网站」与「worker」两个窗口...'
Start-Process powershell -ArgumentList '-NoExit', '-Command', "Set-Location -LiteralPath '$Root'; `$env:PYTHONUTF8='1'; $EnvLine& '$Python' manage.py serve_xgift --port $Port"
Start-Sleep -Seconds 2
Start-Process powershell -ArgumentList '-NoExit', '-Command', "Set-Location -LiteralPath '$Root'; `$env:PYTHONUTF8='1'; $EnvLine& '$Python' manage.py run_worker"
Start-Sleep -Seconds 2
Write-Host ''
Write-Host '本机体验地址：'
Write-Host "  兑换页面   http://127.0.0.1:$Port/"
Write-Host "  运营工作台 http://127.0.0.1:$Port/operator/"
Write-Host "  管理后台   http://127.0.0.1:$Port/admin/"
Write-Host '关闭刚才打开的两个窗口即可停止；不要直接删除数据文件。'
