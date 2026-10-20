# 注册复盘 20:10、盘中 09:30。优先 SYSTEM（开机不必登录）；无权限则当前用户：每日时刻 + 登录 + 开机。
$ErrorActionPreference = "Stop"
$log = "D:\workspace\GoldQuant\_task_reg.txt"
"start $(Get-Date -Format o) user=$env:USERNAME" | Set-Content $log -Encoding utf8
$ps1 = Join-Path $PSScriptRoot "scheduled_post_close.ps1"
$wd = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function New-GoldQuantSettings([string]$instances) {
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -WakeToRun `
        -ExecutionTimeLimit ([TimeSpan]::Zero)
    $settings.MultipleInstances = $instances
    $settings.DisallowStartIfOnBatteries = $false
    $settings.StopIfGoingOnBatteries = $false
    $settings.ExecutionTimeLimit = "PT0S"
    return $settings
}

foreach ($old in @("GoldQuantUpdateDaily", "GoldQuantTacticClose", "GoldQuantTacticIntraday")) {
    Unregister-ScheduledTask -TaskName $old -Confirm:$false -ErrorAction SilentlyContinue
}

function Register-GoldQuantTask($name, $mode, $dailyAt, $instances, $principal, $extraTriggers, $label) {
    $arg = "-NoProfile -ExecutionPolicy Bypass -File `"$ps1`" -Mode $mode"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $wd
    $daily = New-ScheduledTaskTrigger -Daily -At $dailyAt
    $triggers = @($daily) + @($extraTriggers)
    $settings = New-GoldQuantSettings $instances
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Force | Out-Null
    $msg = "OK $name $label"
    Add-Content $log $msg -Encoding utf8
    Write-Host $msg
}

try {
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    Register-GoldQuantTask "GoldQuantTacticClose" "tactic" "20:10" "Queue" $principal @() "SYSTEM Daily20:10"
    Register-GoldQuantTask "GoldQuantTacticIntraday" "intraday" "09:30" "IgnoreNew" $principal @() "SYSTEM Daily09:30"
} catch {
    $msg = "FAIL $($_.Exception.Message)"
    Add-Content $log $msg -Encoding utf8
    Write-Host "SYSTEM 注册失败（$($_.Exception.Message)），改为当前用户：每日时刻 + 登录 + 开机"
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    $logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $boot = New-ScheduledTaskTrigger -AtStartup
    Register-GoldQuantTask "GoldQuantTacticClose" "tactic" "20:10" "Queue" $principal @($logon, $boot) "Interactive Daily20:10+Logon+Startup"
    Register-GoldQuantTask "GoldQuantTacticIntraday" "intraday" "09:30" "IgnoreNew" $principal @($logon, $boot) "Interactive Daily09:30+Logon+Startup"
}
