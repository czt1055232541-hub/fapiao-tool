"""归类层：元数据抽取编排 → 去重 → 抬头/用途 → 行程聚类 → 存疑判定。

规则的事实依据见 docs/整理要求.md；本模块只实现规则，不创造规则。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from . import extract
from .config import buyer_keywords, buyer_tax_ids
from .models import Attachment, Group, Meta, Record, Skipped

# ---- 存疑原因（原文，与 docs/整理要求.md 第 5 节一一对应） ----
R_BUYER = "未识别到指定抬头"
R_TRIP_OPEN = "未能确认杭州出发并返回杭州的行程闭环"
R_TRIP_NO_HOME = "杭州市外行程缺少杭州往返闭环凭证"
R_ORPHAN_ITINERARY = "未匹配到发票的行程单"
R_IMAGE = "图片格式发票无法解析"
R_DUPLICATE = "重复发票不重复计入"
R_TAXI_NO_CITY = "打车发票未匹配行程单，城市待核"
R_UNPARSABLE = "无法解析发票内容"
R_AMOUNT_MISMATCH = "行程单金额与发票金额不一致"
R_NO_PDF = "缺少PDF版发票文件"

PURPOSE_TRIP = "差旅费"
PURPOSE_ENTERTAIN = "招待费"
PURPOSE_TAXI_CITY = "杭州市内打车票"

KIND_TAXI = "打车"
KIND_RAIL = "动车高铁"
KIND_RAIL_REFUND = "动车高铁退改签"
KIND_FLIGHT = "机票"
KIND_HOTEL = "住宿"
KIND_ENTERTAIN = "招待费"
KIND_OTHER = "其他"

_ENTERTAIN_WORDS = ("餐费", "餐饮", "糕点", "伙食", "宴请", "食品")
_HOTEL_WORDS = ("住宿",)
_FLIGHT_WORDS = ("机票", "机票款", "代订机票")
_TAXI_WORDS = ("客运服务", "客运服务费", "出租车", "网约车", "运输服务")


@dataclass
class TripCluster:
    index: int
    city: str
    start: str
    end: str
    route: str = ""
    members: list[str] = field(default_factory=list)

    @property
    def closed(self) -> bool:
        return bool(self.route)

    @property
    def folder(self) -> str:
        base = f"{self.index:02d}_{self.start}_至_{self.end}_{self.city}差旅"
        return f"{base}_{self.route}" if self.route else base


def _parse_date(value: str) -> date | None:
    text = (value or "").split("至")[0].strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _text_of(path: str) -> tuple[str, str, str]:
    if not path:
        return "", "none", "空路径"
    return extract.read_pdf_text(Path(path))


def prepare(group: Group, cfg: dict) -> tuple[Record, Meta, list[Skipped]]:
    """把一组附件转成一条待定稿记录。"""
    skipped: list[Skipped] = []
    meta = Meta()
    itinerary = extract.ItineraryMeta()
    raw_text = ""
    xml_att: Attachment | None = None
    image_att: Attachment | None = None
    invoice_pdf: Attachment | None = None

    for item in group.companions:
        if item.role == "xml" and xml_att is None:
            xml_att = item
        elif item.role == "image" and image_att is None:
            image_att = item
    if group.invoice is not None and group.invoice.role == "pdf":
        invoice_pdf = group.invoice
    elif group.invoice is not None and group.invoice.role == "image":
        image_att = image_att or group.invoice
    elif group.invoice is not None and group.invoice.role == "xml":
        xml_att = xml_att or group.invoice

    if xml_att is not None and xml_att.path:
        try:
            xml_text = Path(xml_att.path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            xml_text = ""
            meta.error = f"XML 读取失败：{exc}"
        parsed = extract.parse_xml_invoice(xml_text) if xml_text else None
        if parsed is not None:
            meta = parsed

    if invoice_pdf is not None and invoice_pdf.path:
        raw_text, _extractor, pdf_error = _text_of(invoice_pdf.path)
        if meta.source_format != "xml" or not meta.invoice_number:
            pdf_meta = extract.parse_pdf_invoice(raw_text, invoice_pdf.filename)
            meta = _merge_meta(meta, pdf_meta)
        if pdf_error and not raw_text:
            meta.error = meta.error or f"PDF 不可解析：{pdf_error}"

    if not raw_text and invoice_pdf is None and image_att is not None:
        meta.error = meta.error or R_IMAGE

    itinerary_att: Attachment | None = group.itineraries[0] if group.itineraries else None
    if itinerary_att is not None and itinerary_att.path:
        itin_text, _, _ = _text_of(itinerary_att.path)
        itinerary = extract.parse_itinerary(itin_text)

    for extra in group.itineraries[1:]:
        skipped.append(
            Skipped(kind="itinerary", reason="同一发票带多份行程单，多余的一份未归档",
                    mail_uid=group.mail.uid, subject=group.mail.subject, filename=extra.filename)
        )

    record = _build_record(group, meta, itinerary, cfg, raw_text, invoice_pdf, itinerary_att, xml_att, image_att)
    return record, meta, skipped


def _merge_meta(primary: Meta, fallback: Meta) -> Meta:
    """XML 为主，PDF 补齐 XML 缺的字段。"""
    out = Meta(**{**primary.__dict__})
    if not out.invoice_number:
        out.invoice_number = fallback.invoice_number
    if not out.issue_date:
        out.issue_date = fallback.issue_date
    if out.total is None:
        out.total = fallback.total
    if not out.buyer_name:
        out.buyer_name = fallback.buyer_name
    if not out.buyer_tax_id:
        out.buyer_tax_id = fallback.buyer_tax_id
    if not out.seller_name:
        out.seller_name = fallback.seller_name
    if not out.seller_tax_id:
        out.seller_tax_id = fallback.seller_tax_id
    if not out.item_names:
        out.item_names = fallback.item_names
    out.is_rail = out.is_rail or fallback.is_rail
    out.is_refund = out.is_refund or fallback.is_refund
    if not out.travel_date:
        out.travel_date = fallback.travel_date
    out.source_format = primary.source_format or fallback.source_format
    if not out.error:
        out.error = fallback.error
    return out


def detect_buyer(meta: Meta, full_text: str, cfg: dict) -> tuple[str, str]:
    """返回 (抬头名, 命中依据)。"""
    tax_ids = buyer_tax_ids(cfg)
    for candidate in (meta.buyer_tax_id, meta.seller_tax_id):
        if candidate and candidate.upper() in tax_ids:
            if candidate == meta.buyer_tax_id:
                return tax_ids[candidate.upper()], "购买方税号"
    for candidate in (meta.buyer_tax_id,):
        if candidate and candidate.upper() in tax_ids:
            return tax_ids[candidate.upper()], "购买方税号"
    haystack = " ".join(filter(None, (meta.buyer_name, full_text)))
    for keyword, name in buyer_keywords(cfg):
        if keyword and keyword in haystack:
            return name, f"关键词 {keyword}"
    return "抬头待核", ""


def detect_kind(meta: Meta, has_itinerary: bool, text: str) -> str:
    joined = " ".join(meta.item_names)
    if meta.is_rail:
        return KIND_RAIL_REFUND if meta.is_refund else KIND_RAIL
    if any(word in joined for word in _HOTEL_WORDS):
        return KIND_HOTEL
    if any(word in joined for word in _FLIGHT_WORDS):
        return KIND_FLIGHT
    if any(word in joined for word in _ENTERTAIN_WORDS):
        return KIND_ENTERTAIN
    if has_itinerary or any(word in joined for word in _TAXI_WORDS):
        return KIND_TAXI
    if "铁路电子客票" in text:
        return KIND_RAIL_REFUND if meta.is_refund else KIND_RAIL
    return KIND_OTHER


def _location_and_date(
    meta: Meta, itinerary: extract.ItineraryMeta, group: Group, kind: str, full_text: str
) -> tuple[str, str]:
    mail_body = f"{group.mail.subject}\n{group.mail.text}"
    if kind == KIND_TAXI:
        city = "-".join(itinerary.cities) if itinerary.cities else "城市待核"
        when = itinerary.date or meta.travel_date or meta.issue_date or "日期待核"
        return city, when
    if kind in (KIND_RAIL, KIND_RAIL_REFUND):
        route = extract.rail_route(full_text)
        return (route or "铁路地点待核"), (meta.travel_date or meta.issue_date or "日期待核")
    if kind == KIND_FLIGHT:
        route = extract.flight_route(full_text) or extract.flight_route(group.mail.subject)
        return (route or "航程待核"), (meta.travel_date or meta.issue_date or "日期待核")
    if kind == KIND_HOTEL:
        start, end = extract.hotel_stay(mail_body)
        when = f"{start}至{end}" if start and end and start != end else (start or meta.issue_date or "日期待核")
        city = extract.hotel_city(group.mail.subject, group.mail.text) or "地点待核"
        return city, when
    return "地点待核", (meta.issue_date or "日期待核")


def _build_record(
    group: Group,
    meta: Meta,
    itinerary: extract.ItineraryMeta,
    cfg: dict,
    raw_text: str,
    invoice_pdf: Attachment | None,
    itinerary_att: Attachment | None,
    xml_att: Attachment | None,
    image_att: Attachment | None,
) -> Record:
    kind = detect_kind(meta, itinerary_att is not None, raw_text)
    location, when = _location_and_date(meta, itinerary, group, kind, raw_text)
    buyer, _basis = detect_buyer(meta, raw_text, cfg)
    home = (cfg.get("classify") or {}).get("homeCity", "杭州")

    amount = meta.total
    if amount is None and itinerary.amount is not None:
        amount = itinerary.amount
    if amount is None and image_att is not None:
        amount = 0.0

    if kind == KIND_ENTERTAIN:
        purpose = PURPOSE_ENTERTAIN
    elif kind == KIND_TAXI and location == home:
        purpose = PURPOSE_TAXI_CITY
    elif kind == KIND_TAXI and location == "城市待核":
        purpose = PURPOSE_TRIP
    else:
        purpose = PURPOSE_TRIP

    record = Record(
        buyer=buyer,
        purpose=purpose,
        travel_date=when,
        location=location,
        kind=kind,
        amount=round(float(amount or 0.0), 2),
        invoice_number=meta.invoice_number or "未识别",
        issue_date=meta.issue_date or "日期待核",
        seller=meta.seller_name or "销售方待核",
        source_invoice=invoice_pdf.path if invoice_pdf else "",
        source_itinerary=itinerary_att.path if itinerary_att else "",
        source_xml=xml_att.path if xml_att else "",
        mail_uid=group.mail.uid,
        mail_subject=group.mail.subject,
        mail_date=group.mail.date,
    )
    record.archived_invoice = ""
    record.reasons = _initial_reasons(record, meta, itinerary, itinerary_att, image_att, invoice_pdf, home)
    return record


def _initial_reasons(
    record: Record,
    meta: Meta,
    itinerary: extract.ItineraryMeta,
    itinerary_att: Attachment | None,
    image_att: Attachment | None,
    invoice_pdf: Attachment | None,
    home: str,
) -> list[str]:
    reasons: list[str] = []
    if record.buyer == "抬头待核":
        reasons.append(R_BUYER)
    if image_att is not None and invoice_pdf is None and meta.total is None:
        reasons.append(R_IMAGE)
    if meta.error and not reasons:
        reasons.append(R_UNPARSABLE)
    if (
        itinerary.amount is not None
        and record.amount
        and abs(itinerary.amount - record.amount) > 0.011
    ):
        reasons.append(R_AMOUNT_MISMATCH)
    if invoice_pdf is None:
        reasons.append(R_NO_PDF)
    if record.kind == KIND_TAXI and itinerary_att is None:
        reasons.append(R_TAXI_NO_CITY)
    if record.kind == KIND_TAXI and itinerary.cities and home not in itinerary.cities:
        reasons.append(f"行程单城市为{'-'.join(itinerary.cities)}，非{home}市内行程")
    return reasons


def dedupe(records: list[Record]) -> tuple[list[Record], list[Record]]:
    """按发票号码去重；无号码时退化为主键 (金额, 开票日期, 销售方)。

    返回 (保留, 重复)。重复项不删除，落盘到存疑目录且不计入总额。
    """
    kept: list[Record] = []
    duplicated: list[Record] = []
    seen: dict[str, Record] = {}
    for record in records:
        key = record.invoice_number
        if key == "未识别" or not key:
            key = f"{record.amount:.2f}|{record.issue_date}|{record.seller}"
        if key in seen:
            duplicated.append(record)
            continue
        seen[key] = record
        kept.append(record)
    return kept, duplicated


def cluster_trips(records: list[Record], cfg: dict) -> list[TripCluster]:
    """差旅费按 (城市, 日期间隔 <= tripGapDays) 聚类成行程。"""
    gap_days = int((cfg.get("classify") or {}).get("tripGapDays", 4) or 4)
    home = (cfg.get("classify") or {}).get("homeCity", "杭州")
    by_city: dict[str, list[Record]] = {}
    for record in records:
        if record.purpose != PURPOSE_TRIP or record.kind == KIND_ENTERTAIN:
            continue
        city = _trip_city(record, home)
        if not city or city in {"城市待核", "地点待核", "铁路地点待核", "航程待核"}:
            continue
        by_city.setdefault(city, []).append(record)

    clusters: list[TripCluster] = []
    index = 0
    for city in sorted(by_city):
        items = sorted(by_city[city], key=lambda r: (_parse_date(r.travel_date) or date.max, r.invoice_number))
        current: list[Record] = []
        for record in items:
            if not current:
                current = [record]
                continue
            prev = _parse_date(current[-1].travel_date)
            cur = _parse_date(record.travel_date)
            if prev and cur and (cur - prev) <= timedelta(days=gap_days):
                current.append(record)
            else:
                index += 1
                clusters.append(_make_cluster(index, city, current, home))
                current = [record]
        if current:
            index += 1
            clusters.append(_make_cluster(index, city, current, home))
    return clusters


def _trip_city(record: Record, home: str) -> str:
    """记录 → 行程城市。铁路的 location 是"站点-站点"，必须先化成城市再比。"""
    if record.kind in (KIND_RAIL, KIND_RAIL_REFUND):
        return _rail_trip_city(record.location, home)
    return record.location or ""


def _rail_trip_city(location: str, home: str) -> str:
    """杭州西-温州南 → 温州（取第一个不是出发地城市的站点城市）。"""
    for part in (location or "").split("-"):
        part = part.strip()
        if not part:
            continue
        city = extract.station_city(part)
        if city and city != home:
            return city
    return ""


def _make_cluster(index: int, city: str, items: list[Record], home: str) -> TripCluster:
    dates = sorted(d for d in (_parse_date(r.travel_date) for r in items) if d)
    start = dates[0].isoformat() if dates else "日期待核"
    end = dates[-1].isoformat() if dates else "日期待核"
    has_home_leg = any(home in (r.location or "") for r in items)
    route = f"{home}-{city}-{home}" if has_home_leg else ""
    return TripCluster(index=index, city=city, start=start, end=end, route=route,
                       members=[r.invoice_number for r in items])


def apply_trips(records: list[Record], clusters: list[TripCluster], cfg: dict) -> None:
    """把行程聚类结果写回记录：填 trip_folder，未闭环的加存疑。"""
    home = (cfg.get("classify") or {}).get("homeCity", "杭州")
    lookup: dict[str, TripCluster] = {}
    for cluster in clusters:
        for number in cluster.members:
            lookup[number] = cluster

    for record in records:
        if record.purpose != PURPOSE_TRIP or record.kind == KIND_ENTERTAIN:
            continue
        cluster = lookup.get(record.invoice_number)
        if cluster is None:
            if R_TRIP_OPEN not in record.reasons and R_BUYER not in record.reasons:
                record.reasons.append(R_TRIP_OPEN)
            continue
        if cluster.closed:
            record.trip_folder = cluster.folder
        else:
            if R_TRIP_NO_HOME not in record.reasons:
                record.reasons.append(R_TRIP_NO_HOME)


def finalize(records: list[Record], cfg: dict) -> None:
    """定稿：排序、编号、状态、分类。"""
    records.sort(key=lambda r: (_sort_date(r), r.invoice_number))
    for seq, record in enumerate(records, 1):
        record.seq = seq
        record.status = "存疑" if record.reasons else "通过"
        record.included = record.status == "通过"
        record.category = f"{record.buyer}{record.purpose}"
        record.amount_scope = "计入" if record.included else "剔除"


def _sort_date(record: Record) -> str:
    parsed = _parse_date(record.travel_date) or _parse_date(record.issue_date)
    return parsed.isoformat() if parsed else "9999-99-99"
