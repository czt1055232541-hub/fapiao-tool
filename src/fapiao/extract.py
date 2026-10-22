"""元数据抽取：XML 优先，PDF（PyMuPDF）兜底。

- XML 是税务/服务商给的权威结构化数据（含购买方税号），字段完整；
- PDF 版式文字碎片化（标签与值分离、竖排单字成行），只能按全文正则抽取；
- pypdf 在部分发票 PDF 上会抛异常（见 docs/环境坑位.md 1），因此用 fitz 做主提取器。
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree

from .models import ItineraryMeta, Meta
from .textutil import (
    CITY_NAMES,  # noqa: F401 - classify 通过 extract.CITY_NAMES 引用
    city_tokens,
    invoice_number,
    issue_date,
    item_names,
    last_cn_date,
    money_total,
)

PDF_EXT = ".pdf"
XML_EXT = ".xml"
OFD_EXT = ".ofd"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff"}

ITINERARY_MARKERS = ("AMAP ITINERARY", "行程单", "ITINERARY")

_SHANGHAI_HOTEL_RE = re.compile(r"入住时间[^\d]{0,10}(20\d{2})年(\d{1,2})月(\d{1,2})日")
_CHECKOUT_RE = re.compile(r"退房时间[^\d]{0,10}(20\d{2})年(\d{1,2})月(\d{1,2})日")
_ITIN_TIME_RE = re.compile(
    r"行程时间[:：]\s*(20\d{2})-(\d{2})-(\d{2}).*?至\s*(20\d{2})-(\d{2})-(\d{2})", re.S
)
_ITIN_TOTAL_RE = re.compile(r"合计\s*([0-9]+(?:\.[0-9]{1,2})?)\s*元")
_RAIL_STATION_RE = re.compile(r"^[\u4e00-\u9fff]{2,10}站$", re.M)
_ROUTE_RE = re.compile(r"([\u4e00-\u9fff]{2,6})-([\u4e00-\u9fff]{2,6})")


def read_pdf_text(path: Path) -> tuple[str, str, str]:
    """返回 (文本, 使用的提取器, 错误信息)。主用 fitz，失败再退 pypdf。"""
    errors: list[str] = []
    try:
        import fitz  # PyMuPDF

        with fitz.open(str(path)) as doc:
            text = "\n".join(page.get_text() or "" for page in doc)
        if text.strip():
            return text, "pymupdf", ""
        errors.append("pymupdf 返回空文本")
    except Exception as exc:  # noqa: BLE001 - 需要把任何解析失败都转成"无法解析"
        errors.append(f"pymupdf: {exc}")
    try:
        from pypdf import PdfReader

        text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
        if text.strip():
            return text, "pypdf", ""
        errors.append("pypdf 返回空文本")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"pypdf: {exc}")
    return "", "none", "；".join(errors)


def pdf_preview(path: Path, limit: int = 600) -> str:
    """只取首页开头一小段，用于判断这份 PDF 到底是发票还是行程单等。"""
    try:
        import fitz

        with fitz.open(str(path)) as doc:
            if doc.page_count:
                return (doc[0].get_text() or "")[:limit]
    except Exception:  # noqa: BLE001 - 预览失败不影响主流程，退回空串
        pass
    return ""


def parse_xml_invoice(text: str) -> Meta | None:
    """解析税务标准的电子发票 XML（EInvoice 结构）。"""
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return None

    def pick(path: str) -> str:
        node = root.find(path)
        if node is None or node.text is None:
            return ""
        return node.text.strip()

    meta = Meta(source_format="xml")
    meta.invoice_number = pick("TaxSupervisionInfo/InvoiceNumber") or pick("Header/EIid")
    meta.issue_date = pick("TaxSupervisionInfo/IssueTime")
    meta.buyer_name = pick("EInvoiceData/BuyerInformation/BuyerName")
    meta.buyer_tax_id = pick("EInvoiceData/BuyerInformation/BuyerIdNum").upper()
    meta.seller_name = pick("EInvoiceData/SellerInformation/SellerName")
    meta.seller_tax_id = pick("EInvoiceData/SellerInformation/SellerIdNum").upper()
    raw_total = pick("EInvoiceData/BasicInformation/TotalTax-includedAmount")
    if raw_total:
        try:
            meta.total = round(float(raw_total), 2)
        except ValueError:
            meta.total = None
    for node in root.findall(".//IssuItemInformation"):
        name = (node.findtext("ItemName") or "").strip()
        if name:
            meta.item_names.append(name)
    joined = " ".join(meta.item_names)
    meta.is_rail = "铁路" in joined or "铁路电子客票" in text
    meta.is_refund = any(word in joined for word in ("退票", "退改"))
    if not meta.invoice_number:
        return meta if (meta.total is not None or meta.buyer_name) else None
    return meta


def parse_pdf_invoice(text: str, filename: str = "") -> Meta:
    meta = Meta(source_format="pdf")
    meta.invoice_number = invoice_number(text)
    meta.issue_date = issue_date(text)
    meta.total = money_total(text)
    meta.item_names = item_names(text)
    meta.is_rail = "铁路电子客票" in text
    meta.is_refund = ("退票" in text) or ("差额退票" in text) or ("改签" in text and "退" in text)
    if meta.is_rail:
        # 铁路票全文里"开票日期"在前、"乘车日期"在后，取最后一个作为行程日期。
        meta.travel_date = last_cn_date(text)
    pairs = _name_code_pairs(text)
    meta.buyer_name, meta.buyer_tax_id = _pick(pairs, last=True)
    meta.seller_name, meta.seller_tax_id = _pick(pairs, last=False)
    if meta.total is None and not meta.invoice_number:
        meta.error = "PDF 未识别到发票号码与金额"
    return meta


def _name_code_pairs(text: str) -> list[tuple[str, str]]:
    """PDF 里"名称 + 统一社会信用代码"相邻成行出现，构成 (名称, 税号) 对。

    用前瞻式匹配：参考样张结尾是"名称\n销售方税号\n购买方税号"，
    普通 findall 会把销售方税号当成第一对的名称吃掉，导致购买方税号永远配不上。
    """
    return re.findall(
        r"([\u4e00-\u9fff（）()A-Za-z0-9·]{2,40})\n(?=([0-9A-Z]{15,20}))", text
    )


def _looks_like_name(value: str) -> bool:
    """税号本身（纯数字/大写字母）不算名称——参考样张里两行税号是紧挨着的。"""
    return len(re.findall(r"[\u4e00-\u9fff]", value)) >= 2


def _pick(pairs: list[tuple[str, str]], last: bool) -> tuple[str, str]:
    """参考样张顺序：销售方在前，购买方在后。样本不足时宁可不填。"""
    if not pairs:
        return "", ""
    if last:
        name, code = pairs[-1]
    else:
        if len(pairs) < 2:
            return "", ""
        name, code = pairs[0]
    return (name if _looks_like_name(name) else ""), code


def parse_itinerary(text: str) -> ItineraryMeta:
    """解析高德打车行程单：行程时间、城市、合计金额。"""
    out = ItineraryMeta()
    match = _ITIN_TIME_RE.search(text)
    if match:
        start = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        end = f"{match.group(4)}-{match.group(5)}-{match.group(6)}"
        out.date = start if start == end else f"{start}至{end}"
    total = _ITIN_TOTAL_RE.search(text)
    if total:
        out.amount = round(float(total.group(1)), 2)
    out.cities = city_tokens(text)
    return out


def rail_stations(text: str) -> list[str]:
    return [s[:-1] for s in _RAIL_STATION_RE.findall(text)]


def strip_station_direction(station: str) -> str:
    """杭州东 -> 杭州；上海南 -> 上海；北京南 -> 北京。"""
    if len(station) > 2 and station[-1] in "东西南北":
        return station[:-1]
    return station


def station_city(station: str) -> str:
    """站点名 → 城市名。

    先按已知城市前缀匹配（`上海虹桥` -> 上海），再退回"去掉方位字"的规则（`杭州东` -> 杭州）。
    """
    for city in sorted(CITY_NAMES, key=len, reverse=True):
        if station.startswith(city):
            return city
    return strip_station_direction(station)


def rail_route(text: str) -> str:
    stations = rail_stations(text)
    if len(stations) >= 2:
        return f"{stations[0]}-{stations[1]}"
    return ""


def rail_cities(text: str) -> list[str]:
    out: list[str] = []
    for station in rail_stations(text):
        city = strip_station_direction(station)
        if city not in out:
            out.append(city)
    return out


def hotel_stay(mail_text: str) -> tuple[str, str]:
    """从邮件正文取入住/退房日期。"""
    start = _SHANGHAI_HOTEL_RE.search(mail_text)
    end = _CHECKOUT_RE.search(mail_text)
    if not start:
        return "", ""
    start_s = "%04d-%02d-%02d" % (int(start.group(1)), int(start.group(2)), int(start.group(3)))
    if not end:
        return start_s, start_s
    end_s = "%04d-%02d-%02d" % (int(end.group(1)), int(end.group(2)), int(end.group(3)))
    return start_s, end_s


def hotel_city(*texts: str) -> str:
    for text in texts:
        for city in CITY_NAMES:
            if city in text:
                return city
    return ""


def flight_route(text: str) -> str:
    for match in _ROUTE_RE.finditer(text):
        left, right = match.group(1), match.group(2)
        if left in CITY_NAMES and right in CITY_NAMES:
            return f"{left}-{right}"
    return ""


# 「销售方地址:四川省遂宁市安居区…」——只认这一个字段，绝不全文扫
# （全文扫会被「中国农业银行成都双楠支行」误导成成都，实测踩过）
_SELLER_ADDR = re.compile(r"销售方地址[:：]\s*([^\n;；]{4,90})")
_ADDR_CITY = re.compile(r"([\u4e00-\u9fff]{2,8}?)市")

# 运单明细 / 对账单 / 结算单：有金额、有运单号，但**没有价税合计**，不是发票版式
_DOCUMENT_ONLY = ("运单明细", "对账单", "结算单", "费用明细清单", "账单明细")


def seller_address_city(text: str) -> str:
    """从「销售方地址」字段抽城市：四川省遂宁市… -> 遂宁。抽不出一律返回空串。"""
    match = _SELLER_ADDR.search(text or "")
    if not match:
        return ""
    cities = _ADDR_CITY.findall(match.group(1))
    if not cities:
        return ""
    city = cities[0]
    # 「四川省遂宁」-> 「遂宁」：剥掉省/自治区/特别行政区前缀
    for separator in ("自治区", "特别行政区", "省"):
        if separator in city:
            city = city.rsplit(separator, 1)[-1]
    return city


# 携程的「代订附加」把真正的行程写在备注里：
#   携程订单:1128151500112049,含2026-10-04 00:42成都天府国际机场接机
# 这是**结构化备注**，不是正文里散落的城市名，可以放心抽。
_CTRIP_ORDER = re.compile(
    r"携程订单[:：]\s*(\d+)[,，]?\s*含\s*"
    r"(\d{4})-(\d{2})-(\d{2})\s+(\d{2}:\d{2})\s*([^\n,，]{2,40})"
)
GROUND_SERVICES = ("接机", "送机", "接送", "接站", "送站")


def remark_service(text: str) -> dict:
    """从备注里抽出「什么时间、在哪个城市、做了什么」。抽不出返回空 dict。

    只认携程订单这种**有固定结构**的备注。不对整段正文扫城市名——
    那样会被开户行「成都双楠支行」骗（见 docs/环境坑位.md 第 24 条）。
    """
    match = _CTRIP_ORDER.search(text or "")
    if not match:
        return {}
    description = match.group(6).strip()
    service = next((word for word in GROUND_SERVICES if word in description), "")
    return {
        "order": match.group(1),
        "date": "%s-%s-%s" % (match.group(2), match.group(3), match.group(4)),
        "time": match.group(5),
        "desc": description,
        "city": hotel_city(description),
        "service": service,
    }


def looks_like_document_only(text: str, filename: str = "") -> bool:
    """判断这其实是一份「清单」而不是发票。

    顺丰的邮件会同时带真发票 PDF 和「顺丰电子发票 — 运单明细」PDF。后者也有发票号、
    也有金额栏，会被当成发票，然后与真发票撞发票号 -> 变成"重复发票不重复计入"的假存疑
    （2026-10 实测 3 条）。判据：命中清单类字样，且**正文没有「价税合计」**。
    """
    haystack = f"{filename or ''}\n{text or ''}"
    if not any(marker in haystack for marker in _DOCUMENT_ONLY):
        return False
    return "价税合计" not in (text or "")
