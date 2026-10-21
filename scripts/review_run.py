r"""归档复核：自动跑完不是终点，过一遍复核才算交付。

    python scripts/review_run.py list                    # 所有归档的复核状态
    python scripts/review_run.py show 20261020月度发票归集(5121.03)   # 复核清单
    python scripts/review_run.py approve 20261020... --note "已核对，存疑 3 条同意留存疑"

规则（docs/整理要求.md §0.7）：
- 计划任务自动产出的归档，\`review.status\` 一律是「待复核」；
- 复核要走一遍会话（把 show 的清单摆出来逐项确认），确认后才 approve；
- **未复核的归档不得据此报销**。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

SUMMARY = "00_审计摘要.json"
DETAIL = "00_审计明细.json"


def _utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def archive_root() -> Path:
    from fapiao import config

    cfg = config.load_config(None)
    return Path((cfg.get("archive") or {}).get("root") or (ROOT / "归档"))


def runs() -> list[Path]:
    root = archive_root()
    if not root.is_dir():
        return []
    return sorted((d for d in root.iterdir() if d.is_dir() and (d / SUMMARY).is_file()),
                  key=lambda p: p.name)


def load(run: Path) -> tuple[dict, list[dict]]:
    summary = json.loads((run / SUMMARY).read_text(encoding="utf-8"))
    detail_file = run / DETAIL
    records = json.loads(detail_file.read_text(encoding="utf-8")) if detail_file.is_file() else []
    return summary, records


def status_of(summary: dict) -> str:
    return str((summary.get("review") or {}).get("status") or "未标注")


def find_run(token: str) -> Path:
    token = token.strip()
    for run in runs():
        if run.name == token or run.name.startswith(token):
            return run
    raise SystemExit(f"找不到归档目录：{token}")


def show(run: Path) -> int:
    summary, records = load(run)
    included = [r for r in records if r.get("included")]
    pending = [r for r in records if not r.get("included")]
    inferred = [r for r in records if r.get("inferred_from")]
    window = summary.get("window") or {}

    print(f"归档目录：{run.name}")
    print(f"复核状态：{status_of(summary)}")
    review = summary.get("review") or {}
    if review.get("at"):
        print(f"  复核于 {review['at']}：{review.get('note', '')}")
    print(f"采集窗口：{window.get('since')} ~ {window.get('before')}（不含）")
    print(f"记录 {len(records)} 条：计入 {len(included)} 张 / {summary.get('included_total', 0):.2f} 元；"
          f"存疑 {len(pending)} 张")
    print()

    print("=== 1. 存疑项（逐条确认是否认可留在存疑）===")
    if not pending:
        print("  无")
    for r in pending:
        print(f"  {r['seq']:>3} {r['amount']:>9.2f} {str(r.get('location'))[:14]:<16} "
              f"{str(r.get('kind')):<8} {'；'.join(r.get('reasons') or [])}")
    print()

    print("=== 2. 推断项（这些是机器推的，重点看）===")
    if not inferred:
        print("  无")
    for r in inferred:
        print(f"  {r['seq']:>3} {r['amount']:>9.2f} {r.get('trip_folder') or r.get('category')}")
        print(f"        {r['inferred_from']}")
    print()

    print("=== 3. 跳过项（每一条都必须有理由）===")
    skipped = summary.get("skipped") or {}
    for key, items in skipped.items():
        if not items:
            continue
        print(f"  {key}：{len(items)} 条")
        for item in items[:6]:
            label = item.get("reason") or item.get("note") or item.get("filename") or ""
            print(f"      - {str(label)[:80]}")
        if len(items) > 6:
            print(f"      …… 还有 {len(items) - 6} 条")
    print()

    print("=== 4. 金额三处一致性 ===")
    detail_total = round(sum(r["amount"] for r in included), 2)
    print(f"  明细自算：{detail_total:.2f}")
    print(f"  摘要记录：{float(summary.get('included_total', 0)):.2f}")
    totals = summary.get("category_totals") or {}
    category_total = round(sum(float(v) for v in totals.values()), 2)
    print(f"  分类汇总：{category_total:.2f}（{len(totals)} 个分类）")
    for name, value in sorted(totals.items()):
        print(f"      {name}：{float(value):.2f}")
    summary_total = float(summary.get("included_total", 0))
    ok = (abs(detail_total - summary_total) < 0.005
          and abs(category_total - summary_total) < 0.005)
    print(f"  -> {'三处一致' if ok else '不一致！'}")
    print()

    print("=== 5. 与上一期的差异 ===")
    previous = [r for r in runs() if r.name < run.name]
    if not previous:
        print("  这是第一份归档")
    else:
        prev = previous[-1]
        _, prev_records = load(prev)
        prev_numbers = {r.get("invoice_number") for r in prev_records if r.get("invoice_number")}
        now_numbers = {r.get("invoice_number") for r in records if r.get("invoice_number")}
        print(f"  上一期：{prev.name}")
        print(f"  本期新增发票 {len(now_numbers - prev_numbers)} 张"
              f"（窗口不重叠时属正常；窗口重叠却新增，说明上次漏采）")
    print()
    print("复核完请执行：")
    print(f'  python scripts/review_run.py approve "{run.name}" --note "核对结论"')
    return 0


def approve(run: Path, note: str, reviewer: str) -> int:
    summary, _ = load(run)
    summary["review"] = {
        "status": "已复核",
        "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "by": reviewer,
        "note": note,
        "rule": (summary.get("review") or {}).get("rule", ""),
    }
    (run / SUMMARY).write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                               encoding="utf-8", newline="")
    print(f"已标记复核完成：{run.name}")
    print(f"  {summary['review']['at']}  {reviewer}  {note}")
    return 0


def main() -> int:
    _utf8()
    parser = argparse.ArgumentParser(description="归档复核")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="列出所有归档的复核状态")
    for name, helptext in (("show", "打印复核清单"), ("approve", "标记已复核")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("run", help="归档目录名（可只给前缀）")
        if name == "approve":
            p.add_argument("--note", default="", help="复核结论")
            p.add_argument("--by", default="会话复核", help="复核人")
    args = parser.parse_args()

    if args.action == "list":
        found = runs()
        if not found:
            print("还没有任何归档")
            return 0
        for run in found:
            summary, records = load(run)
            mark = {"已复核": "[已复核]", "待复核": "[待复核]"}.get(status_of(summary), "[未标注]")
            print(f"  {mark} {run.name:<36} 记录 {len(records):>3}  "
                  f"计入 {float(summary.get('included_total', 0)):>9.2f}")
        pending = [r for r in found if status_of(load(r)[0]) != "已复核"]
        if pending:
            print(f"\n有 {len(pending)} 份归档还没复核 —— 未复核不得据此报销：")
            for run in pending:
                print(f"  {run.name}")
        return 0

    run = find_run(args.run)
    if args.action == "show":
        return show(run)
    return approve(run, args.note, args.by)


if __name__ == "__main__":
    raise SystemExit(main())
