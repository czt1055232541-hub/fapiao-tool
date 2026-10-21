"""命令行入口。"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from . import config, mailfetch, pipeline


class _Tee:
    """把 stdout/stderr 同时写进日志文件。

    计划任务运行时没有控制台，没有日志就等于没有证据。
    """

    def __init__(self, stream, handle):
        self._stream = stream
        self._handle = handle

    def write(self, data):
        self._stream.write(data)
        self._handle.write(data)
        return len(data)

    def flush(self):
        self._stream.flush()
        self._handle.flush()


def _open_log(run_date) -> "object | None":
    from . import config

    try:
        log_dir = config.PROJECT_ROOT / "data" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"{run_date.strftime('%Y%m%d')}.log"
        handle = path.open("a", encoding="utf-8", newline="")
        handle.write(f"\n===== {datetime.now().isoformat(timespec='seconds')} 运行开始 =====\n")
        handle.flush()
        return handle
    except OSError:
        return None


def _force_utf8() -> None:
    """Windows 控制台默认 GBK，中文/符号会 UnicodeEncodeError（docs/环境坑位.md 6）。"""
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
    parser.add_argument("--wechat-dir", type=Path, default=None, help="临时覆盖微信入站目录（assets/inbound）")
    parser.add_argument("--no-wechat", action="store_true", help="本次不收微信来源，只用邮箱")
    naming = parser.add_mutually_exclusive_group()
    naming.add_argument("--monthly", action="store_true",
                        help="按每月执行日归档（目录名带总额）；默认按运行日自动判断")
    naming.add_argument("--mid-month", action="store_true",
                        help="按月中临时归档（目录名不带总额）")
    parser.add_argument("--doctor", action="store_true", help="自检：打印配置、凭据与窗口内邮件数，不写归档")
    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    args = build_parser().parse_args(argv)
    cfg = config.load_config(args.config)

    issues = config.check_ready(cfg)
    if issues:
        print("配置还没填完，先不跑。照 config.example.json 建一份 config.json，填好这几项：")
        for item in issues:
            print("  - " + item)
        print()
        print("完整说明见 README.md 的「快速开始」。")
        return 2

    run_date = _parse_date(args.run_date) if args.run_date else date.today()
    since = _parse_date(args.since) if args.since else None
    before = _parse_date(args.before) if args.before else None

    if args.doctor:
        return _doctor(cfg, run_date, since, before)

    if args.no_wechat:
        cfg.setdefault("wechat", {})["enabled"] = False

    monthly = True if args.monthly else (False if args.mid_month else None)
    return _run_with_log(cfg, run_date, args, since, before, monthly)


def _run_with_log(cfg: dict, run_date: date, args, since, before, monthly=None) -> int:
    """跑主流程，并把输出同时留一份日志。

    注意：结束前必须把 sys.stdout/stderr 还原成原对象再关文件句柄，
    否则解释器退出时 flush 已关闭的文件会报 "Exception ignored on flushing
    sys.stdout" 并把退出码污染成 120（实测踩过）。
    """
    log_handle = _open_log(run_date)
    original_out, original_err = sys.stdout, sys.stderr
    if log_handle is not None:
        sys.stdout = _Tee(original_out, log_handle)
        sys.stderr = _Tee(original_err, log_handle)
    try:
        result = pipeline.run(
            cfg, run_date,
            replay_dir=args.replay,
            dry_run=args.dry_run,
            force=args.force,
            since=since,
            before=before,
            root_override=args.out_root,
            wechat_dir=args.wechat_dir,
            monthly=monthly,
        )
        if result.run_dir is None:
            print("[预演] 未写归档。")
        return 0
    except (FileExistsError, RuntimeError, FileNotFoundError) as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 2
    finally:
        sys.stdout, sys.stderr = original_out, original_err
        if log_handle is not None:
            log_handle.close()


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
    wechat_cfg = cfg.get("wechat") or {}
    inbound = wechat_cfg.get("inboundDir") or "(未配置)"
    print(f"微信来源        : {'开' if wechat_cfg.get('enabled', True) else '关'}  入站目录：{inbound}"
          f"  只收 {wechat_cfg.get('acceptExtensions') or ['.pdf']}")
    if inbound != "(未配置)":
        from pathlib import Path as _Path

        from . import wechatfetch

        probe = wechatfetch.collect(_Path(inbound), window[0], window[1], log=lambda *_a, **_k: None)  # noqa: E501
        print(f"  窗口内文件    : {probe.files_in_window} 个（其中发票 {probe.invoice_files} 个，"
              f"跳过留痕 {len(probe.skipped)} 个，非文档 {probe.ignored_by_extension or '无'}）")
    from . import exclusions as _excl

    table = _excl.load(archive_cfg.get("exclusionsFile") or None)
    print(f"排除清单        : {_excl.DEFAULT_PATH if not archive_cfg.get('exclusionsFile') else archive_cfg['exclusionsFile']}"
          f"  共 {len(table)} 条" + (f" -> {', '.join(sorted(table))}" if table else ""))
    print(f"归档命名        : 执行日 {run_date.day >= 20 and '是' if False else ('是' if run_date.day >= 20 else '否')}"
          f"  -> {'{运行日}月度发票归集(总额)' if run_date.day >= 20 else '{运行日}月度发票归集'}")
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
