# 注册/更新"每月 20 日"执行发票收集的计划任务。
#
#   powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -At 09:30
#   powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -Unregister
#
# 任务以当前用户身份运行、仅在用户登录时执行（不存储密码，避免凭据落盘）。
#
# 注意：本文件必须以 UTF-8 **带 BOM** 保存；Windows PowerShell 5.1 无 BOM 时按 ANSI
# 读取脚本，中文默认值会乱码并导致参数绑定失败（check_task.ps1 实测踩过）。
param(
    [string]$TaskName = "月度发票收集整理",
    [string]$At = "09:00",
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$root = Split-Path -Parent $PSScriptRoot
$entry = Join-Path $root "每月整理.cmd"

if ($Unregister) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Output "已删除计划任务：$TaskName"
    } else {
        Write-Output "计划任务不存在：$TaskName"
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $entry)) {
    throw "找不到入口脚本：$entry"
}

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument ('/c ""{0}" --nopause"' -f $entry) -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Monthly -DaysOfMonth 20 -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force -Description "每月 20 日从 QQ 邮箱收集并整理发票，归档到 <归档根目录>" | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Output "已注册计划任务：$($task.TaskName)"
Write-Output "  状态    : $($task.State)"
Write-Output "  触发    : 每月 $($trigger.DaysOfMonth) 日 $At"
Write-Output "  动作    : $($action.Execute) $($action.Arguments)"
Write-Output "  下次运行: $($info.NextRunTime)"
