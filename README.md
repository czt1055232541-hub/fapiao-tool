# 发票工具（fapiao）

每月 20 号自动从 QQ 邮箱收集发票，按参考案例的整理方式归档成可直接交给财务的报销材料。

**数据不出本机**：工具只在本机读写文件、只连一台 IMAP 服务器（`imap.qq.com`），不上传任何内容。

## 当前状态（验证于 2026-09-18）

| 项 | 实际情况 |
|---|---|
| 代码 | Task 0 骨架建设中，尚无 Python 源码 |
| 数据 | 归档根 `<归档根目录>\`，**不进版本控制** |
| 依赖 | Python 3.13.2 / pypdf / PyMuPDF / openpyxl（pdfplumber 未安装，不使用） |
| 自动化 | 计划任务"每月 20 日"**尚未注册** |
| 回归 | `scripts/check_all.py` 尚未建立 |
| 回滚点 | 见 `PROJECT_STATE.md -> Stages And Rollback Nodes` |

## 文档地图

| 文件 | 作用 | 读者 |
|---|---|---|
| `docs/整理要求.md` | **验收标准**：分类、命名、去重、存疑、统计口径 | 想改行为的人 |
| `docs/GOAL.md` | 目标 + 已验证事实 + 默认决策 + 任务队列 | 接手的 agent |
| `PROJECT_STATE.md` | **唯一进度事实** | 所有人 |
| `docs/ARCHITECTURE.md` | 分层与模块边界 | 改代码的人 |
| `docs/RUNBOOK.md` | 起停、排障、错误码 | 运维/排障 |
| `AGENTS.md` | 开发规则（每轮注入） | agent |

阅读顺序：`AGENTS.md` → `docs/GOAL.md` → `PROJECT_STATE.md` → `git status` → 跑基线。

## 日常使用

双击 `每月整理.cmd`（默认整理"上月21日~本月20日"窗口，并弹出运行结果）。

## 命令行入口

```bash
# 默认：滚动窗口（上月21日~本月20日），真实采集并归档
python scripts/run_monthly.py

# 指定运行日（模拟某次 20 号执行）
python scripts/run_monthly.py --run-date 2026-10-20

# 只解析本地已抓取的暂存目录，不连邮箱（离线重放）
python scripts/run_monthly.py --replay data/staging/20261020

# 预演：连邮箱抓取、算好结果，但不落盘
python scripts/run_monthly.py --dry-run

# 覆盖已存在的运行目录
python scripts/run_monthly.py --force

# 自检（不写归档）
python scripts/run_monthly.py --doctor
```

## 验证

```bash
python scripts/check_all.py      # 单命令跑完整基线；返回非零即失败
```

## 设计原则

1. **XML 优先，PDF 兜底**：银行/税务发的结构化 XML 是权威源；PDF 文字碎片化，只做兜底。
2. **按发票号码去重**：同一张发票在邮件里可能带 2~4 个附件、乃至重复投递，不按附件计数。
3. **宁可存疑，不可猜错**：算不准的一律进 `99_存疑待人工审计` 并写明原因，绝不静默丢掉，也绝不静默算进总额。
4. **三处金额必须对得上**：明细之和 = 分类之和 = 合并 PDF 覆盖金额。
5. **一次运行一个自包含目录**：可单独打包交给财务，也可单独回滚删除。
