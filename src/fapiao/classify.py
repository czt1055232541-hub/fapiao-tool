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
R_NOT_AN_INVOICE = "附件不像发票，未纳入整理"
R_DOCUMENT_ONLY = "运单/对账清单不是发票，未纳入整理"

# 强特征：出现任意一个基本可以断定这是发票版式文件；普通 PDF（论文、策略稿）不会有。
INVOICE_MARKERS = ("电子发票", "铁路电子客票", "价税合计", "发票号码", "增值税", "发票代码")

PURPOSE_TRIP = "差旅费"
PURPOSE_ENTERTAIN = "招待费"
PURPOSE_TAXI_CITY = "杭州市内打车票"
# 非差旅类：技术服务、快递收派、代订附加等。这类票不该套"必须有杭州往返闭环"的规则。
PURPOSE_OTHER = "其他费用"

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
# 明确与出行无关的支出：技术服务、快递收派、会务培训等。
# 只有命中这些才归「其他费用」（不要求行程闭环）；「代订附加」这类跟着机票/住宿走的
# 旅行杂费**不在此列**，要让行程推断去认领它。
_NON_TRAVEL_WORDS = (
    "技术服务", "收派服务", "快递", "会务费", "培训费", "咨询服务",
    "信息服务", "软件", "技术服务费", "设计服务", "印刷",
    # 材料费：报销时要另附「自购材料清单」（docs/证明材料要求.md 第 2 节）
    "材料", "耗材", "货物", "试剂", "化学品", "元器件", "五金", "工具",
)


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


def is_probable_invoice(meta: Meta, raw_text: str, filename: str, has_xml: bool, has_image: bool) -> bool:
    """判断这组附件到底是不是一张发票。

    邮箱里混着各种 PDF（通知、论文、策略稿）。不设这道闸门，它们会被当成"抬头待核、
    金额 0"的发票塞进存疑目录，把真正需要人工看的东西淹掉。
    """
    if meta.invoice_number and meta.invoice_number != "未识别":
        return True
    if meta.total is not None:
        return True
    if has_xml or has_image:
        return True
    if "发票" in (filename or ""):
        return True
    haystack = raw_text or ""
    return any(marker in haystack for marker in INVOICE_MARKERS)


def prepare(
    group: Group, cfg: dict
) -> tuple[Record | None, Meta, list[Skipped], list[dict]]:
    """把一组附件转成一条待定稿记录；不像发票时返回 None 并留痕。

    第 4 个返回值是**支持材料**：像「顺丰运单明细」这种不是发票、但要跟着发票一起交的凭证。
    它们靠单子上印的发票号码挂回对应发票（见 pipeline 的 _attach_supporting）。
    """
    supporting: list[dict] = []
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

    if raw_text and extract.looks_like_document_only(raw_text, group.key):
        # 顺丰的「运单明细」不是发票，但**正是报销要的订单收据**：上面印着发票号码，
        # 按号码挂回对应发票，跟着它一起进汇总 PDF。
        number = extract.invoice_number_of(raw_text)
        if number:
            supporting.append({
                "kind": "订单收据",
                "invoice_number": number,
                "path": group.invoice.path if group.invoice else "",
                "filename": group.key,
            })
            skipped.append(
                Skipped(kind="attachment",
                        reason=f"运单/对账清单（已作为「订单收据」挂到发票 {number[-6:]} 后面）",
                        mail_uid=group.mail.uid, subject=group.mail.subject, filename=group.key)
            )
        else:
            skipped.append(
                Skipped(kind="attachment", reason=R_DOCUMENT_ONLY, mail_uid=group.mail.uid,
                        subject=group.mail.subject, filename=group.key)
            )
        return None, meta, skipped, supporting

    if not is_probable_invoice(meta, raw_text, group.key, xml_att is not None, image_att is not None):
        skipped.append(
            Skipped(kind="attachment", reason=R_NOT_AN_INVOICE, mail_uid=group.mail.uid,
                    subject=group.mail.subject, filename=group.key)
        )
        return None, meta, skipped, supporting

    record = _build_record(group, meta, itinerary, cfg, raw_text, invoice_pdf, itinerary_att, xml_att, image_att)
    return record, meta, skipped, supporting


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


def _is_non_travel(meta: Meta) -> bool:
    joined = " ".join(meta.item_names) or ""
    return any(word in joined for word in _NON_TRAVEL_WORDS)


def _is_airport_transfer(remark: dict) -> bool:
    """备注里写明机场接送（成都天府国际机场接机 / 杭州萧山国际机场送机）。"""
    return bool(remark.get("service")) and "机场" in str(remark.get("desc") or "")


def expense_item(meta: Meta, cfg: dict) -> str:
    """按「开具公司 / 项目名称」查表推断开支项目。

    表在 config.json 的 classify.expenseItems，例：
      [{"match": "顺丰", "item": "运费"}, {"match": "深度求索", "item": "Token 充值"}]
    按顺序匹配，第一个命中的胜出；没命中返回空串（**不猜**）。
    """
    rules = (cfg.get("classify") or {}).get("expenseItems") or []
    haystack = f"{meta.seller_name} {' '.join(meta.item_names)}"
    for rule in rules:
        keyword = str(rule.get("match") or "").strip()
        if keyword and keyword in haystack:
            return str(rule.get("item") or "").strip()
    return ""


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
    # 携程「代订附加」这类票，行程只写在备注里：含 2026-10-04 00:42成都天府国际机场接机
    # —— 机场接送就是地面交通，按打车处理，才能进对应行程或杭州市内打车票。
    if extract.remark_service(text).get("service"):
        return KIND_TAXI
    if "铁路电子客票" in text:
        return KIND_RAIL_REFUND if meta.is_refund else KIND_RAIL
    return KIND_OTHER


def _location_and_date(
    meta: Meta, itinerary: extract.ItineraryMeta, group: Group, kind: str, full_text: str
) -> tuple[str, str, str]:
    """返回 (地点, 行程日期, 日期来源)。

    **日期一律优先取票面备注里的真实时间，取不到才退回开票日** ——
    机票票面只有开票日，真正乘机日只写在备注里，用开票日会把整趟行程的日期搞错。
    """
    date_source = "开票日（推定）"
    mail_body = f"{group.mail.subject}\n{group.mail.text}"
    remark = extract.remark_service(full_text)
    if kind == KIND_TAXI:
        city = ("-".join(itinerary.cities) if itinerary.cities
                else remark.get("city") or "城市待核")
        if itinerary.date:
            return city, itinerary.date, "行程单"
        if remark.get("date"):
            return city, remark["date"], "备注"
        when = meta.travel_date or meta.issue_date or "日期待核"
        return city, when, ("票面" if meta.travel_date else "开票日（推定）")
    if kind in (KIND_RAIL, KIND_RAIL_REFUND):
        route = extract.rail_route(full_text)
        when = meta.travel_date or meta.issue_date or "日期待核"
        return (route or "铁路地点待核"), when, ("票面" if meta.travel_date else "开票日（推定）")
    if kind == KIND_FLIGHT:
        # 机票票面只有开票日，真正的乘机日与航段写在备注里
        flight = extract.remark_flight(full_text)
        route = (flight.get("route") or extract.flight_route(full_text)
                 or extract.flight_route(group.mail.subject))
        if flight.get("date"):
            return (route or "航程待核"), flight["date"], "备注"
        when = meta.travel_date or meta.issue_date or "日期待核"
        return (route or "航程待核"), when, ("票面" if meta.travel_date else "开票日（推定）")
    if kind == KIND_HOTEL:
        start, end = extract.hotel_stay(mail_body)
        # 顺序：邮件正文 > 销售方地址 > 备注。微信来的票没有正文，只能靠后两者
        city = (extract.hotel_city(group.mail.subject, group.mail.text)
                or extract.seller_address_city(full_text)
                or remark.get("city") or "地点待核")
        if start:
            when = f"{start}至{end}" if end and start != end else start
            return city, when, "邮件正文"
        return city, (meta.issue_date or "日期待核"), "开票日（推定）"
    if kind == KIND_ENTERTAIN:
        # 餐饮发票正文里不写城市，只能从主题/销售方名称里认（参考实现也是这么做的）。
        city = extract.hotel_city(group.mail.subject, group.mail.text, meta.seller_name) or "地点待核"
        return city, (meta.issue_date or "日期待核"), "开票日（推定）"
    # 其他类：先看「销售方地址」字段，再看结构化备注，都认不出才是地点待核
    city = extract.seller_address_city(full_text) or remark.get("city") or "地点待核"
    if remark.get("date"):
        return city, remark["date"], "备注"
    return city, (meta.issue_date or "日期待核"), "开票日（推定）"


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
    location, when, date_source = _location_and_date(meta, itinerary, group, kind, raw_text)
    buyer, _basis = detect_buyer(meta, raw_text, cfg)
    home = (cfg.get("classify") or {}).get("homeCity", "杭州")

    amount = meta.total
    if amount is None and itinerary.amount is not None:
        amount = itinerary.amount
    if amount is None and image_att is not None:
        amount = 0.0

    remark = extract.remark_service(raw_text)
    airport_transfer = _is_airport_transfer(remark)

    if kind == KIND_ENTERTAIN:
        purpose = PURPOSE_ENTERTAIN
    elif kind == KIND_OTHER and _is_non_travel(meta):
        # 技术服务 / 快递收派这类与出行无关的：归其他费用，不要求行程闭环
        purpose = PURPOSE_OTHER
    elif kind == KIND_TAXI and location == home and not airport_transfer:
        # 机场接送是"这趟出差的头尾"，不是日常通勤 —— 即使落在杭州也算差旅费
        purpose = PURPOSE_TAXI_CITY
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
    # 开支项目：按开具公司查表；备注：把日程摘要记下来供复核
    record.expense_item = expense_item(meta, cfg)
    record.remark = remark.get("desc", "")
    record.is_airport_transfer = airport_transfer
    record.date_source = date_source
    record.needs_material_list = purpose == PURPOSE_OTHER and any(
        word in " ".join(meta.item_names) for word in ("材料", "耗材", "货物", "试剂", "化学品", "元器件")
    )
    # 快递/运费：报销要附订单收据（顺丰邮件里那份「运单明细」就是）
    joined_items = " ".join(meta.item_names)
    record.needs_receipt = any(word in joined_items for word in ("收派服务", "快递", "运费")) \
        or "顺丰" in (meta.seller_name or "")
    # 单张打车费超过门槛的，汇总材料里必须附行程单（浙大报销口径，2026-10 定）。
    # 只在"打车"上要求：住宿发票的入住单、机票的行程单另有规则。
    threshold = float((cfg.get("classify") or {}).get("taxiItineraryThreshold") or 200)
    record.needs_itinerary = kind == KIND_TAXI and record.amount > threshold
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
    # 打车票没有行程单时，本来靠行程单认城市；城市已经从别处认出来了（备注 / 销售方地址），
    # 这条理由就不成立 —— 再挂"城市待核"就是误判。
    # 实测：携程「代订附加」备注里写着"成都天府国际机场接机"，城市明确，却被这条挡进存疑。
    if record.kind == KIND_TAXI and itinerary_att is None and record.location in ("城市待核", "地点待核"):
        reasons.append(R_TAXI_NO_CITY)
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
        if city == home:
            # 本市支出不是"行程"，不建行程文件夹、也不要求杭州往返闭环。
            continue
        by_city.setdefault(city, []).append(record)

    clusters: list[TripCluster] = []
    index = 0
    for city in sorted(by_city):
        # 住宿不能单独撑起一个「行程」：某城市只有住宿票时，那趟行程的依据根本不存在，
        # 交给 adopt_orphan_by_date 按日期归入真正有交通凭证的行程（2026-10 实测踩过）。
        if not any(r.kind in TRAFFIC_KINDS for r in by_city[city]):
            continue
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
    """记录 → 行程城市。

    location 可能是三种形态：单个城市（杭州 / 上海）、站点对（杭州西-温州南）、
    航段（深圳-杭州）。后两种都要拆开取"第一个不是出发地的城市"。
    """
    if record.trip_city:
        return record.trip_city
    if record.kind in (KIND_RAIL, KIND_RAIL_REFUND):
        return _rail_trip_city(record.location, home)
    parts = [p.strip() for p in (record.location or "").split("-") if p.strip()]
    if len(parts) > 1:
        for part in parts:
            city = extract.station_city(part) if record.kind == KIND_FLIGHT else part
            if city and city != home:
                return city
        return ""
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
        if _trip_city(record, home) == home:
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


TRAFFIC_KINDS = (KIND_TAXI, KIND_RAIL, KIND_RAIL_REFUND, KIND_FLIGHT)


def pair_round_trip_flights(records: list[Record], cfg: dict, gap_days: int = 4) -> int:
    """把「杭州->A」和「B->杭州」两张机票配成一趟往返。

    机票票面没有乘车日期，只有开票日；这两张是同一天开的，本来就是一趟
    （2026-10 实测：杭州-成都 + 重庆-杭州 各算一个行程，把住宿和附加费都拆散了）。
    配对后统一用去程目的地做行程城市，返回配对成功的对数。
    """
    home = (cfg.get("classify") or {}).get("homeCity", "杭州")
    outbound, inbound = [], []
    for record in records:
        if record.kind != KIND_FLIGHT or record.purpose != PURPOSE_TRIP or record.trip_city:
            continue
        parts = [p for p in (record.location or "").split("-") if p]
        if len(parts) != 2:
            continue
        if parts[0] == home and parts[1] != home:
            outbound.append((record, parts[1]))
        elif parts[1] == home and parts[0] != home:
            inbound.append((record, parts[0]))

    used: set[str] = set()
    pairs = 0
    for record, dest in outbound:
        base = _parse_date(record.travel_date)
        best = None
        for other, _origin in inbound:
            if other.invoice_number in used:
                continue
            when = _parse_date(other.travel_date)
            if not (base and when):
                continue
            gap = abs((when - base).days)
            if gap <= gap_days and (best is None or gap < best[0]):
                best = (gap, other)
        if best is None:
            continue
        other = best[1]
        used.add(other.invoice_number)
        other.trip_city = dest
        record.trip_city = dest
        other.inferred_from = f"往返机票配对：与 {record.location} 同批（相差 {best[0]} 天）"
        record.inferred_from = f"往返机票配对：与 {other.location} 同批"
        pairs += 1
    return pairs


def propagate_batch_trips(records: list[Record], cfg: dict, log=None) -> int:
    """同一销售方、同一开票日的一批发票，行程跟着这一批走。

    携程的机票和它的「代订附加」是同一天同一家开的；机票能定行程，附加费不能，
    但它们必然是同一趟的（2026-10 实测：270.00 / 82.00 两张附加费）。
    只在一批里**只有一个**已确定行程时才生效，避免乱推。
    """
    batches: dict[tuple[str, str], list[Record]] = {}
    for record in records:
        if record.purpose != PURPOSE_TRIP:
            continue
        if not record.seller or record.seller == "销售方待核":
            continue
        if not record.issue_date or record.issue_date == "日期待核":
            continue
        batches.setdefault((record.seller, record.issue_date), []).append(record)

    moved = 0
    for (seller, issue), items in batches.items():
        trips = {r.trip_folder for r in items if r.trip_folder}
        if len(trips) != 1:
            continue
        trip = trips.pop()
        for record in items:
            if record.trip_folder:
                continue
            record.trip_folder = trip
            record.reasons = [x for x in record.reasons if x not in (R_TRIP_OPEN, R_TRIP_NO_HOME)]
            record.inferred_from = f"同批推断：{seller} {issue} 同一批发票里已有票确定行程"
            moved += 1
            if log:
                log(f"[推断] {record.invoice_number} {record.amount:.2f} 元 -> {trip}（同批：{seller} {issue}）")
    return moved


def adopt_orphan_by_date(records: list[Record], clusters: list[TripCluster], cfg: dict,
                         gap_days: int = 7, log=None) -> int:
    """住宿票 / 机场接送票若没有行程归属，且窗口内只有**唯一一个**候选行程，就归入它。

    这两类天生与行程强相关，但票面不一定带得出城市：
    - 住宿票既没有城市也没有入住日期（微信来的更没有邮件正文）；
    - 机场接送票的城市是杭州（本城），按城市聚类分不进任何行程。
    只有当候选行程唯一时才动手（多个候选 = 猜，宁可留着）。
    """
    orphans = [
        r for r in records
        if r.purpose == PURPOSE_TRIP and not r.trip_folder
        and (r.kind == KIND_HOTEL or r.is_airport_transfer)
    ]
    moved = 0
    for record in orphans:
        when = _parse_date(record.issue_date)
        if not when:
            continue
        candidates = []
        for cluster in clusters:
            start, end = _parse_date(cluster.start), _parse_date(cluster.end)
            if not (start and end):
                continue
            gap = min(abs((when - start).days), abs((when - end).days))
            if gap <= gap_days:
                candidates.append((gap, cluster))
        if len(candidates) != 1:
            continue
        gap, cluster = candidates[0]
        record.trip_folder = cluster.folder
        record.reasons = [x for x in record.reasons if x not in (R_TRIP_OPEN, R_TRIP_NO_HOME)]
        record.inferred_from = (f"日期邻近推断：唯一候选行程 {cluster.folder}"
                                f"（开票日与该行程相差 {gap} 天）")
        moved += 1
        if log:
            log(f"[推断] {record.invoice_number} {record.amount:.2f} 元 -> {cluster.folder}（住宿按日期归入唯一候选行程）")
    return moved


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
