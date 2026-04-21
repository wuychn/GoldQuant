# GoldQuant 开发启动脚本（需在项目根目录执行，或右键「使用 PowerShell 运行」本脚本）
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Write-Host "未找到 $Py ，请先创建 venv 并安装依赖: pip install -r requirements.txt" -ForegroundColor Yellow
    exit 1
}

& $Py -m app @args
