# 运行手册

## 日常：每月 20 号

### 自动（推荐，需先注册计划任务）

计划任务「月度发票收集整理」在每月 20 日 09:00 触发 `每月整理.cmd --nopause`。

**当前状态：任务尚未注册**（本机 DSH 沙箱拒绝写任务计划程序存储，见下方「启用自动化」）。

### 手动

双击 `每月整理.cmd`。窗口会显示全过程，结束时停留等你按键。

## 命令一览

```bash
python scripts/run_monthly.py                     # 真实运行（最近一个完整窗口）
python scripts/run_monthly.py --run-date 2026-10-20   # 模拟某次 20 号执行
python scripts/run_monthly.py --dry-run           # 连邮箱算结果，但不落盘
python scripts/run_monthly.py --doctor            # 自检：配置 + 凭据 + 窗口邮件数
python scripts/run_monthly.py --replay data/staging/20260920   # 离线重放，不连邮箱
python scripts/run_monthly.py --force             # 覆盖已存在的运行目录（先清空）

python scripts/preview.py --run-date 2026-10-20   # 预演：打印明细表 + 行程聚类，不写盘
python scripts/preview.py --replay data/staging/20260920

python scripts/check_all.py                       # 验证基线（7 项）
python scripts/check_privacy.py                   # 只看隐私闸门
powershell -ExecutionPolicy Bypass -File scripts/check_task.ps1   # 只看计划任务状态
```

## 启用自动化

沙箱内无法注册计划任务（`New-ScheduledTaskAction` / `schtasks /create` 均被拒绝）。
请在**普通 PowerShell 窗口**里手动执行一次（若提示权限不足，用「以管理员身份运行」）：

```powershell
cd "<工具根目录>"
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
```

可选参数：`-At 09:30` 改时间、`-TaskName "别的名字"`、`-Unregister` 删除。

注册后确认：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\check_task.ps1
```

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
