# 任务计划程序入口。SYSTEM 账户开机不必登录。
# tactic：20:10 复盘。intraday：09:30 起每 3 分钟到 15:05。
param(
    [ValidateSet("session", "daily", "tactic", "intraday")]
    [string]$Mode = "daily"
)

$ErrorActionPreference = "Continue"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$PyCandidates = @(
    (Join-Path $Root ".venv\Scripts\python.exe"),
    "D:\ProgramData\pypoetry\cache\virtualenvs\goldquant-JA6Nvtj9-py3.11\Scripts\python.exe"
)
$Py = $PyCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Py) {
    throw "找不到 GoldQuant 虚拟环境 python"
}

$QuantHome = "D:\ProgramData\.quant"
$LogDir = Join-Path $QuantHome "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir "scheduled_post_close.log"

function Write-Log([string]$msg) {
    Add-Content -Path $Log -Value $msg -Encoding utf8
}

Set-Location $Root
$env:PYTHONUNBUFFERED = "1"
$env:QUANT_HOME = $QuantHome
$now = Get-Date
$stamp = $now.ToString("yyyy-MM-dd HH:mm:ss")
Write-Log "`n==== $stamp mode=$Mode ===="

if ($Mode -eq "session") {
    $mod = "scripts.data.capture_session_archive"
    $extra = @("--closed-session")
} elseif ($Mode -eq "tactic") {
    $mod = "scripts.tactic.close"
    $extra = @()
} elseif ($Mode -eq "intraday") {
    $mod = "scripts.tactic.intraday"
    $extra = @("--loop")
} else {
    $mod = "scripts.data.update_daily"
    $extra = @("--closed-session")
}

& $Py -m $mod --home $QuantHome @extra 2>&1 | Out-File -FilePath $Log -Append -Encoding utf8
Write-Log ("exit=" + $LASTEXITCODE)
exit $LASTEXITCODE
