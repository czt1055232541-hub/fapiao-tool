"""排除清单：按发票号码把某些票从归档与统计里剔除。

用途（2026-10-06 定的口径）：
- 作废 / 开错的票；
- **已交由他人报销**、不由本工具重复计入的票；
- 月中已经报销过的票，月末整理时不再重复计入。

硬约束：**只排除，不删源文件**。每条排除都必须带原因，落进 00_审计摘要.json 的
skipped.excludedByNumber，不许静默消失。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = PROJECT_ROOT / "exclusions.json"

EMPTY = {
    "说明": "按发票号码排除。只从归档与统计里排除，源文件一律不删。",
    "exclusions": [],
}


def load(path: Path | str | None = None) -> dict[str, dict]:
    """读排除清单，返回 {发票号码: 条目}。文件不存在时返回空表，不报错。"""
    target = Path(path) if path else DEFAULT_PATH
    if not target.is_file():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict] = {}
    for item in payload.get("exclusions") or []:
        number = str(item.get("invoiceNumber") or "").strip()
        if number:
            out[number] = item
    return out


def _write(target: Path, entries: list[dict]) -> None:
    payload = dict(EMPTY)
    payload["exclusions"] = entries
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="")


def add(number: str, reason: str, note: str = "", path: Path | str | None = None) -> dict:
    target = Path(path) if path else DEFAULT_PATH
    number = str(number).strip()
    if not number:
        raise ValueError("发票号码不能为空")
    if not reason.strip():
        raise ValueError("必须给排除原因（例如：作废 / 交由他人报销 / 月中已报销）")
    payload = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else dict(EMPTY)
    entries = [e for e in payload.get("exclusions") or [] if str(e.get("invoiceNumber")) != number]
    entry = {
        "invoiceNumber": number,
        "reason": reason.strip(),
        "addedAt": date.today().isoformat(),
        "note": note.strip(),
    }
    entries.append(entry)
    _write(target, entries)
    return entry


def remove(number: str, path: Path | str | None = None) -> bool:
    target = Path(path) if path else DEFAULT_PATH
    if not target.is_file():
        return False
    payload = json.loads(target.read_text(encoding="utf-8"))
    entries = payload.get("exclusions") or []
    kept = [e for e in entries if str(e.get("invoiceNumber")) != str(number).strip()]
    if len(kept) == len(entries):
        return False
    _write(target, kept)
    return True
