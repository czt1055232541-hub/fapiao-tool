"""编排层：采集 → 解析 → 归类 → 归档 → 报告，并把每一步的跳过项留痕。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from . import archive, classify, config, exclusions, mailfetch, report, wechatfetch
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
    wechat_dir: Path | None = None,
    monthly: bool | None = None,
    log=print,
) -> RunResult:
    mail_cfg = cfg.get("mail") or {}
    archive_cfg = cfg.get("archive") or {}
    lookback = int(mail_cfg.get("extraLookbackDays") or 0)
    window = (since, before) if (since and before) else month_range(run_date, lookback)
    run_key = run_date.strftime("%Y%m%d")

    # ---------- 1. 采集 ----------
    wechat_cfg = cfg.get("wechat") or {}
    groups: list[Group] = []
    mail_scanned = 0
    mail_with_attachments = 0
    wechat_skipped: list[dict] = []
    wechat_info: dict | None = None
    if replay_dir is not None:
        log(f"[重放] 从本地暂存目录读取：{replay_dir}（重放只针对邮箱来源，不含微信来源）")
        groups, mail_scanned, mail_with_attachments = mailfetch.load_manifest(Path(replay_dir))
    else:
        cred = config.resolve_credentials(cfg)
        log(f"[采集] 使用凭据：{cred.redacted()}")
        staging = staging_root() / run_key
        groups, mail_scanned, mail_with_attachments = mailfetch.collect(
            cred,
            window[0],
            window[1],
            mail_cfg.get("mailbox") or "INBOX",
            staging,
            max_mails=int(mail_cfg.get("maxMailsPerRun") or 3000),
            log=log,
        )
        # 微信来源排在邮箱之后：同一张发票两边都来时，去重会保留先到的邮箱记录
        # （邮箱记录带邮件正文，能认出住宿城市与入住日期，上下文更全）。
        if wechat_cfg.get("enabled", True):
            inbound = wechat_dir or wechat_cfg.get("inboundDir")
            if inbound:
                wechat = wechatfetch.collect(
                    Path(inbound), window[0], window[1],
                    accept_exts=tuple(wechat_cfg.get("acceptExtensions") or wechatfetch.DEFAULT_ACCEPT_EXTS),
                    log=log,
                )
                groups.extend(wechat.groups)
                wechat_skipped = wechat.skipped
                wechat_info = wechat.info()
            else:
                log("[微信] 未配置 inboundDir，跳过微信来源")
        else:
            log("[微信] 配置里已关闭微信来源")

    # ---------- 2. 解析 + 3. 归类 ----------
    records: list[Record] = []
    supporting_docs: list[dict] = []
    orphan_itineraries: list[tuple[str, str]] = []
    skipped = {
        "mails": [], "attachments": [], "duplicates": [], "orphan_itineraries": [],
        "wechat": list(wechat_skipped), "excludedByNumber": [],
    }

    for group in groups:
        if group.invoice is None:
            for att in group.companions:
                skipped["attachments"].append({
                    "mail_uid": group.mail.uid, "subject": group.mail.subject,
                    "filename": att.filename,
                    "reason": "附件没有对应的发票版式文件（PDF），未纳入整理",
                })
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
        record, _meta, extra, supporting = classify.prepare(group, cfg)
        supporting_docs.extend(supporting)
        for item in extra:
            skipped["attachments"].append({
                "mail_uid": item.mail_uid, "subject": item.subject,
                "filename": item.filename, "reason": item.reason,
            })
        if record is None:
            continue
        records.append(record)
        for att in group.companions:
            if att.role == "ofd" and not archive_cfg.get("keepOfd"):
                skipped["attachments"].append({
                    "mail_uid": group.mail.uid, "subject": group.mail.subject,
                    "filename": att.filename, "reason": "OFD 版式文件按配置忽略（PDF 已归档）",
                })

    _attach_supporting(records, supporting_docs, skipped, log)

    # ---------- 排除清单（作废 / 交由他人报销 / 月中已报销） ----------
    table = exclusions.load(archive_cfg.get("exclusionsFile") or None)
    if table:
        hit = [r for r in records if r.invoice_number in table]
        records = [r for r in records if r.invoice_number not in table]
        for record in hit:
            entry = table[record.invoice_number]
            skipped["excludedByNumber"].append({
                "invoice_number": record.invoice_number,
                "amount": record.amount,
                "reason": entry.get("reason") or "未写明原因",
                "note": entry.get("note") or "",
                "source": record.source_invoice or record.mail_uid,
            })
            log(f"[排除] {record.invoice_number} {record.amount:.2f} 元 —— {entry.get('reason')}"
                f"（{entry.get('note') or '无备注'}）")
        if hit:
            log(f"[排除] 共排除 {len(hit)} 张；源文件未删除，只从归档与统计里剔除")

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

    # 行程归属的三道推断（顺序有讲究，见各函数 docstring）
    paired = classify.pair_round_trip_flights(kept, cfg)
    if paired:
        log(f"[推断] 往返机票配对成功 {paired} 对，合并为同一趟行程")

    clusters = classify.cluster_trips(kept, cfg)
    classify.apply_trips(kept, clusters, cfg)

    all_records = kept + duplicates
    moved = classify.propagate_batch_trips(kept, cfg, log=log)
    adopted = classify.adopt_orphan_by_date(kept, clusters, cfg, log=log)
    if moved or adopted:
        log(f"[推断] 同批归属 {moved} 张、住宿按日期归入 {adopted} 张")

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
            wechat_info=wechat_info,
        )
        return RunResult(run_dir=None, summary=summary, records=all_records, skipped=skipped)

    # ---------- 4. 归档 ----------
    root = root_override or Path(archive_cfg.get("root") or (config.PROJECT_ROOT / "归档"))
    # 命名策略（2026-10-06 定的口径）：
    #   月中临时归档 -> {运行日}月度发票归集       （不带总额，因为后面还会再进票）
    #   每月执行日   -> {运行日}月度发票归集(总额)  （总额已确定）
    is_monthly = monthly if monthly is not None else run_date.day >= 20
    template_key = "folderTemplate" if is_monthly else "midMonthTemplate"
    fallback = "{runDate}月度发票归集({total:.2f})" if is_monthly else "{runDate}月度发票归集"
    name = archive.run_folder_name(archive_cfg.get(template_key) or fallback, run_date, included_total)
    run_dir = archive.prepare_run_dir(root, name, force)
    log(f"[归档] 输出目录：{run_dir}（{'每月执行日' if is_monthly else '月中临时归档'}命名）")

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
        wechat_info=wechat_info,
    )
    # 复核闸门（2026-10 定的口径）：自动跑出来的归档只是"候选结果"，
    # 必须过一遍会话复核才算最终结果。状态写在摘要里，任何人打开就知道能不能用。
    summary["review"] = {
        "status": "待复核",
        "rule": "计划任务自动产出的归档必须经会话复核后才算最终交付；未复核前不得据此报销。",
        "command": f'python scripts/review_run.py show "{run_dir.name}"',
    }
    report.write_summary(run_dir, (cfg.get("report") or {}).get("summaryName") or "00_审计摘要.json", summary)
    report.write_records(run_dir, all_records)
    report.write_excel(
        run_dir, (cfg.get("report") or {}).get("excelName") or "00_发票审计明细及汇总.xlsx",
        all_records, summary, merged_folder,
    )
    # 判据是"票的集合"，每次运行都做（不只是执行日）：偶发补档的窄窗口本来就跟月度窗口对不上。
    if archive_cfg.get("consolidateRedundant", archive_cfg.get("consolidateOnMonthlyRun", True)):
        archive.consolidate_redundant(root, run_dir, all_records, log=log)
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


def _attach_supporting(records: list[Record], docs: list[dict], skipped: dict, log=print) -> None:
    """把「订单收据」这类支持材料按发票号码挂到对应发票上。

    挂不上就留痕，不静默丢 —— 说明那张单独的凭证找不到主发票。
    """
    by_number = {r.invoice_number: r for r in records if r.invoice_number and r.invoice_number != "未识别"}
    attached = 0
    for doc in docs:
        target = by_number.get(doc.get("invoice_number") or "")
        if target is None:
            skipped["attachments"].append({
                "filename": doc.get("filename"),
                "reason": f"订单收据找不到对应发票（号码 {doc.get('invoice_number')}），未归档",
            })
            continue
        target.supporting.append(doc)
        attached += 1
    if attached:
        log(f"[材料] {attached} 份订单收据已挂到对应发票后面")


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
