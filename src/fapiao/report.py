"""报告层：00_审计摘要.json + 审计 Excel + 分类合并 PDF。

三处金额必须对得上（明细之和 = 分类之和 = 合并 PDF 覆盖金额），对不上就整次运行失败。
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from .models import Record
from .textutil import safe_name

EXCEL_HEADER = [
    "序号", "抬头", "用途", "行程日期", "地点或路线", "票据类型", "金额", "发票号码",
    "归档状态", "金额口径", "归档发票", "计入总额", "分类", "开支项目", "备注",
]


def summary_payload(
    *,
    run_date: date,
    window: tuple[date, date],
    records: list[Record],
    skipped: dict,
    mail_scanned: int,
    mail_with_attachments: int,
    merged: list[dict],
    include_since_year: int | None,
    wechat_info: dict | None = None,
) -> dict:
    included = [r for r in records if r.included]
    excluded = [r for r in records if not r.included]
    categories: dict[str, list[Record]] = defaultdict(list)
    for record in included:
        categories[record.category].append(record)
    scope = "本次运行采集窗口内的全部发票均计入总额"
    if include_since_year:
        scope = f"仅统计开票/行程日期为 {include_since_year} 年及以后的发票，之前的保留归档但不计入金额与分类汇总 PDF"
    return {
        "统计口径": scope,
        "run_date": run_date.isoformat(),
        "window": {"since": window[0].isoformat(), "before": window[1].isoformat()},
        "mail_scanned": mail_scanned,
        "mail_with_attachments": mail_with_attachments,
        "wechat_source": wechat_info or {"enabled": False},
        "archive_invoice_count": len(records),
        "included_invoice_count": len(included),
        "excluded_invoice_count": len(excluded),
        "included_total": round(sum(r.amount for r in included), 2),
        "excluded_total": round(sum(r.amount for r in excluded), 2),
        "category_totals": {k: round(sum(r.amount for r in v), 2) for k, v in sorted(categories.items())},
        "category_counts": {k: len(v) for k, v in sorted(categories.items())},
        "分类汇总PDF": merged,
        "skipped": skipped,
    }


def _year(record: Record) -> int:
    for value in (record.travel_date, record.issue_date):
        head = (value or "")[:4]
        if head.isdigit():
            return int(head)
    return 0


def write_summary(run_dir: Path, name: str, payload: dict) -> Path:
    path = run_dir / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="")
    return path


def write_records(run_dir: Path, records: list[Record]) -> Path:
    path = run_dir / "00_审计明细.json"
    path.write_text(
        json.dumps([r.to_dict() for r in records], ensure_ascii=False, indent=2),
        encoding="utf-8", newline="",
    )
    return path


def merge_category_pdfs(
    run_dir: Path, records: list[Record], folder_name: str, log=print
) -> list[dict]:
    """按分类合并计入总额的发票（不含行程单），并校验页数。"""
    from pypdf import PdfReader, PdfWriter

    out_dir = run_dir / folder_name
    out_dir.mkdir(parents=True, exist_ok=True)

    grouped: dict[str, list[Record]] = defaultdict(list)
    for record in records:
        if record.included and record.archived_invoice:
            grouped[record.category].append(record)

    manifest: list[dict] = []
    expected: set[str] = set()
    for category in sorted(grouped):
        items = sorted(grouped[category], key=lambda r: (r.travel_date, r.archived_invoice))
        total = round(sum(r.amount for r in items), 2)
        out_path = out_dir / f"{safe_name(category, 80)}-{total:.2f}元.pdf"
        expected.add(out_path.name)

        writer = PdfWriter()
        page_count = 0
        for item in items:
            source = run_dir / item.archived_invoice
            reader = PdfReader(str(source))
            start = page_count
            for page in reader.pages:
                writer.add_page(page)
                page_count += 1
            if len(reader.pages):
                writer.add_outline_item(
                    safe_name(f"{item.travel_date}_{item.amount:.2f}元_{source.stem}", 180), start
                )
        with out_path.open("wb") as handle:
            writer.write(handle)
        check = PdfReader(str(out_path))
        if len(check.pages) != page_count:
            raise RuntimeError(f"合并 PDF 页数校验失败：{out_path} 期望 {page_count} 实得 {len(check.pages)}")
        manifest.append({
            "category": category,
            "invoice_count": len(items),
            "amount": total,
            "page_count": page_count,
            "path": str(out_path),
        })
        log(f"[报告] 合并 {category}：{len(items)} 张 / {total:.2f} 元 / {page_count} 页")

    for stale in out_dir.glob("*.pdf"):
        if stale.name not in expected:
            stale.unlink()

    (out_dir / "合并清单.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline=""
    )
    return manifest


def write_excel(run_dir: Path, name: str, records: list[Record], summary: dict, merged_folder: str) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    path = run_dir / name
    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "汇总"
    audit_sheet = workbook.create_sheet("审计明细")

    dark = PatternFill("solid", fgColor="1F4E78")
    light = PatternFill("solid", fgColor="D9EAF7")
    warn = PatternFill("solid", fgColor="FFF2CC")
    white_bold = Font(name="Microsoft YaHei", size=10, bold=True, color="FFFFFF")
    title_font = Font(name="Microsoft YaHei", size=16, bold=True, color="1F1F1F")
    note_font = Font(name="Microsoft YaHei", size=10, color="666666")
    body_font = Font(name="Microsoft YaHei", size=9)
    thin = Side(style="thin", color="D9E1F2")

    for sheet in (summary_sheet, audit_sheet):
        sheet.sheet_view.showGridLines = False
    summary_sheet.sheet_properties.tabColor = "1F4E78"
    audit_sheet.sheet_properties.tabColor = "5B9BD5"

    summary_sheet["A2"] = f"{summary['run_date'][:4]}年{int(summary['run_date'][5:7])}月 月度发票归集汇总"
    summary_sheet.merge_cells("A2:G2")
    summary_sheet["A2"].font = title_font
    summary_sheet["A3"] = f"统计口径：{summary['统计口径']}。采集窗口 {summary['window']['since']} ~ {summary['window']['before']}（不含）。"
    summary_sheet.merge_cells("A3:G3")
    summary_sheet["A3"].font = note_font

    summary_sheet["A5"], summary_sheet["B5"] = "指标", "结果"
    metrics = [
        ("当前归档发票张数", summary["archive_invoice_count"]),
        ("计入张数", summary["included_invoice_count"]),
        ("待报销总金额（元）", summary["included_total"]),
        ("剔除张数（存疑）", summary["excluded_invoice_count"]),
        ("剔除金额（元）", summary["excluded_total"]),
    ]
    for offset, (label, value) in enumerate(metrics):
        summary_sheet.cell(row=6 + offset, column=1, value=label)
        summary_sheet.cell(row=6 + offset, column=2, value=value)

    for column, header in enumerate(["分类", "计入张数", "计入金额（元）", "合并PDF"], start=4):
        summary_sheet.cell(row=5, column=column, value=header)
    row = 6
    for category, amount in (summary["category_totals"] or {}).items():
        summary_sheet.cell(row=row, column=4, value=category)
        summary_sheet.cell(row=row, column=5, value=summary["category_counts"].get(category, 0))
        summary_sheet.cell(row=row, column=6, value=amount)
        summary_sheet.cell(row=row, column=7, value=f"{category}-{float(amount):.2f}元.pdf")
        row += 1
    summary_sheet.cell(row=row, column=4, value="合计")
    summary_sheet.cell(row=row, column=6, value=summary["included_total"])
    summary_sheet.cell(row=row, column=7, value=f"{len(summary['category_totals'] or {})}份")

    notes_row = row + 2
    summary_sheet.cell(row=notes_row, column=1, value="归档说明")
    summary_sheet.merge_cells(start_row=notes_row, start_column=1, end_row=notes_row, end_column=7)
    notes = [
        ("金额口径", summary["统计口径"]),
        ("存疑处理", f"存疑 {summary['excluded_invoice_count']} 张、{summary['excluded_total']:.2f} 元未计入总额，"
                     f"全部落在 {merged_folder} 同级的「99_存疑待人工审计」目录并注明原因。"),
        ("PDF口径", f"每个分类合并为一份 PDF，仅包含计入总额的发票，不含行程单；文件在「{merged_folder}」。"),
    ]
    for offset, (label, text) in enumerate(notes, start=1):
        summary_sheet.cell(row=notes_row + offset, column=1, value=label)
        summary_sheet.cell(row=notes_row + offset, column=2, value=text)
        summary_sheet.merge_cells(start_row=notes_row + offset, start_column=2,
                                  end_row=notes_row + offset, end_column=7)

    for cell in ("A5", "B5", "D5", "E5", "F5", "G5"):
        summary_sheet[cell].fill = dark
        summary_sheet[cell].font = white_bold
        summary_sheet[cell].alignment = Alignment(horizontal="center", vertical="center")
    for cell in ("A13",):
        summary_sheet[cell].fill = light
        summary_sheet[cell].font = Font(name="Microsoft YaHei", size=11, bold=True)
    for offset in range(5):
        for column in (1, 2):
            summary_sheet.cell(row=6 + offset, column=column).border = Border(bottom=thin)
            summary_sheet.cell(row=6 + offset, column=column).font = body_font
    for r in range(6, row + 1):
        for column in range(4, 8):
            summary_sheet.cell(row=r, column=column).border = Border(bottom=thin)
            summary_sheet.cell(row=r, column=column).font = body_font
    summary_sheet["B8"].number_format = "#,##0.00"
    summary_sheet["B10"].number_format = "#,##0.00"
    for r in range(6, row + 1):
        summary_sheet.cell(row=r, column=6).number_format = "#,##0.00"

    for column, width in zip("ABCDEFG", (28, 18, 3, 28, 15, 18, 42)):
        summary_sheet.column_dimensions[column].width = width

    audit_sheet["A2"] = "发票归档审计明细"
    audit_sheet.merge_cells("A2:O2")
    audit_sheet["A2"].font = title_font
    audit_sheet["A3"] = "黄色行为存疑记录：已归档备查，但不计入报销总额与分类汇总 PDF。"
    audit_sheet.merge_cells("A3:O3")
    audit_sheet["A3"].font = note_font
    for column, header in enumerate(EXCEL_HEADER, start=1):
        audit_sheet.cell(row=4, column=column, value=header)

    for offset, record in enumerate(records):
        r = 5 + offset
        values = [
            record.seq, record.buyer, record.purpose, record.travel_date, record.location,
            record.kind, record.amount, record.invoice_number, record.archive_status,
            record.amount_scope + ("（计入）" if record.included else "（剔除）"),
            record.archived_invoice, "是" if record.included else "否", record.category,
            record.expense_item, record.remark,
        ]
        for column, value in enumerate(values, start=1):
            cell = audit_sheet.cell(row=r, column=column, value=value)
            cell.font = body_font
            if column == 8:
                cell.number_format = "@"
            if not record.included:
                cell.fill = warn
    last_row = 4 + len(records)
    for column in range(1, len(EXCEL_HEADER) + 1):
        cell = audit_sheet.cell(row=4, column=column)
        cell.fill = dark
        cell.font = white_bold
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for r in range(5, last_row + 1):
        audit_sheet.cell(row=r, column=7).number_format = "#,##0.00"
        audit_sheet.cell(row=r, column=8).number_format = "@"
    audit_sheet.freeze_panes = "D5"
    for column, width in zip("ABCDEFGHIJKLMNO",
                             (7, 13, 18, 24, 27, 16, 12, 24, 12, 23, 70, 12, 28, 16, 34)):
        audit_sheet.column_dimensions[column].width = width
    audit_sheet.row_dimensions[4].height = 32

    workbook.save(str(path))
    return path
