"""跨层传递的数据结构。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class MailInfo:
    uid: str
    mailbox: str
    subject: str
    sender: str
    date: str
    message_id: str = ""
    text: str = ""


@dataclass
class Attachment:
    mail_uid: str
    mailbox: str
    index: int
    filename: str
    content_type: str
    size: int
    path: str = ""
    kind: str = ""  # invoice / itinerary / companion / other / ignored
    role: str = ""  # pdf / xml / ofd / image / other
    preview: str = ""  # 分类用的内容片段；--replay 靠它复算 kind，避免用过期判定

    @property
    def base_key(self) -> str:
        from .textutil import strip_suffix

        stem = self.filename
        for ext in (".pdf", ".xml", ".ofd", ".jpg", ".jpeg", ".png"):
            if stem.lower().endswith(ext):
                stem = stem[: -len(ext)]
                break
        return strip_suffix(
            stem,
            (
                "电子发票",
                "发票",
                "电子行程单",
                "行程单",
                "电子客票",
                "（发票）",
                "(发票)",
            ),
        ).strip(" _-（）()")


@dataclass
class Group:
    """一封邮件里的"一张发票"及其同伴（XML/OFD/图片）与行程单。"""

    key: str
    mail: MailInfo
    invoice: Attachment | None = None
    companions: list[Attachment] = field(default_factory=list)
    itineraries: list[Attachment] = field(default_factory=list)


@dataclass
class Meta:
    invoice_number: str = ""
    issue_date: str = ""
    travel_date: str = ""
    total: float | None = None
    buyer_name: str = ""
    buyer_tax_id: str = ""
    seller_name: str = ""
    seller_tax_id: str = ""
    item_names: list[str] = field(default_factory=list)
    is_rail: bool = False
    is_refund: bool = False
    source_format: str = ""  # xml / pdf / none
    error: str = ""


@dataclass
class ItineraryMeta:
    date: str = ""
    cities: list[str] = field(default_factory=list)
    amount: float | None = None


@dataclass
class Record:
    seq: int = 0
    buyer: str = ""
    purpose: str = ""
    travel_date: str = ""
    location: str = ""
    kind: str = ""
    amount: float = 0.0
    invoice_number: str = ""
    issue_date: str = ""
    seller: str = ""
    status: str = ""
    reasons: list[str] = field(default_factory=list)
    inferred_from: str = ""   # 行程是按什么证据推断来的（人工复核要看这个）
    trip_city: str = ""       # 往返机票配对后统一的城市（覆盖 location 推导）
    category: str = ""
    included: bool = False
    archived_invoice: str = ""
    archived_itinerary: str = ""
    archived_xml: str = ""
    source_invoice: str = ""
    source_itinerary: str = ""
    source_xml: str = ""
    trip_folder: str = ""
    mail_uid: str = ""
    mail_subject: str = ""
    mail_date: str = ""
    amount_scope: str = ""
    archive_status: str = "已归档"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Skipped:
    kind: str
    reason: str
    mail_uid: str = ""
    subject: str = ""
    filename: str = ""
