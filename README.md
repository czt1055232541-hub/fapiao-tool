# 发票工具（fapiao）

每月 20 号从 **QQ 邮箱** 和 **微信** 两个地方收发票，自动认抬头、认用途、按行程归档，
产出能直接交给财务的报销材料。

**数据不出本机**：只读写本机文件、只连一台 IMAP 服务器（默认 `imap.qq.com`），不上传任何内容。

---

## 它能做什么

- 采集：按日期窗口从邮箱拉附件；微信发来的票据走入站目录
- 解析：PDF / XML 双通道，认出发票号、金额、开票日期、销售方、抬头
- 归类：按抬头和用途分目录；差旅费还能把同一趟的票聚成一个行程
- 查重：同一张发票号只计一次；内容相同的图片按 md5 去重
- 存疑：看不出闭环、认不出抬头、图片格式解析不了的，单独进 `99_存疑待人工审计\`，**绝不静默丢**
- 交付：`00_发票审计明细及汇总.xlsx` + 按分类合并的 PDF

---

## 环境要求

| 项 | 要求 |
|---|---|
| 系统 | Windows 10 / 11（用到 PowerShell 计划任务） |
| Python | 3.11 以上（开发环境实测 3.13） |
| 邮箱 | QQ 邮箱，需在网页版开通 IMAP 并生成**授权码** |
| 依赖 | `pip install -r requirements.txt`（PyMuPDF / pypdf / openpyxl） |

---

## 快速开始（新同事照着做）

### 1. 拉代码

```bash
git clone <仓库地址> fapiao-tool
cd fapiao-tool
pip install -r requirements.txt
```

### 2. 建自己的配置

```bash
copy config.example.json config.json
```

编辑 `config.json`，把这几处换成你自己的：

| 字段 | 填什么 |
|---|---|
| `archive.root` | 归档放哪，例如 `D:\\发票归档` |
| `mail.credentialPath` | 邮箱凭据 JSON 的路径（下一步生成） |
| `wechat.enabled` | 不用微信就改成 `false` |
| `wechat.inboundDir` | 用微信才填；入站目录见 `docs/RUNBOOK.md` |
| `classify.buyers` | 报销抬头（名称 + 税号），按你单位的填 |

`config.json` 已在 `.gitignore` 里，不会被提交。

### 3. 准备邮箱凭据

QQ 邮箱网页版 → 设置 → 账号 → 开启「IMAP/SMTP 服务」→ 生成 **16 位授权码**（不是登录密码）。

新建一个 JSON 文件（随便放哪，别提交），内容：

```json
{
  "email": "you@qq.com",
  "authCode": "十六位授权码"
}
```

把它的绝对路径填进 `config.json` 的 `mail.credentialPath`。

### 4. 自检

```bash
python scripts/run_monthly.py --doctor
```

它会打印配置、凭据是否读到、窗口内有多少封邮件，**不写归档**。有问题按提示改。

### 5. 先预演，再真跑

```bash
python scripts/preview.py          # 预演：明细表 + 行程聚类，不落盘
python scripts/run_monthly.py      # 真跑
```

---

## 日常使用

1. 双击 `每月整理.cmd`（默认整理「上月 21 日 ~ 本月 20 日」，邮箱 + 微信一起收）
2. 结果在 `archive.root\{运行日}月度发票归集({总额})\`
3. 先看 `00_发票审计明细及汇总.xlsx`，黄行是存疑 → 去 `99_存疑待人工审计\` 核对
4. 交财务用 `00_汇总发票\` 里按分类合并好的 PDF

**跑完必须复核**（自动跑出来的只是候选结果）：

```bash
python scripts/review_run.py list             # 哪些还没复核
python scripts/review_run.py show 20261020    # 五段复核清单
python scripts/review_run.py approve 20261020 --note "核对结论"
```

### 某张票不该计入

```bash
python scripts/manage_exclusions.py add <发票号码> --reason "交由他人报销"
python scripts/manage_exclusions.py list
```

用于作废票、已由他人报销的票、月中已报销不重复计入的票。
**只排除不删源文件**，每次运行都会在 `00_审计摘要.json -> skipped.excludedByNumber` 留痕。

### 开启每月自动执行

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -DayOfMonth 20 -At 09:00
powershell -ExecutionPolicy Bypass -File scripts\check_task.ps1      # 查状态
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -Unregister
```

任务只在**当前用户已登录**时运行（不存密码）；当天没开机的话，下次登录会补跑。

### 按预约单合并打印件

```bash
python scripts/merge_reimbursement.py "<…\03_已至报销单>" --each-subfolder --dry-run
python scripts/merge_reimbursement.py "<…\03_已至报销单>" --each-subfolder
```

---

## 文档

| 文件 | 作用 | 读者 |
|---|---|---|
| `docs/整理要求.md` | 整理规则与验收标准：分类、命名、去重、存疑、行程推断、统计口径 | 想知道"为什么这么归"的人 |
| `docs/RUNBOOK.md` | 怎么跑、怎么排障、怎么回滚、各种配置项 | 使用者 / 排障 |
| `docs/ARCHITECTURE.md` | 分层、模块边界、数据流、不变量、退出码 | 要改代码的人 |

---

## 开发与回归

```bash
python scripts/check_all.py      # 一条命令跑完整基线（编译 / 单测 / 编码 / 隐私 / 计划任务 / 入口）
python -m unittest discover -s tests -t .
```

`.gitignore` 会把本机的东西挡在版本库外：`config.json`、`exclusions.json`、
`privacy.local.json`（身份类隐私模式）、`data/`、`归档/`、日志。
