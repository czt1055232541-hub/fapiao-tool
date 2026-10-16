r"""把某一份预约报销单下的发票与行程单合并成一份 PDF，供统一打印。

    # 一个预约单文件夹 -> 一份 PDF
    python scripts/merge_reimbursement.py "…\预约号-12340000差旅报销"

    # 一批预约单文件夹 -> 各一份 PDF
    python scripts/merge_reimbursement.py "…\03_已至报销单" --each-subfolder

    # 只看会合并哪些文件，不写盘
    python scripts/merge_reimbursement.py "…\03_已至报销单" --each-subfolder --dry-run

规则：
- **预约单本身不合并**（它是要跟附件一起交的另一份文件）。默认按文件夹名里的预约号
  （\`预约号-12340000差旅报销\` -> \`12340001\`）把同号开头的 PDF 排除掉；也可以用 --exclude 追加。
- 排序按文件名开头的三位序号（\`062_…_发票.pdf\`），所以一张发票后面紧跟着它自己的行程单。
- 每份源文件加一个书签；合并后校验页数 = 各源文件页数之和，对不上就报错不落盘。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

RESERVATION = re.compile(r"预约号[-_]?(\d{6,})")
LEADING_SEQ = re.compile(r"^(\d+)_")


def _utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def sort_key(path: Path) -> tuple[int, str]:
    match = LEADING_SEQ.match(path.name)
    return (int(match.group(1)) if match else 10**6, path.name)


def excluded_prefixes(folder: Path, extra: list[str]) -> list[str]:
    prefixes = list(extra)
    match = RESERVATION.search(folder.name)
    if match:
        prefixes.append(match.group(1))
    return prefixes


def collect(folder: Path, extra_exclude: list[str]) -> tuple[list[Path], list[Path]]:
    """返回 (要合并的 PDF, 被排除的 PDF)。"""
    prefixes = excluded_prefixes(folder, extra_exclude)
    keep: list[Path] = []
    drop: list[Path] = []
    for path in folder.rglob("*.pdf"):
        if path.name.startswith("_"):
            continue
        if any(path.name.startswith(prefix) for prefix in prefixes):
            drop.append(path)
            continue
        keep.append(path)
    keep.sort(key=sort_key)
    drop.sort(key=sort_key)
    return keep, drop


def merge(files: list[Path], out_path: Path) -> int:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    pages = 0
    for item in files:
        reader = PdfReader(str(item))
        start = pages
        for page in reader.pages:
            writer.add_page(page)
            pages += 1
        if len(reader.pages):
            writer.add_outline_item(item.stem[:180], start)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as handle:
        writer.write(handle)

    check = PdfReader(str(out_path))
    if len(check.pages) != pages:
        out_path.unlink(missing_ok=True)
        raise RuntimeError(f"页数校验失败：{out_path} 期望 {pages} 实得 {len(check.pages)}")
    return pages


def bundled_name(folder: Path, form: Path | None) -> str:
    from fapiao.textutil import safe_name

    stem = form.stem if form is not None else folder.name
    return f"{safe_name(stem, 120)}_发票与行程单.pdf"


def main() -> int:
    _utf8()
    parser = argparse.ArgumentParser(description="把预约报销单下的发票与行程单合并成一份 PDF")
    parser.add_argument("path", type=Path, help="预约单文件夹，或包含多个预约单文件夹的父目录")
    parser.add_argument("--each-subfolder", action="store_true", help="对每个直接子文件夹各出一份 PDF")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="输出目录（默认写到父目录下的 _打印用合并PDF）")
    parser.add_argument("--exclude", action="append", default=[],
                        help="额外排除：按文件名前缀，可重复")
    parser.add_argument("--dry-run", action="store_true", help="只列出会合并什么，不写盘")
    args = parser.parse_args()

    root = args.path
    if not root.is_dir():
        print(f"目录不存在：{root}", file=sys.stderr)
        return 2

    folders = [d for d in sorted(root.iterdir()) if d.is_dir()] if args.each_subfolder else [root]
    folders = [d for d in folders if any(d.rglob("*.pdf"))]
    if not folders:
        print(f"没找到含 PDF 的子文件夹：{root}", file=sys.stderr)
        return 2

    out_dir = args.out_dir or (root / "_打印用合并PDF")
    produced: list[tuple[Path, int, int]] = []
    problems: list[str] = []

    for folder in folders:
        keep, drop = collect(folder, args.exclude)
        if not keep:
            problems.append(f"{folder.name}：没有可合并的 PDF")
            continue
        form = drop[0] if drop else None
        out_path = out_dir / bundled_name(folder, form)

        print(f"\n=== {folder.name} ===")
        if drop:
            for item in drop:
                print(f"  [排除] {item.name}")
        for item in keep:
            print(f"  [合并] {item.name}")
        print(f"  -> {out_path.name}（{len(keep)} 份源文件）")

        if args.dry_run:
            continue
        try:
            pages = merge(keep, out_path)
        except Exception as exc:  # noqa: BLE001 - 任何合并失败都要如实报出来，不能半成品落盘
            problems.append(f"{folder.name}：合并失败 {exc}")
            continue
        produced.append((out_path, len(keep), pages))
        print(f"  -> 完成，共 {pages} 页")

    if args.dry_run:
        print(f"\n[预演] 不写盘。将输出到：{out_dir}")
        return 0

    print(f"\n=== 汇总 ===")
    for path, sources, pages in produced:
        print(f"  {sources:>3} 份源文件 / {pages:>3} 页  {path}")
    print(f"  输出目录：{out_dir}")
    if problems:
        print("\n有问题：")
        for item in problems:
            print(f"  - {item}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
