"""预演查看：把一次运行会得到什么打成一张表，不写归档。

用法：
    python scripts/preview.py --run-date 2026-10-20          # 连邮箱采集后预演
    python scripts/preview.py --replay data/staging/20260920  # 复用已抓取的暂存
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fapiao import classify, config, mailfetch  # noqa: E402
from fapiao.textutil import month_range  # noqa: E402


def _utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def collect_groups(args, cfg) -> tuple[list, dict]:
    if args.replay:
        staging = Path(args.replay)
    else:
        run_date = datetime.strptime(args.run_date, "%Y-%m-%d").date() if args.run_date else date.today()
        mail_cfg = cfg.get("mail") or {}
        since, before = month_range(run_date, int(mail_cfg.get("extraLookbackDays") or 0))
        if args.since:
            since = datetime.strptime(args.since, "%Y-%m-%d").date()
        if args.before:
            before = datetime.strptime(args.before, "%Y-%m-%d").date()
        staging = ROOT / "data" / "staging" / run_date.strftime("%Y%m%d")
        if not staging.is_dir():
            cred = config.resolve_credentials(cfg)
            print(f"[采集] {cred.redacted()}")
            mailfetch.collect(cred, since, before, mail_cfg.get("mailbox") or "INBOX",
                              staging, max_mails=int(mail_cfg.get("maxMailsPerRun") or 3000))
    groups, mails, with_att = mailfetch.load_manifest(staging)
    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    return groups, {"staging": staging, "mails": mails, "with_att": with_att, "manifest": manifest}


def main() -> int:
    _utf8()
    parser = argparse.ArgumentParser(description="预演：看清这次会收到什么、哪些会进存疑")
    parser.add_argument("--run-date", default=None)
    parser.add_argument("--since", default=None)
    parser.add_argument("--before", default=None)
    parser.add_argument("--replay", default=None)
    args = parser.parse_args()

    cfg = config.load_config()
    groups, info = collect_groups(args, cfg)
    mail_by_uid = {e["mail"]["uid"]: e for e in info["manifest"]["mails"]}

    records, not_invoice = [], []
    for group in groups:
        if group.invoice is None:
            continue
        record, _meta, _extra = classify.prepare(group, cfg)
        (records if record is not None else not_invoice).append(record or group)

    print(f"\n暂存目录 {info['staging']}")
    print(f"邮件 {info['mails']} 封（有附件 {info['with_att']}）｜分组 {len(groups)}｜"
          f"判定为发票 {len(records)}｜不像发票 {len(not_invoice)}")

    if not_invoice:
        print("\n【不像发票，已跳过】")
        for group in not_invoice:
            print(f"  uid={group.mail.uid:<7} {group.mail.subject[:46]:<48} {group.key[:44]}")

    kept, duplicates = classify.dedupe(records)
    for dup in duplicates:
        dup.reasons = [classify.R_DUPLICATE]
    clusters = classify.cluster_trips(kept, cfg)
    classify.apply_trips(kept, clusters, cfg)
    classify.finalize(records, cfg)

    print(f"\n{'#':>3} {'抬头':<7} {'用途':<7} {'行程日期':<21} {'地点/路线':<15} {'类型':<9} "
          f"{'金额':>9} {'号后6':<7} 存疑原因")
    print("-" * 132)
    for record in records:
        print(f"{record.seq:>3} {record.buyer:<7} {record.purpose:<7} {record.travel_date:<21} "
              f"{record.location:<15} {record.kind:<9} {record.amount:>9.2f} "
              f"{record.invoice_number[-6:]:<7} {'；'.join(record.reasons)}")

    included = [r for r in records if r.included]
    print(f"\n计入 {len(included)} 张 / {sum(r.amount for r in included):.2f} 元；"
          f"存疑 {len(records) - len(included)} 张 / "
          f"{sum(r.amount for r in records if not r.included):.2f} 元")
    print("分类：", dict(Counter(r.category for r in included)))
    print("\n行程聚类：")
    for cluster in clusters:
        print(f"  {cluster.folder:<50} 闭环={'是' if cluster.closed else '否'} 成员={len(cluster.members)}")
    reasons = Counter(reason for record in records for reason in record.reasons)
    if reasons:
        print("\n存疑原因：", dict(reasons))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
