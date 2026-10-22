"""复核一个已生成的归档目录是否自洽。

    python scripts/verify_archive.py "<归档根目录>\20260920月度发票归集(9372.25)"

检查项（任一不通过就返回非零）：
  1. 三处金额一致：明细之和 = 分类之和 = 合并 PDF 清单之和 = included_total
  2. 每条计入记录都有真实存在的归档发票（有行程单的也要在）
  3. 合并 PDF 页数 = 该分类发票张数
  4. 分类汇总 PDF 的 path 字段指向真实存在的文件
  5. skipped 里每一条都带原因，没有静默跳过
  6. Excel 两张表、表头列序、行数正确
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EXPECTED_HEADER = [
    "序号", "抬头", "用途", "行程日期", "地点或路线", "票据类型", "金额", "发票号码",
    "归档状态", "金额口径", "归档发票", "计入总额", "分类",
]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    run = Path(sys.argv[1])
    if not run.is_dir():
        print(f"目录不存在：{run}")
        return 2

    problems: list[str] = []
    summary = json.loads((run / "00_审计摘要.json").read_text(encoding="utf-8"))
    records = json.loads((run / "00_审计明细.json").read_text(encoding="utf-8"))
    included = [r for r in records if r["included"]]

    a = round(sum(r["amount"] for r in included), 2)
    b = round(sum(summary["category_totals"].values()), 2)
    c = round(sum(m["amount"] for m in summary["分类汇总PDF"]), 2)
    total = summary["included_total"]
    for label, value in (("明细之和", a), ("分类之和", b), ("合并PDF之和", c)):
        if abs(value - total) != 0:
            problems.append(f"{label} {value} != included_total {total}")

    if summary["included_invoice_count"] != len(included):
        problems.append(f"included_invoice_count {summary['included_invoice_count']} != 实际计入行数 {len(included)}")

    for record in records:
        if record["included"] and not record["archived_invoice"]:
            problems.append(f"序号 {record['seq']} 计入总额却没有归档发票")
        for key in ("archived_invoice", "archived_itinerary", "archived_xml"):
            value = record.get(key) or ""
            if value and not (run / value).is_file():
                problems.append(f"序号 {record['seq']} {key} 指向的文件不存在：{value}")

    for item in summary["分类汇总PDF"]:
        path = Path(item["path"])
        if not path.is_file():
            problems.append(f"分类汇总 PDF 不存在：{path}")
            continue
        if path.parent.name != (run / "00_汇总发票").name and path.parent != run / "00_汇总发票":
            problems.append(f"分类汇总 PDF 不在 00_汇总发票 下：{path}")
        expected_pages = item["invoice_count"] + int(item.get("itinerary_pages") or 0)
        if item["page_count"] != expected_pages:
            problems.append(
                f"{item['category']} 页数 {item['page_count']} != 张数 {item['invoice_count']}"
                f" + 行程单 {int(item.get('itinerary_pages') or 0)}"
            )

    for bucket, items in (summary.get("skipped") or {}).items():
        for entry in items:
            if not entry.get("reason"):
                problems.append(f"skipped.{bucket} 有未说明原因的条目：{entry}")

    excel = run / "00_发票审计明细及汇总.xlsx"
    if not excel.is_file():
        problems.append("缺少 00_发票审计明细及汇总.xlsx")
    else:
        from openpyxl import load_workbook

        workbook = load_workbook(excel)
        if workbook.sheetnames != ["汇总", "审计明细"]:
            problems.append(f"Excel 工作表不是 [汇总, 审计明细]：{workbook.sheetnames}")
        else:
            sheet = workbook["审计明细"]
            header = [sheet.cell(row=4, column=i).value for i in range(1, 14)]
            if header != EXPECTED_HEADER:
                problems.append(f"Excel 表头不符：{header}")
            if sheet.max_row != 4 + len(records):
                problems.append(f"Excel 数据行数 {sheet.max_row - 4} != 记录数 {len(records)}")

    print(f"归档目录：{run}")
    print(f"  归档发票 {summary['archive_invoice_count']} 张；计入 {summary['included_invoice_count']} 张 / {total} 元；"
          f"存疑 {summary['excluded_invoice_count']} 张 / {summary['excluded_total']} 元")
    print(f"  三处金额：明细 {a} / 分类 {b} / 合并PDF {c}")
    print(f"  文件总数：{sum(1 for _ in run.rglob('*') if _.is_file())}")
    if problems:
        print("\n复核未通过：")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("\n复核通过：金额一致、文件齐全、页数匹配、无静默跳过、Excel 结构正确。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
