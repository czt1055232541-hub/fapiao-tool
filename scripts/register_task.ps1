# 注册/更新"每月 N 日"执行发票收集的计划任务。
#
#   powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -DayOfMonth 25 -At 09:30
#   powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -Unregister
#
# 任务以当前用户身份运行、仅在用户登录时执行（InteractiveToken，不存储密码）。
#
# 五条必须记住的坑（全部实测踩过，见 AGENTS.md 环境坑位 12/13/14/15/16）：
#   1. 本文件必须存成 UTF-8 **带 BOM**：Windows PowerShell 5.1 无 BOM 时按 ANSI 读脚本，
#      中文默认值会乱码，报 "Cannot validate argument on parameter 'TaskName'"。
#   2. New-ScheduledTaskTrigger **没有 -Monthly 参数**。
#   3. 自己造 MSFT_TaskMonthlyTrigger 也走不通：逐属性赋值报 "adapter cannot set DaysOfMonth"；
#      构造时用 -Property 给全能建出来，但 Register-ScheduledTask -Trigger 报 PSTypeNames
#      不匹配 CimInstance#MSFT_TaskTrigger，手动 Insert 基类型名后仍失败。
#   4. 本机 ScheduledTasks 模块的**写入路径整体不可用**：Register-ScheduledTask 无论
#      -Action/-Trigger 还是 -Xml，一律 "Unspecified error" (0x80004005)；
#      schtasks.exe 直接被拒绝启动 (Access is denied)。但底层 COM (Schedule.Service)
#      可用 —— 所以这里走 COM。
#   5. **COM 的 IMonthlyTrigger.DaysOfMonth 是"日期位掩码"，不是日期号**：
#      直接写 20 会被当成 0b10100 = 第 3 天和第 5 天，任务实际跑到 10 月 3 日（已实测误触发）。
#      正确做法是设 ITaskDefinition.XmlText（XML 里 <Day>20</Day> 才是日期号），
#      存完再读回来是 524288 = 1 shl 19 = 第 20 天。
param(
    [string]$TaskName = "月度发票收集整理",
    [int]$DayOfMonth = 20,
    [string]$At = "09:00",
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$root = Split-Path -Parent $PSScriptRoot
$entry = Join-Path $root "每月整理.cmd"

$service = New-Object -ComObject Schedule.Service
$service.Connect()
$folder = $service.GetFolder('\')

if ($Unregister) {
    try {
        $folder.DeleteTask($TaskName, 0)
        Write-Output "已删除计划任务：$TaskName"
    } catch {
        Write-Output "计划任务不存在或删除失败：$TaskName（$($_.Exception.Message)）"
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $entry)) {
    throw "找不到入口脚本：$entry"
}
if ($DayOfMonth -lt 1 -or $DayOfMonth -gt 31) {
    throw "DayOfMonth 必须在 1..31 之间，实际：$DayOfMonth"
}

function Get-XmlEscaped([string]$value) {
    return $value.Replace('&', '&amp;').Replace('<', '&lt;').Replace('>', '&gt;').Replace('"', '&quot;')
}

$actionArgs = '/c ""' + $entry + '" --nopause"'
$description = '每月 ' + $DayOfMonth + ' 日从 QQ 邮箱收集并整理发票，归档到 <归档根目录>'
$startBoundary = (Get-Date -Date $At -Format "yyyy-MM-dd'T'HH:mm:ss")

# 单引号 here-string：不做 PowerShell 变量插值，避免路径里的 $ 被吃掉。
$template = @'
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>__DESCRIPTION__</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>__START__</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByMonth>
        <DaysOfMonth><Day>__DAY__</Day></DaysOfMonth>
        <Months>
          <January /><February /><March /><April /><May /><June />
          <July /><August /><September /><October /><November /><December />
        </Months>
      </ScheduleByMonth>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>cmd.exe</Command>
      <Arguments>__ARGS__</Arguments>
      <WorkingDirectory>__WORKDIR__</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
'@

$xml = $template.Replace('__DESCRIPTION__', (Get-XmlEscaped $description))
$xml = $xml.Replace('__START__', $startBoundary)
$xml = $xml.Replace('__DAY__', [string]$DayOfMonth)
$xml = $xml.Replace('__ARGS__', (Get-XmlEscaped $actionArgs))
$xml = $xml.Replace('__WORKDIR__', (Get-XmlEscaped $root))

$definition = $service.NewTask(0)
$definition.XmlText = $xml
$folder.RegisterTaskDefinition($TaskName, $definition, 6, $null, $null, 3) | Out-Null

# 从全新连接读回，确认触发日真的是 $DayOfMonth，而不是位掩码算出来的别的日子。
$check = New-Object -ComObject Schedule.Service
$check.Connect()
$task = $check.GetFolder('\').GetTask($TaskName)
$mask = [uint32]$task.Definition.Triggers.Item(1).DaysOfMonth
$days = @()
for ($bit = 0; $bit -lt 32; $bit++) {
    if (($mask -band ([uint32]1 -shl $bit)) -ne 0) { $days += ($bit + 1) }
}
$dayText = ($days -join ',')

Write-Output "已注册计划任务：$TaskName"
Write-Output "  状态    : $($task.State)  (3 = Ready)"
Write-Output "  触发    : 每月 $dayText 日 $At"
Write-Output ('  动作    : cmd.exe /c ' + $actionArgs)
Write-Output "  下次运行: $($task.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss'))"

if ($days -notcontains $DayOfMonth) {
    Write-Output "  [失败] 读回的触发日 $dayText 与期望的 $DayOfMonth 不一致"
    exit 1
}
