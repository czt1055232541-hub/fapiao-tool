"""管理排除清单（按发票号码）。

    python scripts/manage_exclusions.py list
    python scripts/manage_exclusions.py add 25495606207001213771 --reason "交由他人报销" --note "..."
    python scripts/manage_exclusions.py remove 25495606207001213771

排除 = 从归档与统计里剔除；**源文件不删**。每条必须带原因，会落进 00_审计摘要.json。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fapiao import exclusions  # noqa: E402


def _utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main() -> int:
    _utf8()
    parser = argparse.ArgumentParser(description="管理排除清单")
    parser.add_argument("action", choices=["list", "add", "remove"])
    parser.add_argument("number", nargs="?", default=None, help="发票号码（add/remove 必填）")
    parser.add_argument("--reason", default="", help="排除原因（add 必填）")
    parser.add_argument("--note", default="", help="备注")
    parser.add_argument("--file", type=Path, default=None, help="排除清单路径")
    args = parser.parse_args()

    target = args.file or exclusions.DEFAULT_PATH
    print(f"排除清单：{target}")

    if args.action == "list":
        table = exclusions.load(target)
        if not table:
            print("  （空）")
            return 0
        for number, entry in table.items():
            print(f"  {number}  {entry.get('reason', ''):<16} {entry.get('addedAt', '')}  {entry.get('note', '')}")
        print(f"  共 {len(table)} 条")
        return 0

    if not args.number:
        print("add/remove 需要给发票号码", file=sys.stderr)
        return 2

    if args.action == "add":
        try:
            entry = exclusions.add(args.number, args.reason, args.note, target)
        except ValueError as exc:
            print(f"[失败] {exc}", file=sys.stderr)
            return 2
        print("已加入排除：" + json.dumps(entry, ensure_ascii=False))
        return 0

    removed = exclusions.remove(args.number, target)
    print("已移出排除：" + args.number if removed else "清单里没有这个号码：" + args.number)
    return 0 if removed else 1


if __name__ == "__main__":
    raise SystemExit(main())
