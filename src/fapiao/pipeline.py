"""编排层：采集 → 解析 → 归类 → 归档 → 报告，并把每一步的跳过项留痕。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from . import archive, classify, config, mailfetch, report
from .models import Group, Record
from .textutil import month_range


@dataclass
class RunResult:
    run_dir: Path | None
    summary: dict
    records: list[Record] = field(default_factory=list)
    skipped: dict = field(default_factory=dict)


def staging_root() -> Path:
    return config.PROJECT_ROOT / "data" / "staging"


def run(
    cfg: dict,
    run_date: date,
    *,
    replay_dir: Path | None = None,
    dry_run: bool = False,
    force: bool = False,
    since: date | None = None,
    before: date | None = None,
    root_override: Path | None = None,
    log=print,
) -> RunResult:
    mail_cfg = cfg.get("mail") or {}
    archive_cfg = cfg.get("archive") or {}
    lookback = int(mail_cfg.get("extraLookbackDays") or 0)
    window = (since, before) if (since and before) else month_range(run_date, lookback)
    run_key = run_date.strftime("%Y%m%d")

    # ---------- 1. 采集 ----------
    groups: list[Group] = []
    mail_scanned = 0
    mail_with_attachments = 0
    if replay_dir is not None:
        log(f"[重放] 从本地暂存目录读取：{replay_dir}")
        groups, mail_scanned, mail_with_attachments = mailfetch.load_manifest(Path(replay_dir))
    else:
        cred = config.resolve_credentials(cfg)
        log(f"[采集] 使用凭据：{cred.redacted()}")
        staging = staging_root() / run_key
        groups = mailfetch.collect(
            cred,
            window[0],
            window[1],
            mail_cfg.get("mailbox") or "INBOX",
            staging,
            max_mails=int(mail_cfg.get("maxMailsPerRun") or 3000),
            log=log,
        )
        manifest = json.loads((staging / mailfetch.MANIFEST_NAME).read_text(encoding="utf-8"))
        mail_scanned = manifest.get("mail_count", 0)
        mail_with_attachments = sum(1 for m in manifest.get("mails", []) if any(a["path"] for a in m["attachments"]))
        groups, _, _ = mailfetch.load_manifest(staging)

    # ---------- 2. 解析 + 3. 归类 ----------
    records: list[Record] = []
    orphan_itineraries: list[tuple[str, str]] = []
    skipped = {"mails": [], "attachments": [], "duplicates": [], "orphan_itineraries": []}

    for group in groups:
        if group.invoice is None:
            for att in group.itineraries:
                skipped["orphan_itineraries"].append({
                    "mail_uid": group.mail.uid,
                    "subject": group.mail.subject,
                    "filename": att.filename,
                    "reason": classify.R_ORPHAN_ITINERARY,
                })
                if att.path:
                    orphan_itineraries.append((att.path, att.filename))
            continue
        record, _meta, extra = classify.prepare(group, cfg)
        records.append(record)
        for item in extra:
            skipped["attachments"].append({
                "mail_uid": item.mail_uid, "subject": item.subject,
                "filename": item.filename, "reason": item.reason,
            })
        for att in group.companions:
            if att.role == "ofd" and not archive_cfg.get("keepOfd"):
                skipped["attachments"].append({
                    "mail_uid": group.mail.uid, "subject": group.mail.subject,
                    "filename": att.filename, "reason": "OFD 版式文件按配置忽略（PDF 已归档）",
                })

    kept, duplicates = classify.dedupe(records)
    for dup in duplicates:
        dup.reasons = [classify.R_DUPLICATE]
        dup.archive_status = "重复备查"
        skipped["duplicates"].append({
            "invoice_number": dup.invoice_number,
            "amount": dup.amount,
            "subject": dup.mail_subject,
            "reason": classify.R_DUPLICATE,
        })
    log(f"[归类] 发票 {len(records)} 张：去重保留 {len(kept)}、重复 {len(duplicates)}、"
        f"孤儿行程单 {len(orphan_itineraries)}")

    clusters = classify.cluster_trips(kept, cfg)
    classify.apply_trips(kept, clusters, cfg)
    all_records = kept + duplicates
    classify.finalize(all_records, cfg)
    _apply_year_scope(all_records, archive_cfg.get("includeSinceYear"))
    _stamp_scope(all_records)

    included = [r for r in all_records if r.included]
    included_total = round(sum(r.amount for r in included), 2)
    log(f"[归类] 计入 {len(included)} 张 / {included_total:.2f} 元；存疑 {len(all_records) - len(included)} 张")

    # ---------- 预演 ----------
    if dry_run:
        log("[预演] --dry-run：不落盘。")
        summary = report.summary_payload(
            run_date=run_date, window=window, records=all_records, skipped=skipped,
            mail_scanned=mail_scanned, mail_with_attachments=mail_with_attachments,
            merged=[], include_since_year=archive_cfg.get("includeSinceYear"),
        )
        return RunResult(run_dir=None, summary=summary, records=all_records, skipped=skipped)

    # ---------- 4. 归档 ----------
    root = root_override or Path(archive_cfg.get("root") or (config.PROJECT_ROOT / "归档"))
    name = archive.run_folder_name(
        archive_cfg.get("folderTemplate") or "{runDate}月度发票归集({total:.2f})",
        run_date, included_total,
    )
    run_dir = archive.prepare_run_dir(root, name, force)
    log(f"[归档] 输出目录：{run_dir}")

    for record in all_records:
        archive.place(record, run_dir, cfg)
    for source_path, filename in orphan_itineraries:
        archive.place_orphan_itinerary(run_dir, source_path, filename)

    merged_folder = archive.merged_folder_name(cfg, archive_cfg.get("includeSinceYear"))
    merged = report.merge_category_pdfs(run_dir, all_records, merged_folder, log=log)
    summary = report.summary_payload(
        run_date=run_date, window=window, records=all_records, skipped=skipped,
        mail_scanned=mail_scanned, mail_with_attachments=mail_with_attachments,
        merged=merged, include_since_year=archive_cfg.get("includeSinceYear"),
    )
    report.write_summary(run_dir, (cfg.get("report") or {}).get("summaryName") or "00_审计摘要.json", summary)
    report.write_records(run_dir, all_records)
    report.write_excel(
        run_dir, (cfg.get("report") or {}).get("excelName") or "00_发票审计明细及汇总.xlsx",
        all_records, summary, merged_folder,
    )
    verify_run(run_dir, summary, all_records)
    log(f"[完成] {run_dir}")
    log(f"[完成] 计入 {summary['included_invoice_count']} 张 / {summary['included_total']:.2f} 元；"
        f"存疑 {summary['excluded_invoice_count']} 张 / {summary['excluded_total']:.2f} 元")
    return RunResult(run_dir=run_dir, summary=summary, records=all_records, skipped=skipped)


def _apply_year_scope(records: list[Record], include_since_year: int | None) -> None:
    if not include_since_year:
        return
    for record in records:
        if not record.included:
            continue
        year = 0
        for value in (record.travel_date, record.issue_date):
            head = (value or "")[:4]
            if head.isdigit():
                year = int(head)
                break
        if year and year < include_since_year:
            record.included = False
            record.archive_status = "已归档"


def _stamp_scope(records: list[Record]) -> None:
    for record in records:
        if record.included:
            record.amount_scope = "计入"
        elif record.archive_status == "重复备查":
            record.amount_scope = "剔除（重复）"
        else:
            record.amount_scope = "剔除（存疑）"


def verify_run(run_dir: Path, summary: dict, records: list[Record]) -> None:
    """三处金额必须对得上；归档文件必须真实存在。"""
    problems: list[str] = []
    included = [r for r in records if r.included]
    total_records = round(sum(r.amount for r in included), 2)
    total_categories = round(sum((summary.get("category_totals") or {}).values()), 2)
    total_merged = round(sum(float(m.get("amount") or 0) for m in summary.get("分类汇总PDF") or []), 2)
    if abs(total_records - summary["included_total"]) > 0.005:
        problems.append(f"明细之和 {total_records} != included_total {summary['included_total']}")
    if abs(total_categories - summary["included_total"]) > 0.005:
        problems.append(f"分类之和 {total_categories} != included_total {summary['included_total']}")
    if abs(total_merged - summary["included_total"]) > 0.005:
        problems.append(f"合并PDF之和 {total_merged} != included_total {summary['included_total']}")
    for record in records:
        if record.included and not record.archived_invoice:
            problems.append(f"序号 {record.seq} 计入总额却没有归档发票文件")
        if record.archived_invoice and not (run_dir / record.archived_invoice).is_file():
            problems.append(f"序号 {record.seq} 归档发票不存在：{record.archived_invoice}")
        if record.archived_itinerary and not (run_dir / record.archived_itinerary).is_file():
            problems.append(f"序号 {record.seq} 归档行程单不存在：{record.archived_itinerary}")
    if problems:
        raise RuntimeError("归档自检失败：\n  - " + "\n  - ".join(problems))
