r"""占空间的东西定期清一清（默认只预演，加 --apply 才真删）。

    python scripts/cleanup.py                 # 预演，列出会删什么、能省多少
    python scripts/cleanup.py --apply         # 真删
    python scripts/cleanup.py --apply --keep-staging 2 --log-days 30

清什么（**只碰这些，绝不碰归档和配置**）：

| 目标 | 规则 | 为什么能删 |
|---|---|---|
| \`data/staging/*\` | 保留最近 N 次（默认 1） | 每次运行把附件复制一份进来，归档里的 \`_原始数据\\\` 才是留存；要重放重新采一次即可 |
| \`data/logs/*.log\` | 超过 N 天（默认 90） | 运行日志 |
| \`data/_test_tmp/*\` | 全部 | 单测自己建自己删，异常退出会留残余 |
| \`data/__pycache__\` 等缓存 | 全部 | 解释器缓存 |

归档目录 \`archive.root\`、\`config.json\`、\`exclusions.json\` **永远不会被删**。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def _utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def size_of(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return total


def human(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def collect(keep_staging: int, log_days: int) -> list[tuple[Path, str]]:
    targets: list[tuple[Path, str]] = []

    staging = ROOT / "data" / "staging"
    if staging.is_dir():
        runs = sorted((d for d in staging.iterdir() if d.is_dir()),
                      key=lambda d: d.stat().st_mtime, reverse=True)
        for index, run in enumerate(runs):
            if index < keep_staging:
                continue
            targets.append((run, "回放暂存（保留最近 %d 次）" % keep_staging))

    logs = ROOT / "data" / "logs"
    if logs.is_dir():
        cutoff = datetime.now() - timedelta(days=log_days)
        for log in sorted(logs.glob("*.log")):
            try:
                when = datetime.fromtimestamp(log.stat().st_mtime)
            except OSError:
                continue
            if when < cutoff:
                targets.append((log, "运行日志（超过 %d 天）" % log_days))

    tmp = ROOT / "data" / "_test_tmp"
    if tmp.is_dir():
        for item in sorted(tmp.iterdir()):
            targets.append((item, "单测残余"))

    for cache in ROOT.rglob("__pycache__"):
        if cache.is_dir():
            targets.append((cache, "解释器缓存"))
    return targets


def main() -> int:
    _utf8()
    parser = argparse.ArgumentParser(description="清理运行期占空间的东西")
    parser.add_argument("--apply", action="store_true", help="真删（默认只预演）")
    parser.add_argument("--keep-staging", type=int, default=1,
                        help="保留最近几次运行的回放暂存（默认 1）")
    parser.add_argument("--log-days", type=int, default=90, help="保留多少天内的日志（默认 90）")
    args = parser.parse_args()

    targets = collect(args.keep_staging, args.log_days)
    if not targets:
        print("没有需要清理的东西。")
        return 0

    total = 0
    print(f"{'动作':<6}{'大小':>10}  说明 / 路径")
    print("-" * 78)
    for path, reason in targets:
        size = size_of(path)
        total += size
        try:
            label = path.relative_to(ROOT)
        except ValueError:
            label = path
        print(f"{'删除' if args.apply else '将删':<6}{human(size):>10}  {label}  —— {reason}")

    print("-" * 78)
    print(f"{'已释放' if args.apply else '可释放'}: {human(total)}（{len(targets)} 项）")

    if not args.apply:
        print()
        print("这是预演。确认无误后加 --apply 真删。")
        print("注意：删掉 data/staging 后，--replay 需要先重新采集一次。")
        return 0

    failed = 0
    for path, _ in targets:
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as exc:
            failed += 1
            print(f"  [失败] {path}: {exc}")
    if failed:
        print(f"\n有 {failed} 项没删掉。")
        return 1
    print("\n清理完成。归档与配置未被触碰。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
