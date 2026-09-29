"""采集层：按日期窗口从 IMAP 拉取发票邮件与附件。

设计要点（实测得出，见 AGENTS.md 环境坑位 4/5）：
- 只用纯 ASCII 的 SINCE/BEFORE 日期窗口做服务器端过滤；中文关键词搜索在服务器端不可靠。
- 邮箱侧一律只读：不删信、不移动、不标已读。
- 同一封邮件里同一张发票可能同时带 PDF/XML/OFD，必须先按基名分组，再算"一张发票"。
"""

from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
import json
import re
from dataclasses import asdict
from datetime import date
from pathlib import Path

from .config import Credentials
from .models import Attachment, Group, MailInfo
from .textutil import safe_name

# strftime('%b') 受 locale 影响，中文 locale 下会输出"9月"导致 IMAP 搜索语法错误。
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

_ALLOWED_EXTS = {".pdf", ".xml", ".ofd", ".jpg", ".jpeg", ".png"}
_IGNORED_EXTS = {".zip", ".rar", ".7z", ".txt", ".doc", ".docx", ".xls", ".xlsx", ".eml", ".ics"}

_ITINERARY_NAME = re.compile(r"行程单|行程$|ITINERARY", re.I)
_HTML_TAG = re.compile(r"<[^>]+>")
_HTML_SCRIPT = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)

MANIFEST_NAME = "manifest.json"


def imap_date(value: date) -> str:
    return f"{value.day:02d}-{_MONTHS[value.month - 1]}-{value.year}"


def decode_mime(value: str | None) -> str:
    if not value:
        return ""
    out: list[str] = []
    for chunk, charset in email.header.decode_header(value):
        if isinstance(chunk, bytes):
            try:
                out.append(chunk.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                out.append(chunk.decode("utf-8", errors="replace"))
        else:
            out.append(chunk)
    return "".join(out)


def html_to_text(html: str) -> str:
    text = _HTML_SCRIPT.sub(" ", html)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", text, flags=re.I)
    text = _HTML_TAG.sub(" ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def message_text(msg: email.message.Message) -> str:
    plain: list[str] = []
    rich: list[str] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_filename():
            continue
        ctype = (part.get_content_type() or "").lower()
        if ctype not in {"text/plain", "text/html"}:
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            decoded = payload.decode(charset, errors="replace")
        except LookupError:
            decoded = payload.decode("utf-8", errors="replace")
        (plain if ctype == "text/plain" else rich).append(decoded)
    if plain:
        return "\n".join(plain)[:20000]
    if rich:
        return html_to_text("\n".join(rich))[:20000]
    return ""


def attachment_role(filename: str) -> tuple[str, str]:
    """返回 (role, ext)。role 属于 pdf/xml/ofd/image/other。"""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return "pdf", ext
    if ext == ".xml":
        return "xml", ext
    if ext == ".ofd":
        return "ofd", ext
    if ext in {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff"}:
        return "image", ext
    return "other", ext


class MailFetcher:
    def __init__(self, cred: Credentials, timeout: int = 60):
        self.cred = cred
        self.timeout = timeout

    def __enter__(self) -> "MailFetcher":
        self.conn = imaplib.IMAP4_SSL(self.cred.imap_host, self.cred.imap_port, timeout=self.timeout)
        self.conn.login(self.cred.email, self.cred.auth_code)
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.conn.logout()
        except Exception:  # noqa: BLE001 - 断开失败不该遮蔽真实异常
            pass

    def search_uids(self, mailbox: str, since: date, before: date) -> list[bytes]:
        """按日期窗口搜索。

        **绝对不要给条件加括号**：QQ IMAP 对 "(SINCE ... BEFORE ...)" 会
        **静默返回整箱邮件**（实测：带括号 1093 封 = 全部；不带括号 48 封 = 正确窗口）。
        这是"错但不报错"的坑，会一次性把整个邮箱的附件都抓下来。
        日期也按 RFC 3501 用裸原子（date 不是 astring，不需要引号）。
        """
        self.conn.select(mailbox, readonly=True)
        status, data = self.conn.uid(
            "SEARCH", None, "SINCE", imap_date(since), "BEFORE", imap_date(before)
        )
        if status != "OK":
            raise RuntimeError(f"IMAP 搜索失败：{status} {data}")
        return (data[0] or b"").split()

    def fetch_message(self, uid: bytes) -> email.message.Message | None:
        status, data = self.conn.uid("FETCH", uid, "(RFC822)")
        if status != "OK" or not data:
            return None
        for item in data:
            if isinstance(item, tuple) and len(item) >= 2:
                return email.message_from_bytes(item[1])
        return None


def iter_attachments(msg: email.message.Message) -> list[tuple[int, str, str, bytes]]:
    """产出 (序号, 文件名, content_type, 字节)。index 从 1 开始，与 qqmail_read 口径一致。"""
    out: list[tuple[int, str, str, bytes]] = []
    index = 0
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = decode_mime(part.get_filename())
        if not filename:
            disposition = str(part.get("Content-Disposition") or "")
            if "attachment" not in disposition.lower():
                continue
            filename = decode_mime(part.get_param("name", header="content-type")) or "unnamed"
        index += 1
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        out.append((index, filename, (part.get_content_type() or "").lower(), payload))
    return out


def classify_attachment(filename: str, role: str, text_preview: str) -> str:
    """invoice / itinerary / companion / other / ignored。

    OFD 一律是 companion，**绝不单独成为一张发票**：携程的 PDF 与 OFD 附件名里各带一段
    随机串（`电子发票-2cu1412000tw59aeyA9D3.pdf` vs `电子发票-2cu1512000tw59aezEB96.ofd`），
    基名对不上就会被拆成两组，OFD 那组必然"缺少 PDF"而变成假存疑（实测踩过 6 条）。
    """
    ext = Path(filename).suffix.lower()
    if ext in _IGNORED_EXTS or ext not in _ALLOWED_EXTS:
        return "ignored"
    if role == "xml":
        return "invoice" if "EInvoice" in text_preview or "<Invoice" in text_preview else "other"
    if role == "ofd":
        return "companion"
    if role == "image":
        return "invoice"
    if role == "pdf":
        if _ITINERARY_NAME.search(filename):
            return "itinerary"
        if "AMAP ITINERARY" in text_preview or "行程单" in text_preview[:400]:
            return "itinerary"
        return "invoice"
    return "other"


def collect(
    cred: Credentials,
    since: date,
    before: date,
    mailbox: str,
    staging_dir: Path,
    max_mails: int = 3000,
    log=print,
) -> list[Group]:
    """连邮箱抓取窗口内的邮件，附件落盘到 staging_dir，返回按发票分组的 Group 列表。"""
    staging_dir.mkdir(parents=True, exist_ok=True)
    groups: list[Group] = []
    manifest_mails: list[dict] = []

    with MailFetcher(cred) as fetcher:
        uids = fetcher.search_uids(mailbox, since, before)
        log(f"[采集] 窗口 {since} ~ {before}（不含）内共 {len(uids)} 封邮件")
        if len(uids) > max_mails:
            log(f"[采集] 超过 maxMailsPerRun={max_mails}，只处理最早的 {max_mails} 封并告警")
            uids = uids[:max_mails]

        for uid_bytes in uids:
            uid = uid_bytes.decode()
            msg = fetcher.fetch_message(uid_bytes)
            if msg is None:
                log(f"[采集] uid={uid} 拉取失败，跳过")
                continue
            mail = MailInfo(
                uid=uid,
                mailbox=mailbox,
                subject=decode_mime(msg.get("Subject")),
                sender=decode_mime(msg.get("From")),
                date=_format_mail_date(msg.get("Date")),
                message_id=(msg.get("Message-ID") or "").strip(),
                text=message_text(msg)[:6000],
            )
            attachments = iter_attachments(msg)
            if not attachments:
                continue

            items: list[Attachment] = []
            for index, filename, ctype, payload in attachments:
                role, _ = attachment_role(filename)
                preview = ""
                if role in {"xml", "ofd"}:
                    preview = payload[:4000].decode("utf-8", errors="replace")
                kind = classify_attachment(filename, role, preview)
                saved = ""
                if kind != "ignored":
                    saved = str(_save(staging_dir, uid, index, filename, payload))
                if role == "pdf" and saved and kind == "invoice" and not _ITINERARY_NAME.search(filename):
                    # 文件名没写"行程单"的 PDF，用首页内容再确认一次是不是行程单。
                    from .extract import pdf_preview

                    preview = pdf_preview(Path(saved))
                    kind = classify_attachment(filename, role, preview)
                items.append(
                    Attachment(
                        mail_uid=uid,
                        mailbox=mailbox,
                        index=index,
                        filename=filename,
                        content_type=ctype,
                        size=len(payload),
                        path=saved,
                        kind=kind,
                        role=role,
                        preview=preview,
                    )
                )

            manifest_mails.append(
                {
                    "mail": asdict(mail),
                    "attachments": [asdict(a) for a in items],
                }
            )
            groups.extend(build_groups(mail, items))

    manifest = {
        "mailbox": mailbox,
        "window": {"since": since.isoformat(), "before": before.isoformat()},
        "mail_count": len(manifest_mails),
        "mails": manifest_mails,
    }
    (staging_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline=""
    )
    log(f"[采集] 落盘 {sum(1 for m in manifest_mails for a in m['attachments'] if a['path'])} 个附件，"
        f"分组成 {len(groups)} 张发票")
    return groups


def load_manifest(staging_dir: Path) -> tuple[list[Group], int, int]:
    """从暂存目录的 manifest 重建分组，供 --replay 离线重放使用。

    返回 (groups, 邮件数, 有附件的邮件数)。
    """
    path = Path(staging_dir) / MANIFEST_NAME
    if not path.is_file():
        raise FileNotFoundError(f"暂存清单不存在：{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    groups: list[Group] = []
    with_attachments = 0
    for entry in payload.get("mails", []):
        raw_mail = entry["mail"]
        mail = MailInfo(
            uid=str(raw_mail.get("uid", "")),
            mailbox=raw_mail.get("mailbox", ""),
            subject=raw_mail.get("subject", ""),
            sender=raw_mail.get("sender", ""),
            date=raw_mail.get("date", ""),
            message_id=raw_mail.get("message_id", ""),
            text=raw_mail.get("text", ""),
        )
        attachments = [
            Attachment(
                mail_uid=str(a.get("mail_uid", mail.uid)),
                mailbox=a.get("mailbox", mail.mailbox),
                index=int(a.get("index", 0)),
                filename=a.get("filename", ""),
                content_type=a.get("content_type", ""),
                size=int(a.get("size", 0)),
                path=a.get("path", ""),
                kind="",
                role=a.get("role", ""),
                preview=a.get("preview", ""),
            )
            for a in entry.get("attachments", [])
        ]
        # 用当前分类器复算 kind，避免 --replay 沿用上次采集时写死的、已被修掉的判定。
        for attachment in attachments:
            if not attachment.path:
                continue
            preview = attachment.preview
            if not preview and attachment.role in {"xml", "ofd"}:
                # 老 manifest 没存 preview 时从磁盘补读，保证重放仍然准确。
                try:
                    preview = Path(attachment.path).read_bytes()[:4000].decode("utf-8", errors="replace")
                except OSError:
                    preview = ""
            attachment.kind = classify_attachment(attachment.filename, attachment.role, preview)
        if any(a.path for a in attachments):
            with_attachments += 1
        groups.extend(build_groups(mail, attachments))
    return groups, int(payload.get("mail_count", len(payload.get("mails", [])))), with_attachments


def _format_mail_date(raw: str | None) -> str:
    if not raw:
        return ""
    parsed = email.utils.parsedate_to_datetime(raw)
    if parsed is None:
        return raw
    return parsed.isoformat()


def _save(staging_dir: Path, uid: str, index: int, filename: str, payload: bytes) -> Path:
    stem = Path(filename).stem
    ext = Path(filename).suffix
    target = staging_dir / f"{uid}_{index:02d}_{safe_name(stem, 100)}{ext}"
    target.write_bytes(payload)
    return target


def _amount_in_name(filename: str) -> float | None:
    values = re.findall(r"(d+(?:.d{1,2}))元", filename or "")
    return max(map(float, values)) if values else None


def build_groups(mail: MailInfo, items: list[Attachment]) -> list[Group]:
    """把一封邮件的附件整理成"一张发票 + 它的同伴与行程单"。"""
    invoices: dict[str, Group] = {}
    itineraries: list[Attachment] = []
    companions: list[Attachment] = []
    order: list[str] = []

    for item in items:
        if item.kind == "ignored" or item.kind == "other":
            continue
        if item.kind == "itinerary":
            itineraries.append(item)
            continue
        if item.kind == "companion":
            companions.append(item)
            continue
        key = item.base_key or item.filename
        group = invoices.get(key)
        if group is None:
            group = Group(key=key, mail=mail)
            invoices[key] = group
            order.append(key)
        if group.invoice is None:
            # PDF 优先作为归档载体；没有 PDF 时才让 XML/OFD/图片顶上，
            # 否则"只发 XML 的邮件"会被误判成没有发票。
            group.invoice = item
        elif item.role == "pdf" and group.invoice.role != "pdf":
            group.companions.append(group.invoice)
            group.invoice = item
        else:
            group.companions.append(item)

    leftovers = _attach(companions, invoices, order)

    if itineraries:
        unmatched: list[Attachment] = []
        for trip in itineraries:
            target = invoices.get(trip.base_key or "")
            if target is not None:
                target.itineraries.append(trip)
            else:
                unmatched.append(trip)
        # 基名对不上时，退回"这封邮件里只有一张发票"的弱配对，并在上层用金额复核。
        if unmatched and len(invoices) == 1:
            only = invoices[order[0]]
            only.itineraries.extend(unmatched)
        elif unmatched:
            orphan = Group(key=f"__orphan_itinerary__/{mail.uid}", mail=mail)
            orphan.itineraries = unmatched
            return [invoices[k] for k in order] + [orphan]

    groups = [invoices[k] for k in order]
    if leftovers:
        orphan = Group(key=f"__orphan_companion__/{mail.uid}", mail=mail)
        orphan.companions = leftovers
        groups.append(orphan)
    return groups


def _attach(
    companions: list[Attachment], invoices: dict[str, Group], order: list[str]
) -> list[Attachment]:
    """把同伴附件挂到发票组上：先按基名，再按"同邮件只有一张发票"，最后按文件名金额。"""
    leftovers: list[Attachment] = []
    for comp in companions:
        target = invoices.get(comp.base_key or "")
        if target is not None:
            target.companions.append(comp)
        else:
            leftovers.append(comp)
    if leftovers and len(invoices) == 1:
        invoices[order[0]].companions.extend(leftovers)
        return []
    still_left: list[Attachment] = []
    for comp in leftovers:
        amount = _amount_in_name(comp.filename)
        matched = None
        if amount is not None:
            for key in order:
                carrier = invoices[key].invoice
                if carrier is None:
                    continue
                other = _amount_in_name(carrier.filename)
                if other is not None and abs(other - amount) < 0.011:
                    matched = invoices[key]
                    break
        if matched is not None:
            matched.companions.append(comp)
        else:
            still_left.append(comp)
    return still_left
