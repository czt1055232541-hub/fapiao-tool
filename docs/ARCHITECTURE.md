# 架构

## 分层

```
每月整理.cmd          用户双击入口（CRLF + 纯 ASCII；只做 chcp/参数处理/暂停）
  └─ scripts/run_monthly.py    薄包装，把 src/ 加进 sys.path
       └─ src/fapiao/cli.py    argparse、日志留存、退出码约定
            └─ src/fapiao/pipeline.py   编排：采集 → 解析 → 归类 → 归档 → 报告 → 自检
                 ├─ config.py     配置装载 + 凭据解析（授权码只读自 DSH 配置库）
                 ├─ exclusions.py 排除清单（按发票号码：作废 / 交由他人报销 / 月中已报销）
                 ├─ textutil.py   命名、金额、日期、城市、项目名等纯函数
                 ├─ mailfetch.py  IMAP 窗口采集 + 附件按基名分组
                 ├─ wechatfetch.py 微信入站目录采集（第二个来源，产出同构 Group）
                 ├─ extract.py    XML 优先 / PDF(PyMuPDF) 兜底 → Meta / ItineraryMeta
                 ├─ classify.py   去重 / 抬头 / 用途 / 行程聚类 / 存疑判定
                 ├─ archive.py    目录与命名落盘（复刻参考案例约定）
                 └─ report.py     审计 JSON + 双 sheet Excel + 分类合并 PDF
```

## 模块边界

| 模块 | 负责 | 不负责 |
|---|---|---|
| `config` | 读 config.json、从 dsh-qqmail.json 取凭据、抬头表 | 任何 IO 语义判断 |
| `textutil` | 纯函数：命名清洗、金额/日期/城市正则 | 读文件、连网络 |
| `mailfetch` | IMAP 只读采集、附件落盘、分组、manifest 读写 | 解析发票内容 |
| `wechatfetch` | 扫微信入站目录、窗口过滤、扩展名策略、md5 去重、产出同构 Group | 解析发票内容、决定归档位置 |
| `exclusions` | 读写排除清单（号码 + 原因） | 决定排除后怎么记账（由 pipeline 落进 skipped） |
| `extract` | 从 XML / PDF 文本里抽字段 | 判断抬头归属、决定目录 |
| `classify` | 规则：抬头/用途/类型/行程/存疑/去重 | 落盘、写报告 |
| `archive` | 目录与文件名、幂等闸门、复制文件、执行日合并同窗口旧目录 | 决定记录该去哪（由 classify 的原因决定） |
| `report` | 摘要 JSON、Excel、合并 PDF、金额一致性自检 | 改记录内容 |

规则本身写在 `docs/整理要求.md`，代码只实现规则。改规则 = 改那份文档 + 改 `classify`。

## 数据流

```
来源一：IMAP INBOX                    来源二：微信入站目录 assets\inbound\
  │  SINCE/BEFORE 裸原子日期窗口        │  文件名日期前缀落窗 + md5 去重 + 只收 PDF
  │  （绝不能加括号）                   │  图片不收（留痕）、silk 等只计数
  └────────────────┬───────────────────┘
                   ▼
data/staging/{YYYYMMDD}/  附件 + manifest.json（含 preview，供 --replay 复算；只覆盖邮箱来源）
  │  邮箱：按基名分组（PDF 作载体，XML/OFD 作同伴，行程单另挂）
  │  微信：一个 PDF 就是一组
  │  两来源合并时邮箱排在前面 —— 同一张票两边都来时，去重保留先到的邮箱记录
  │  （邮箱有正文，认得出住宿城市与入住日期；微信没有正文）
  ▼
Record 列表（每条 = 一张发票）
  │  去重主键 = 发票号码；无号码退化为 (金额, 开票日期, 销售方)
  │  抬头 ← 税号优先，其次关键词；用途 ← 票据类型 × 城市
  │  差旅费按 (城市, 日期间隔 ≤ tripGapDays) 聚成行程
  │  任何说不准的地方 → 存疑原因（宁可存疑，不可猜错）
  ▼
<归档根目录>\{YYYYMMDD}月度发票归集({总额})/
  ├─ 00_审计摘要.json / 00_审计明细.json / 00_发票审计明细及汇总.xlsx
  ├─ 00_汇总发票/{分类}-{金额}元.pdf + 合并清单.json
  ├─ 01_{抬头}明细/{01_差旅费,02_招待费,03_杭州市内打车票}/...
  ├─ 99_存疑待人工审计/{原因}/...
  └─ _原始数据/*.xml
```

## 不变量（违反即为缺陷）

1. **金额三处一致**：计入明细之和 = 分类之和 = 合并 PDF 覆盖金额 = `included_total`。
   `pipeline.verify_run` 在每次运行末尾强制校验，不一致直接抛错、不产出报告。
2. **只读邮箱**：`IMAP4.select(readonly=True)`，永不删除/移动/标已读。
3. **不静默跳过**：每个被跳过的附件都要带原因进 `00_审计摘要.json -> skipped`。
4. **幂等**：同名的运行目录已存在且非空时拒绝覆盖（`--force` 才允许）。
5. **归档根在仓库之外**：`归档\` 与 `发票工具\` 是同级目录，数据永远不进 git。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功（含 `--dry-run` 预演成功） |
| 1 | 基线 `check_all.py` 有检查项失败 |
| 2 | 运行失败：运行目录已存在、归档自检不通过、凭据/配置/暂存缺失 |

## 关键第三方依赖

| 库 | 用途 | 为什么是它 |
|---|---|---|
| PyMuPDF(`fitz`) | 主 PDF 文本提取 | pypdf 在携程火车票 PDF 上抛 `More than one /FontFile found` |
| pypdf | 合并分类 PDF、页数校验 | 只做页级搬运与校验，不做文本抽取 |
| openpyxl | 生成审计 Excel | 需要数字格式、冻结窗格、条件填色 |
