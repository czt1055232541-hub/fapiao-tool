"""微信来源：把 <微信工作区> 的 assets/inbound/ 当成第二个收件箱。

背景与边界（2026-10-06 与老板/操作者确认）：
- 老板的长期指示是「以后的发票都丢给微信对话统一归档」，所以这是一个**通用来源**，
  不是给某几张票的特殊处理。
- 节奏与邮箱一致：**每月 20 号那一趟**统一整理，不挂实时监听。
- **图片不收**（聊天图太多），只进 skipped 留痕；OFD / XML 也不收
  （微信转发的文件名是随机串，按基名合并会造出假分组 —— 邮箱侧踩过同类坑）。
- 同内容文件按 **md5 去重**（桥接会把同一张图存成 .jpg 和 .png 两份）。

与邮箱来源的差别：这里**没有邮件正文**，所以住宿发票认不出城市、行程也谈不上闭环。
按已定口径（方案 A），这类票的归宿就是 99_存疑待人工审计，不补上下文、不改规则。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .models import Attachment, Group, MailInfo

WECHAT_MAILBOX = "WECHAT"

# 文件名前缀就是消息日期（实测：2026-10-06_7513183848471655000_1_313f47.pdf）
_DATE_PREFIX = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_")

# 只把 PDF 当发票载体。理由见模块头。
DEFAULT_ACCEPT_EXTS = (".pdf",)
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".heic", ".webp")
DOCUMENT_EXTS = DEFAULT_ACCEPT_EXTS + (".ofd", ".xml") + IMAGE_EXTS

R_IMAGE_NOT_ACCEPTED = "微信来源不收图片（可能不是发票），需人工确认后再处理"
R_FORMAT_NOT_ACCEPTED = "微信来源只收 PDF（随机文件名无法按基名合并多格式），如需归档请改发 PDF"
R_UNKNOWN_DATE = "无法判断文件日期"
R_DUPLICATE_BY_HASH = "与另一份内容相同（md5 一致），未重复纳入"


@dataclass
class WeChatResult:
    groups: list[Group] = field(default_factory=list)
    inbound_dir: str = ""
    files_in_window: int = 0
    invoice_files: int = 0
    ignored_by_extension: dict = field(default_factory=dict)
    skipped: list[dict] = field(default_factory=list)

    def info(self) -> dict:
        return {
            "inboundDir": self.inbound_dir,
            "filesInWindow": self.files_in_window,
            "invoiceFiles": self.invoice_files,
            "ignoredByExtension": dict(sorted(self.ignored_by_extension.items())),
        }


def file_date(path: Path) -> date | None:
    """优先取文件名里的日期前缀；没有就退回文件修改时间。"""
    match = _DATE_PREFIX.match(path.name)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass  # 形如 2026-13-45 的坏前缀：退回 mtime，别把文件直接丢掉
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).date()
    except OSError:
        return None


def collect(
    root: Path | str,
    since: date,
    before: date,
    accept_exts: tuple[str, ...] = DEFAULT_ACCEPT_EXTS,
    log=print,
) -> WeChatResult:
    """扫描微信入站目录，产出与邮箱来源同构的 Group 列表。

    窗口语义与邮箱一致：since <= 文件日期 < before。
    """
    result = WeChatResult(inbound_dir=str(root))
    inbound = Path(root)
    if not inbound.is_dir():
        log(f"[微信] 入站目录不存在，跳过微信来源：{inbound}")
        return result

    accepted = tuple(ext.lower() for ext in accept_exts)
    files = [p for p in sorted(inbound.iterdir()) if p.is_file() and not p.name.startswith(".")]

    in_window: list[Path] = []
    for path in files:
        when = file_date(path)
        if when is None:
            result.skipped.append({"filename": path.name, "reason": R_UNKNOWN_DATE})
            continue
        if not (since <= when < before):
            continue  # 窗口外的文件静默略过：所有月份的文件都堆在这同一个目录里
        in_window.append(path)
    result.files_in_window = len(in_window)

    # md5 去重：桥接会把同一张图存成 .jpg 和 .png 两份，必须只留一份
    seen_hash: dict[str, str] = {}
    unique: list[Path] = []
    for path in in_window:
        try:
            digest = hashlib.md5(path.read_bytes()).hexdigest()
        except OSError as exc:
            result.skipped.append({"filename": path.name, "reason": f"读取失败：{exc}"})
            continue
        if digest in seen_hash:
            result.skipped.append({
                "filename": path.name,
                "reason": f"{R_DUPLICATE_BY_HASH}（保留 {seen_hash[digest]}）",
            })
            continue
        seen_hash[digest] = path.name
        unique.append(path)

    for path in unique:
        ext = path.suffix.lower()
        if ext not in DOCUMENT_EXTS:
            key = ext.lstrip(".") or "(无扩展名)"
            result.ignored_by_extension[key] = result.ignored_by_extension.get(key, 0) + 1
            continue
        if ext in IMAGE_EXTS:
            result.skipped.append({"filename": path.name, "reason": R_IMAGE_NOT_ACCEPTED})
            continue
        if ext not in accepted:
            result.skipped.append({"filename": path.name, "reason": R_FORMAT_NOT_ACCEPTED})
            continue

        when = file_date(path)
        mail = MailInfo(
            uid=f"wechat:{path.name}",
            mailbox=WECHAT_MAILBOX,
            subject=path.name,
            sender="",
            date=when.isoformat() if when else "",
            message_id="",
            text="",
        )
        attachment = Attachment(
            mail_uid=mail.uid,
            mailbox=WECHAT_MAILBOX,
            index=1,
            filename=path.name,
            content_type="",
            size=path.stat().st_size,
            path=str(path),
            kind="invoice",
            role="pdf",
        )
        result.groups.append(Group(key=path.name, mail=mail, invoice=attachment))
        result.invoice_files += 1

    tail = f"、非文档忽略 {result.ignored_by_extension}" if result.ignored_by_extension else ""
    log(
        f"[微信] 入站目录 {inbound}：窗口内 {result.files_in_window} 个文件，"
        f"其中发票 {result.invoice_files} 个、跳过留痕 {len(result.skipped)} 个{tail}"
    )
    return result
