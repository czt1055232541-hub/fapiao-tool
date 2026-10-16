# 运行手册

## 日常：每月 20 号

### 自动（推荐，需先注册计划任务）

计划任务「月度发票收集整理」在**每月 20 日 09:00** 触发 `每月整理.cmd --nopause`。

**当前状态（2026-09-29 核实）：已注册**，状态 Ready，下次运行 **2026-10-20 09:00**。
已用 COM 手动触发跑过一次，`LastTaskResult = 0x0`，日志见 `data/logs/20260929.log`。

### 手动

双击 `每月整理.cmd`。窗口会显示全过程，结束时停留等你按键。

### 两个来源一起收

| 来源 | 位置 | 说明 |
|---|---|---|
| 邮箱 | QQ 邮箱 INBOX | 窗口内邮件的发票附件 |
| **微信** | `<微信入站目录>\` | 老板丢进微信对话的票据，由桥接自动落盘 |

微信来源的规则：**只收 `.pdf`**；图片不收（只留痕）；OFD/XML 不收；同内容按 md5 去重；
`.silk` 语音只记计数。窗口按**文件名日期前缀**判定（`2026-10-06_...`），无前缀退回文件修改时间。

看它会收什么、不收什么，跑一次预演最直观：

```bash
python scripts/preview.py --run-date 2026-10-20    # 会逐条打印微信入站目录的"跳过留痕"
```

## 命令一览

```bash
python scripts/run_monthly.py                     # 真实运行（最近一个完整窗口）
python scripts/run_monthly.py --run-date 2026-10-20   # 模拟某次 20 号执行
python scripts/run_monthly.py --dry-run           # 连邮箱算结果，但不落盘
python scripts/run_monthly.py --doctor            # 自检：配置 + 凭据 + 窗口邮件数
python scripts/run_monthly.py --replay data/staging/20260920   # 离线重放，不连邮箱（不含微信来源）
python scripts/run_monthly.py --force             # 覆盖已存在的运行目录（先清空）
python scripts/run_monthly.py --no-wechat         # 本次只收邮箱，不收微信
python scripts/run_monthly.py --wechat-dir "E:\别的目录"   # 临时换微信入站目录
python scripts/run_monthly.py --monthly           # 强制按"每月执行日"归档（名字带总额）
python scripts/run_monthly.py --mid-month         # 强制按"月中"归档（名字不带总额）

python scripts/manage_exclusions.py list                                      # 排除清单
python scripts/manage_exclusions.py add <发票号码> --reason "交由他人报销"      # 排除某张票
python scripts/manage_exclusions.py remove <发票号码>

python scripts/preview.py --run-date 2026-10-20   # 预演：打印明细表 + 行程聚类，不写盘
python scripts/preview.py --replay data/staging/20260920

python scripts/check_all.py                       # 验证基线（7 项）
python scripts/check_privacy.py                   # 只看隐私闸门
python scripts/verify_archive.py "<归档根目录>\20260920月度发票归集(9372.25)"   # 复核某份归档是否自洽
powershell -ExecutionPolicy Bypass -File scripts/check_task.ps1   # 只看计划任务状态
```

## 按预约报销单合并打印件

月度归档完成后，你会把发票分配到各张预约单下（`03_已至报销单\预约号-xxxxx…\`）。
要打印时，把每张预约单名下的发票与行程单各合并成一份 PDF：

```bash
# 先看会合并什么（不写盘）
python scripts/merge_reimbursement.py "<…\03_已至报销单>" --each-subfolder --dry-run

# 真合并
python scripts/merge_reimbursement.py "<…\03_已至报销单>" --each-subfolder
```

- 输出到 `<03_已至报销单>\_打印用合并PDF\`，命名 `{预约号}_{金额}_发票与行程单.pdf`
- **预约单本身不会被合并进去**（它是要跟附件一起交的另一份文件）；用 `--exclude 前缀` 可以再排除别的
- 排序按文件名开头的三位序号，所以每张发票后面紧跟着它自己的行程单
- 每份源文件加一个书签；合并后校验页数 = 各源文件页数之和，对不上就报错且不留半成品

## 归档目录命名与执行日合并

| 什么时候跑 | 目录名 | 说明 |
|---|---|---|
| 月中（运行日 < 20） | `{运行日}月度发票归集` | 不带总额 —— 这个月后面还会进票 |
| 每月执行日（运行日 ≥ 20） | `{运行日}月度发票归集({计入总额})` | 总额已确定 |

执行日那趟跑完会**自动合并同采集窗口的月中临时目录**：只有当月中目录里的每张发票都能在新目录里
按发票号码找到时才删它，少一张就保留并在日志里报告。不想要这个行为就在 `config.json` 里把
`archive.consolidateOnMonthlyRun` 设为 `false`。

## 某张票不该计入

```bash
python scripts/manage_exclusions.py add 25495606207001213771 --reason "交由他人报销" --note "..."
```

用于：作废/开错的票、已交由他人报销的票、月中已经报销过、月末不重复计入的票。

**只排除，不删源文件**；理由是必填项；每次运行都会写进 `00_审计摘要.json -> skipped.excludedByNumber`
（含金额、原因、备注、来源路径）。

## 自动化（已启用）

注册 / 改时间 / 删除：

```powershell
cd "<工具根目录>"
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1                  # 每月 20 日 09:00
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -DayOfMonth 25 -At 09:30
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -Unregister
```

确认状态（状态、触发日、动作、下次运行，任一不对就返回非零）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\check_task.ps1
```

> **为什么脚本里用 COM 而不是 `Register-ScheduledTask`**：本机 `ScheduledTasks` 模块的写入路径
> 一律返回 `Unspecified error (0x80004005)`，`schtasks.exe /create` 直接被拒绝启动，
> `New-ScheduledTaskTrigger` 又没有 `-Monthly`。细节与证据见 `AGENTS.md` 环境坑位 13–16。
>
> 另外：**注册后第一次想验证它真的能跑**，别在 DSH 工具调用里触发后马上返回——
> 调用结束会清掉该调用期间启动的进程树，任务会跑一半被杀（`AGENTS.md` 环境坑位 17）。

## 排障

| 症状 | 根因 | 处理 |
|---|---|---|
| `[失败] 运行目录已存在且非空` | 幂等闸门生效，该月已经跑过 | 确认要重建才加 `--force`；否则直接看已有目录 |
| `未能解析邮箱凭据` | DSH 里的 qqmail 配置丢失或没填授权码 | 用 dsh-qqmail 插件重新 `qqmail_config`（email + 16 位授权码） |
| `IMAP 连接: [失败]` | 网络、授权码失效、或 QQ 关闭了 IMAP 服务 | 先 `--doctor`；确认邮箱网页版「设置→账号→IMAP/SMTP服务」已开启 |
| 窗口内邮件数明显偏大（几千封） | IMAP 日期条件被服务器忽略 | 检查 `mailfetch.search_uids` 是否被改成了带括号的条件 |
| 某张发票进了存疑 | 见 `00_审计摘要.json -> 存疑原因` 与 Excel 黄行 | 人工核对后手动搬到对应分类目录 |
| 中文在控制台里是乱码 | 控制台代码页不是 UTF-8 | 用 `每月整理.cmd`（内部已 `chcp 65001`），别直接 `python` 双击 |
| 合并 PDF 页数校验失败 | 源 PDF 损坏 | 看报错里指出的文件，重新从邮箱取一次 |

## 日志与暂存

| 路径 | 内容 | 会不会进 git |
|---|---|---|
| `data/logs/{YYYYMMDD}.log` | 每次运行的完整输出 | 不会（已 gitignore） |
| `data/staging/{YYYYMMDD}/` | 抓下来的附件 + `manifest.json` | 不会 |
| `data/_test_tmp/` | 单测临时目录 | 不会 |
| `D:\...\发票报销\归档\` | 归档产物（唯一真实副本） | 不在仓库内 |

暂存可以随时删；删掉之后 `--replay` 就不可用（要重新连邮箱采集）。

## 还原与回滚

- **某次运行生成错了**：直接删掉那个运行目录（一个目录 = 一次运行，自包含）。
- **代码改坏了**：`git tag -n1` 看回滚点，`git checkout <tag>` 只读查看，`git revert` 或 `git reset` 回退。
- **误删归档**：归档唯一的副本就在归档目录，没有第二份；删前先复制到别处。
