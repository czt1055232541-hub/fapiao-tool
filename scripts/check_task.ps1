# 计划任务自检：只报告状态，不改动任何东西；未注册时返回 0 并明确说明。
#
# 用 COM 读而不是 ScheduledTasks 模块：本机该模块读出来的 DaysOfMonth 是空的
# （见 register_task.ps1 顶部第 4/5 条说明）。
param(
    [string]$TaskName = "月度发票收集整理",
    [int]$ExpectDay = 20
)

$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$service = New-Object -ComObject Schedule.Service
$service.Connect()
$folder = $service.GetFolder('\')
try {
    $task = $folder.GetTask($TaskName)
} catch {
    Write-Output "计划任务未注册：$TaskName（尚未启用自动化，或已被删除）"
    exit 0
}

# COM 的 DaysOfMonth 是日期位掩码：位 N-1 置位表示第 N 天。
$mask = [uint32]$task.Definition.Triggers.Item(1).DaysOfMonth
$days = @()
for ($bit = 0; $bit -lt 32; $bit++) {
    if (($mask -band ([uint32]1 -shl $bit)) -ne 0) { $days += ($bit + 1) }
}
$dayText = ($days -join ',')
$action = $task.Definition.Actions.Item(1)

Write-Output "计划任务：$TaskName"
Write-Output "  状态    : $($task.State)  (3 = Ready)"
Write-Output "  触发    : 每月 $dayText 日"
Write-Output "  动作    : $($action.Path) $($action.Arguments)"
Write-Output "  上次运行: $($task.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss'))  结果: $($task.LastTaskResult)"
Write-Output "  下次运行: $($task.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss'))"

$problems = @()
if ($task.State -eq 4) { $problems += "任务处于 Disabled 状态，自动化不会发生" }
if (-not $task.Enabled) { $problems += "任务未启用" }
if ($days -notcontains $ExpectDay) { $problems += "触发日 $dayText 不含期望的 $ExpectDay（位掩码可能被写错）" }
if ($action.Path -notmatch 'cmd') { $problems += "动作不是 cmd.exe：$($action.Path)" }
if ($action.Arguments -notmatch '每月整理\.cmd') { $problems += "动作没有指向 每月整理.cmd：$($action.Arguments)" }
if ($action.Arguments -notmatch '--nopause') { $problems += "动作缺少 --nopause，计划任务会卡在 pause" }
if ($task.NextRunTime.Year -lt 2000) { $problems += "没有算出下次运行时间" }

if ($problems.Count -gt 0) {
    foreach ($item in $problems) { Write-Output "  [计划任务自检] $item" }
    exit 1
}
Write-Output "  计划任务自检通过。"
exit 0
