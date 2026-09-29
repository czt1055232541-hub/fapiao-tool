# 计划任务自检：只报告状态，不改动任何东西；未注册时返回 0 并明确说明。
#
# 注意：本文件必须以 UTF-8 **带 BOM** 保存。Windows PowerShell 5.1 在没有 BOM 时
# 会按系统 ANSI 代码页读取脚本，中文参数默认值会被解码成乱码并导致参数绑定失败
# （实测报 "Cannot validate argument on parameter 'TaskName'. The argument is null"）。
param([string]$TaskName = "月度发票收集整理")

$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Output "计划任务未注册：$TaskName（尚未启用自动化，或已被删除）"
    exit 0
}

$trigger = $task.Triggers | Select-Object -First 1
$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Output "计划任务：$($task.TaskName)"
Write-Output "  状态    : $($task.State)"
Write-Output "  触发    : 每月 $($trigger.DaysOfMonth) 日 $($trigger.StartBoundary)"
Write-Output "  动作    : $($task.Actions[0].Execute) $($task.Actions[0].Arguments)"
Write-Output "  上次运行: $($info.LastRunTime)  结果: $($info.LastTaskResult)"
Write-Output "  下次运行: $($info.NextRunTime)"
if ($task.State -eq "Disabled") {
    Write-Output "  [警告] 任务处于 Disabled 状态，自动化不会发生。"
    exit 1
}
exit 0
