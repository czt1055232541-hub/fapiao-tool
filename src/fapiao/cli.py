"""命令行入口。"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from . import config, mailfetch, pipeline


def _force_utf8() -> None:
    """Windows 控制台默认 GBK，中文/符号会 UnicodeEncodeError（AGENTS.md 环境坑位 6）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fapiao",
        description="每月 20 号从 QQ 邮箱收集并整理发票",
    )
    parser.add_argument("--config", type=Path, default=None, help="配置文件路径（默认项目根 config.json）")
    parser.add_argument("--run-date", type=str, default=None, help="运行日 YYYY-MM-DD（默认今天）")
    parser.add_argument("--since", type=str, default=None, help="采集窗口起始日 YYYY-MM-DD（覆盖默认窗口）")
    parser.add_argument("--before", type=str, default=None, help="采集窗口结束日 YYYY-MM-DD，不含当日")
    parser.add_argument("--replay", type=Path, default=None, help="离线重放：直接读本地暂存目录，不连邮箱")
    parser.add_argument("--dry-run", action="store_true", help="连邮箱采集并算结果，但不落盘")
    parser.add_argument("--force", action="store_true", help="允许覆盖已存在的运行目录（会先清空）")
    parser.add_argument("--out-root", type=Path, default=None, help="临时覆盖归档根目录")
    parser.add_argument("--doctor", action="store_true", help="自检：打印配置、凭据与窗口内邮件数，不写归档")
    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    args = build_parser().parse_args(argv)
    cfg = config.load_config(args.config)
    run_date = _parse_date(args.run_date) if args.run_date else date.today()
    since = _parse_date(args.since) if args.since else None
    before = _parse_date(args.before) if args.before else None

    if args.doctor:
        return _doctor(cfg, run_date, since, before)

    try:
        result = pipeline.run(
            cfg, run_date,
            replay_dir=args.replay,
            dry_run=args.dry_run,
            force=args.force,
            since=since,
            before=before,
            root_override=args.out_root,
        )
    except (FileExistsError, RuntimeError, FileNotFoundError) as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 2
    if result.run_dir is None:
        print("[预演] 未写归档。")
    return 0


def _doctor(cfg: dict, run_date: date, since: date | None, before: date | None) -> int:
    from .textutil import month_range

    print("== 配置自检 ==")
    mail_cfg = cfg.get("mail") or {}
    archive_cfg = cfg.get("archive") or {}
    print(f"配置文件        : {cfg.get('_config_path')}")
    print(f"归档根目录      : {archive_cfg.get('root')}")
    print(f"收件箱          : {mail_cfg.get('mailbox')}")
    print(f"运行日          : {run_date}")
    window = (since, before) if (since and before) else month_range(run_date, int(mail_cfg.get('extraLookbackDays') or 0))
    print(f"采集窗口        : {window[0]} ~ {window[1]}（不含）")
    print(f"统计起始年份    : {archive_cfg.get('includeSinceYear') or '不限'}")
    try:
        cred = config.resolve_credentials(cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"凭据            : [失败] {exc}")
        return 2
    print(f"凭据            : {cred.redacted()}")
    print(f"凭据来源        : {cred.source}")
    try:
        with mailfetch.MailFetcher(cred) as fetcher:
            uids = fetcher.search_uids(mail_cfg.get("mailbox") or "INBOX", window[0], window[1])
        print(f"窗口内邮件数    : {len(uids)}")
    except Exception as exc:  # noqa: BLE001
        print(f"IMAP 连接       : [失败] {exc}")
        return 2
    print("== 自检通过（未写任何归档） ==")
    return 0
