"""文本、金额、命名的公共工具。

这里的规则全部经过真实样张验证，见 docs/环境坑位.md 2/3。
"""

from __future__ import annotations

import re
from datetime import date, timedelta

# 常见城市，用于从邮件主题/正文/销售方名称里认城市（酒店与餐饮发票正文常不写"市"字）。
CITY_NAMES = (
    "北京 上海 广州 深圳 杭州 南京 苏州 无锡 宁波 温州 嘉兴 绍兴 金华 台州 湖州 衢州 丽水 舟山 "
    "合肥 武汉 长沙 南昌 福州 厦门 泉州 郑州 济南 青岛 烟台 潍坊 天津 石家庄 太原 西安 兰州 西宁 "
    "银川 乌鲁木齐 呼和浩特 沈阳 大连 长春 哈尔滨 成都 重庆 贵阳 昆明 南宁 海口 三亚 拉萨 珠海 "
    "佛山 东莞 中山 惠州 汕头 湛江 常州 南通 徐州 扬州 镇江 泰州 盐城 淮安 连云港 宿迁 芜湖 "
    "洛阳 开封 新乡 邯郸 保定 唐山 秦皇岛 临沂 淄博 济宁 威海 襄阳 宜昌 岳阳 株洲 湘潭 "
    "赣州 九江 桂林 柳州 北海 绵阳 德阳 宜宾 泸州 遵义 曲靖 大理 丽江 义乌 昆山 张家港 "
    "简阳 都江堰 余姚 慈溪 温岭 瑞安 乐清 诸暨 海宁 桐乡 平湖 长兴 德清 安吉 嵊州 临海 龙泉"
).split()

_INVALID_FS = re.compile(r'[<>:"/\\|?*]+')
_WS = re.compile(r"\s+")

# 价税合计 = 金额 + 税额，因此带 ¥/￥ 前缀的若干数值中，绝对值最大的那个就是价税合计。
_MONEY = re.compile(r"[¥￥]\s*(-?[0-9]+(?:\.[0-9]{1,2})?)")
# 20 位发票号码；用词边界排除 25 位电子客票号里嵌着的 20 位数字。
_INVOICE_NO = re.compile(r"(?<!\d)(\d{20})(?!\d)")
_CN_DATE = re.compile(r"(20\d{2})年(\d{1,2})月(\d{1,2})日")
_ISSUE_DATE = re.compile(r"开票日期[:：]?\s*(20\d{2})年(\d{1,2})月(\d{1,2})日")
# 行程单的城市是表格里的一个独立单元格，必须"单独成行"才算城市；
# 放宽成任意位置的 X市 会把"小果园生鲜连锁超市"这种店名吃进来（实测踩过）。
_CITY_LINE = re.compile(r"^[ \t]*([\u4e00-\u9fff]{2,8}市)[ \t]*$", re.M)
# 尾部用惰性匹配 + 前瞻停在"数字/行尾"，否则会把后面的单价格式一起吞进项目名。
_ITEM = re.compile(r"\*([^*\n]{2,20})\*([^*\n]{0,24}?)(?=\s*[0-9]|\n|$)")


def safe_name(value: object, limit: int = 150) -> str:
    """把任意字符串压成合法文件名片段（与参考案例实现保持一致）。"""
    text = _INVALID_FS.sub("-", str(value))
    text = _WS.sub("", text)
    return text.strip(" ._-")[:limit] or "待核"


def money_total(text: str) -> float | None:
    """抽取价税合计（含税总额）。取绝对值最大者，红冲发票保留负号。"""
    values = [float(v) for v in _MONEY.findall(text)]
    if not values:
        return None
    return round(max(values, key=abs), 2)


def invoice_number(text: str) -> str:
    match = _INVOICE_NO.search(text)
    return match.group(1) if match else ""


def _fmt(match: re.Match) -> str:
    return "%04d-%02d-%02d" % (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def issue_date(text: str) -> str:
    """开票日期：优先取"开票日期"标签后的日期（PDF 碎片化排版下标签与值仍同行）。"""
    match = _ISSUE_DATE.search(text)
    if match:
        return _fmt(match)
    match = _CN_DATE.search(text)
    return _fmt(match) if match else ""


def all_cn_dates(text: str) -> list[str]:
    return [_fmt(m) for m in _CN_DATE.finditer(text)]


def last_cn_date(text: str) -> str:
    """铁路电子客票全文里开票日期在前、乘车日期在后，取最后一个。"""
    dates = all_cn_dates(text)
    return dates[-1] if dates else ""


def city_tokens(text: str) -> list[str]:
    """抽取行程单里的城市（去"市"字）。

    先取"单独成行"的 X市；一个都没有时，退回在文本里找已知城市名。
    """
    seen: list[str] = []
    for raw in _CITY_LINE.findall(text):
        name = raw[:-1]
        if name not in seen:
            seen.append(name)
    if seen:
        # 行程单是多行表格，终点单元格里的"小果园生鲜连锁超市"也会单独成行；
        # 只要行里出现过已知城市，就只保留已知城市。
        known = [name for name in seen if name in CITY_NAMES]
        return known or seen
    return known_cities_in(text)


def known_cities_in(text: str) -> list[str]:
    """按已知城市表找出文本里出现的城市（按表内顺序，去重）。"""
    return [city for city in CITY_NAMES if city in text]


def item_names(text: str) -> list[str]:
    """抽取"*类别*项目名"，例如 *交通运输服务*客运服务费。"""
    out: list[str] = []
    for head, tail in _ITEM.findall(text):
        value = f"*{head}*{tail}".strip()
        if value not in out:
            out.append(value)
    return out


def strip_suffix(value: str, suffixes: tuple[str, ...]) -> str:
    for suffix in suffixes:
        if value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def fmt_date(value: date) -> str:
    return value.strftime("%Y-%m-%d")


def fmt_run_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def _previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def month_range(run_day: date, extra_lookback_days: int = 0) -> tuple[date, date]:
    """采集窗口 = 上月 21 日 ~ 本月 20 日（含两端）。

    返回 (since, before)，对应 IMAP 的 SINCE >= since 且 BEFORE < before，
    因此 before 取"21 日"。窗口两两相邻且不重叠，保证同一张发票只被收一次。

    非 20 号执行时取**最近一个已经走完的窗口**，而不是把半个窗口提前收掉，
    避免下次 20 号跑的时候重复收集。
    """
    if run_day.day >= 20:
        end_exclusive = run_day.replace(day=21)
        year, month = run_day.year, run_day.month
    else:
        end_exclusive = (run_day.replace(day=1) - timedelta(days=1)).replace(day=21)
        year, month = end_exclusive.year, end_exclusive.month
    prev_year, prev_month = _previous_month(year, month)
    start = date(prev_year, prev_month, 21)
    if extra_lookback_days > 0:
        start = start - timedelta(days=extra_lookback_days)
    return start, end_exclusive
