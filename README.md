# 发票工具（fapiao）

每月 20 号自动从**两个来源**收集发票——QQ 邮箱、以及微信里老板发来的票据——
按参考案例 `<参考案例目录>` 的整理方式，
归档成可直接交给财务的报销材料。

**数据不出本机**：只读写本机文件、只连一台 IMAP 服务器（`imap.qq.com`），不上传任何内容。

## 当前状态（验证于 2026-10-06，v1.2）

| 项 | 实际情况 |
|---|---|
| 代码 | v1.2；`src/fapiao/` 9 个模块 + `scripts/` 6 个脚本 + `tests/` 8 个测试文件 |
| 数据 | 归档在 `<归档根目录>\`，与仓库物理分离，**不进版本控制** |
| 真实产出 | `归档\20260920月度发票归集(9372.25)\`：52 张发票 / 计入 50 张 / 9372.25 元 / 存疑 2 张 |
| 依赖 | Python 3.13.2 / PyMuPDF / pypdf / openpyxl（pdfplumber 未安装，不使用） |
| 自动化 | 计划任务「月度发票收集整理」**已注册**：Ready，每月 20 日 09:00，**下次运行 2026-10-20 09:00**；已手动触发实测跑通（`LastTaskResult=0x0`） |
| 微信来源 | **已接入**：只收 `assets/inbound/` 里的 `.pdf`，图片不收只留痕，同内容按 md5 去重。实测该目录窗口内 10 个文件 → 2 张发票 + 6 条留痕 + 2 条语音计数 |
| 回归 | `python scripts/check_all.py` = 7 项全绿；81 个离线单测（零网络零费用） |
| 归档复核 | `python scripts/verify_archive.py "<归档目录>"` = 6 类检查通过 |
| 回滚点 | `stage-full-pipeline` / `stage-real-mail-integration` / `stage-v1.0-real-archive` / `stage-v1.1-automation-live` / `stage-v1.2-wechat-source`（见 `PROJECT_STATE.md`） |

## 文档地图

| 文件 | 作用 | 读者 |
|---|---|---|
| `docs/整理要求.md` | **验收标准**：分类、命名、去重、存疑、统计口径 | 想改行为的人 |
| `docs/RUNBOOK.md` | 怎么跑、怎么启用自动化、怎么排障、怎么回滚 | 使用者 / 排障 |
| `docs/ARCHITECTURE.md` | 分层、模块边界、数据流、不变量、退出码 | 改代码的人 |
| `docs/GOAL.md` | 目标 + 已验证事实 + 默认决策 | 接手的 agent |
| `PROJECT_STATE.md` | **唯一进度事实** | 所有人 |
| `AGENTS.md` | 开发规则与 20 条环境坑位（每轮注入） | agent |

阅读顺序：`AGENTS.md` → `docs/GOAL.md` → `PROJECT_STATE.md` → `git status` → 跑基线。

## 日常使用

1. 双击 `每月整理.cmd`（默认整理"上月21日~本月20日"窗口，**邮箱 + 微信两个来源一起收**）。
   - 老板把发票丢进微信对话即可；桥接会自动落到 `<微信入站目录>\`
   - 微信来源**只认 PDF**；发图片或 OFD 不会被归档，只在 `00_审计摘要.json` 的 `skipped.wechat` 里留痕
2. 结果在 `<归档根目录>\{运行日}月度发票归集({总额})\`。
3. 先看 `00_发票审计明细及汇总.xlsx`；黄行是存疑，去 `99_存疑待人工审计\` 核对。
4. 交给财务用 `00_汇总发票\` 里的分类合并 PDF（按分类各一份，不含行程单）。

**自动化已启用**：计划任务「月度发票收集整理」每月 20 日 09:00 自动执行 `每月整理.cmd`。
改时间或删除：

```powershell
cd "<工具根目录>"
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -DayOfMonth 25 -At 09:30
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -Unregister
powershell -ExecutionPolicy Bypass -File scripts\check_task.ps1     # 查状态
```

> 任务只在**当前用户已登录**时运行（不存储密码）。20 号当天没开机/没登录的话，
> 下次登录后由「错过就补跑」（`StartWhenAvailable`）补上。

## 命令行入口

```bash
python scripts/run_monthly.py                     # 真实运行（最近一个完整窗口）
python scripts/run_monthly.py --run-date 2026-10-20   # 模拟某次 20 号执行
python scripts/run_monthly.py --dry-run           # 连邮箱算结果，但不落盘
python scripts/run_monthly.py --doctor            # 自检：配置 + 凭据 + 窗口邮件数
python scripts/run_monthly.py --replay data/staging/20260920   # 离线重放，不连邮箱
python scripts/run_monthly.py --force             # 覆盖已存在的运行目录

python scripts/preview.py --run-date 2026-10-20   # 预演：明细表 + 行程聚类，不写盘
python scripts/check_all.py                       # 验证基线（7 项）
python scripts/verify_archive.py "<归档目录>"      # 复核某份归档是否自洽
```

## 设计原则

1. **XML 优先，PDF 兜底**：税务/服务商给的结构化 XML 是权威源；PDF 文字碎片化，只做兜底。
2. **按发票号码去重**：同一张发票在邮件里可能带 2~4 个附件、乃至重复投递；不按附件计数。
3. **宁可存疑，不可猜错**：算不准的一律进 `99_存疑待人工审计\` 并写明原因，绝不静默丢掉，也绝不静默算进总额。
4. **三处金额必须对得上**：明细之和 = 分类之和 = 合并 PDF 覆盖金额，对不上就整次运行失败。
5. **一次运行一个自包含目录**：可单独打包交给财务，也可单独回滚删除。
6. **邮箱只读**：永不删信、永不移动、永不标已读。
