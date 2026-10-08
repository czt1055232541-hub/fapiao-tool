"""归档层：按参考案例的目录结构与命名规则落盘。

命名规则逐字符对齐 <参考案例目录>，见 docs/整理要求.md 第 3 节。
"""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

from . import classify
from .models import Record
from .textutil import safe_name

PURPOSE_DIRS = {
    classify.PURPOSE_TRIP: "01_差旅费",
    classify.PURPOSE_ENTERTAIN: "02_招待费",
    classify.PURPOSE_TAXI_CITY: "03_杭州市内打车票",
}

QUARANTINE = "99_存疑待人工审计"
RAW_DIR = "_原始数据"
MERGED_JSON = "合并清单.json"


def tail6(invoice_number: str) -> str:
    number = (invoice_number or "").strip()
    if not number or number == "未识别":
        return "未识别"
    return number[-6:]


def file_stem(record: Record) -> str:
    return safe_name(
        f"{record.seq:03d}_{record.travel_date}_{record.location}_{record.kind}_"
        f"{record.amount:.2f}元_发票{tail6(record.invoice_number)}"
    )


def run_folder_name(template: str, run_date: date, total: float) -> str:
    return safe_name(template.format(runDate=run_date.strftime("%Y%m%d"), total=total))


def merged_folder_name(cfg: dict, include_since_year: int | None) -> str:
    base = (cfg.get("report") or {}).get("mergedFolder") or "00_汇总发票"
    if include_since_year:
        return f"{base}（剔除{include_since_year - 1}年及以前）"
    return base


def owner_dir(buyer: str, cfg: dict) -> str | None:
    """抬头 → 明细目录：按 config 里 buyers 的顺序编号（浙江大学=01、芯智辰光=02）。"""
    names = [b.get("name") for b in (cfg.get("classify") or {}).get("buyers") or []]
    if buyer not in names:
        return None
    return f"{names.index(buyer) + 1:02d}_{buyer}明细"


def relative_dir(record: Record, cfg: dict) -> Path:
    if record.reasons:
        return Path(QUARANTINE) / safe_name(record.reasons[0], 80)
    owner = owner_dir(record.buyer, cfg)
    if owner is None:
        return Path(QUARANTINE) / safe_name(classify.R_BUYER, 80)
    purpose_dir = PURPOSE_DIRS.get(record.purpose, "09_其他")
    out = Path(owner) / purpose_dir
    if record.trip_folder:
        out = out / safe_name(record.trip_folder, 120)
    return out


def place(record: Record, run_dir: Path, cfg: dict) -> None:
    """把一条记录的 PDF/行程单/XML 复制到归档目录，并回填归档相对路径。"""
    target = run_dir / relative_dir(record, cfg)
    target.mkdir(parents=True, exist_ok=True)
    stem = file_stem(record)

    invoice_out = target / f"{stem}_发票.pdf"
    if record.source_invoice and Path(record.source_invoice).is_file():
        shutil.copy2(record.source_invoice, invoice_out)
        record.archived_invoice = str(invoice_out.relative_to(run_dir))
    elif record.source_invoice:
        record.reasons.append(f"源发票文件缺失：{Path(record.source_invoice).name}")
        record.archived_invoice = ""

    if record.source_itinerary and Path(record.source_itinerary).is_file():
        itinerary_out = target / f"{stem}_行程单.pdf"
        shutil.copy2(record.source_itinerary, itinerary_out)
        record.archived_itinerary = str(itinerary_out.relative_to(run_dir))

    if record.source_xml and Path(record.source_xml).is_file():
        raw_out = run_dir / RAW_DIR / f"{stem}.xml"
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(record.source_xml, raw_out)
        record.archived_xml = str(raw_out.relative_to(run_dir))


def place_orphan_itinerary(run_dir: Path, path: str, filename: str) -> str:
    target = run_dir / QUARANTINE / safe_name(classify.R_ORPHAN_ITINERARY, 80)
    target.mkdir(parents=True, exist_ok=True)
    out = target / safe_name(filename, 150)
    if out.suffix.lower() != ".pdf":
        out = out.with_suffix(".pdf")
    shutil.copy2(path, out)
    return str(out.relative_to(run_dir))


def consolidate_same_window(
    root: Path, run_dir: Path, window: tuple[date, date], records: list[Record], log=print
) -> list[str]:
    """执行日合并：把同一采集窗口的旧目录并掉（那是月中提前生成的）。

    安全闸门：**只有当旧目录里的每一张发票都能在新目录里按发票号码找到时才删**；
    少一张就保留并报告，绝不拿"反正差不多"去赌。返回被删掉的目录名。
    """
    have = {r.invoice_number for r in records if r.invoice_number and r.invoice_number != "未识别"}
    wanted = (window[0].isoformat(), window[1].isoformat())
    removed: list[str] = []
    if not root.is_dir():
        return removed

    for path in sorted(root.iterdir()):
        if not path.is_dir() or path.resolve() == run_dir.resolve():
            continue
        summary_file = path / "00_审计摘要.json"
        if not summary_file.is_file():
            continue
        try:
            payload = json.loads(summary_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        other = payload.get("window") or {}
        if (other.get("since"), other.get("before")) != wanted:
            continue

        numbers: set[str] = set()
        detail = path / "00_审计明细.json"
        if detail.is_file():
            try:
                numbers = {
                    str(item.get("invoice_number"))
                    for item in json.loads(detail.read_text(encoding="utf-8"))
                    if item.get("invoice_number") and item.get("invoice_number") != "未识别"
                }
            except (OSError, ValueError, TypeError):
                numbers = set()

        missing = numbers - have
        if missing:
            log(f"[合并] 保留 {path.name}：它有新目录里没有的发票号 {sorted(missing)}")
            continue
        shutil.rmtree(path)
        removed.append(path.name)
        log(f"[合并] 已并入并删除重复的月中目录：{path.name}（{len(numbers)} 张发票全部已在新目录中）")
    return removed


def prepare_run_dir(root: Path, name: str, force: bool) -> Path:
    """幂等闸门：目标目录已存在且非空时拒绝覆盖（除非 force）。"""
    run_dir = root / name
    if run_dir.exists():
        has_content = any(run_dir.iterdir())
        if has_content and not force:
            raise FileExistsError(
                f"运行目录已存在且非空，为避免覆盖已停止：{run_dir}\n"
                f"确认要重建请加 --force（会先清空该目录）。"
            )
        if force:
            shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir
