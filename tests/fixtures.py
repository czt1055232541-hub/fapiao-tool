"""合成测试夹具。

**故意不用真实发票样张**：真实 PDF/XML 含姓名、税号、手机号等个人信息，不得进入版本控制。
这里按真实样张的**文字排版顺序**复刻（解析器看到的就是这个顺序），身份信息一律用占位值。

排版依据：_probe 下真实样张的 PyMuPDF 抽取结果（只用于对齐格式，未入库）。
"""

from __future__ import annotations

from pathlib import Path

BUYER_NAME = "测试大学"
BUYER_TAX_ID = "12100000000000000A"
SELLER_NAME = "测试出行科技有限公司"
SELLER_TAX_ID = "91330100TEST0000001"

# 增值税电子发票（普通发票）：标签与值分离、竖排单字成行。
VAT_TAXI_INVOICE_TEXT = """电子发票（普通发票）
开票日期：
购
买
方
信
息
名称：
统一社会信用代码/纳税人识别号：
销
售
方
信
息
项目名称
合 计
价税合计（大写） （小写）
备
注
开票人：
名称：
统一社会信用代码/纳税人识别号：
发票号码：
21.58
测试大学
¥21.58
0.65
2026年09月18日
1
¥0.65
测试开票人
¥22.23
测试出行科技有限公司
贰拾贰元贰角叁分
*交通运输服务*客运服务费
21.58
3%
28147879666960508042
91330100TEST0000001
12100000000000000A
"""

# 红冲（负数）发票：金额与税额都是负的，价税合计绝对值最大。
VAT_RED_INVOICE_TEXT = """电子发票（普通发票）
开票日期：
购买方信息
名称：
销售方信息
项目名称
合 计
价税合计（大写） （小写）
开票人：
发票号码：
-14.85
测试大学
¥-14.85
-0.15
2026年09月20日
1
¥-0.15
测试开票人
¥-15.00
测试餐饮管理有限公司
壹拾伍元整
*生产生活服务*餐费
-14.85
1%
23957003100053509999
91330100TEST0000002
12100000000000000A
"""

# 铁路电子客票：全文里"开票日期"在前、"乘车日期"在后；电子客票号是 25 位，内嵌 20 位数字。
RAIL_INVOICE_TEXT = """国家税务总局
全
国
统
一
发
票
监
制
章
局
务
税
省
江
浙
开票日期:2026年09月18日
发票号码:22883035638232649100
杭州东站
上海南站
G7542
Hangzhoudong
Shanghainan
2026年07月18日
14:05开
13车17F号
票价:
二等座
1101011990****0000
测试人
电子客票号:9004190086071990283402026
始发改签
购买方名称:测试大学
统一社会信用代码:12100000000000000A
买票请到12306 发货请到95306
中国铁路祝您旅途愉快
电子发票（铁路电子客票）
￥79.00
"""

# 铁路退票（负数额度）：用于验证"动车高铁退改签"分支。
RAIL_REFUND_TEXT = """开票日期:2026年03月13日
发票号码:24255100406718649222
杭州东站
上海南站
G7501
2026年03月13日
差额退票
购买方名称:测试大学
统一社会信用代码:12100000000000000A
电子发票（铁路电子客票）
￥-17.50
"""

# 高德打车行程单。
ITINERARY_TEXT = """高德地图—打车——行程单
AMAP ITINERARY
申请时间：2026-09-18
行程时间：2026-07-09 18:30至2026-07-09 18:51
行程人手机号：13900000000
共计1单行程，合计22.23元
序号
服务商
车型
上车时间
城市
起点
终点
金额
1
测试出行
经济型
2026-07-09 18:30
杭州市
测试起点
测试终点
22.23元
页码： 1 / 1
"""

# 外地打车行程单：用于验证"非杭州 → 差旅费"分支。
ITINERARY_TEXT_SHANGHAI = """高德地图—打车——行程单
AMAP ITINERARY
申请时间：2026-07-19
行程时间：2026-07-18 21:10至2026-07-18 21:40
行程人手机号：13900000000
共计1单行程，合计36.86元
序号
服务商
车型
上车时间
城市
起点
终点
金额
1
测试出行
经济型
2026-07-18 21:10
上海市
测试起点
测试终点
36.86元
页码： 1 / 1
"""

TAXI_INVOICE_XML = """<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<EInvoice>
  <Header><EIid>28147879666960508042</EIid><EInvoiceTag>SWEI4400</EInvoiceTag><Version>0.32</Version></Header>
  <EInvoiceData>
    <SellerInformation>
      <SellerIdNum>91330100TEST0000001</SellerIdNum>
      <SellerName>测试出行科技有限公司</SellerName>
    </SellerInformation>
    <BuyerInformation>
      <BuyerIdNum>12100000000000000A</BuyerIdNum>
      <BuyerName>测试大学</BuyerName>
    </BuyerInformation>
    <BasicInformation>
      <TotalAmWithoutTax>21.58</TotalAmWithoutTax>
      <TotalTaxAm>0.65</TotalTaxAm>
      <TotalTax-includedAmount>22.23</TotalTax-includedAmount>
      <RequestTime>2026-09-18 13:28:54</RequestTime>
    </BasicInformation>
    <IssuItemInformation>
      <ItemName>*交通运输服务*客运服务费</ItemName>
      <Amount>21.58</Amount>
      <TaxRate>0.030000</TaxRate>
      <TotaltaxIncludedAmount>22.23</TotaltaxIncludedAmount>
    </IssuItemInformation>
  </EInvoiceData>
  <TaxSupervisionInfo>
    <InvoiceNumber>28147879666960508042</InvoiceNumber>
    <IssueTime>2026-09-18</IssueTime>
  </TaxSupervisionInfo>
</EInvoice>
"""

HOTEL_INVOICE_XML_ONLY = """<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<EInvoice>
  <Header><EIid>27823854920028326999</EIid></Header>
  <EInvoiceData>
    <SellerInformation>
      <SellerIdNum>91330100TEST0000003</SellerIdNum>
      <SellerName>测试酒店管理有限公司</SellerName>
    </SellerInformation>
    <BuyerInformation>
      <BuyerIdNum>12100000000000000A</BuyerIdNum>
      <BuyerName>测试大学</BuyerName>
    </BuyerInformation>
    <BasicInformation>
      <TotalAmWithoutTax>196.76</TotalAmWithoutTax>
      <TotalTaxAm>11.81</TotalTaxAm>
      <TotalTax-includedAmount>208.57</TotalTax-includedAmount>
      <RequestTime>2026-07-18 16:53:01</RequestTime>
    </BasicInformation>
    <IssuItemInformation>
      <ItemName>*生产生活服务*代订住宿费</ItemName>
      <Amount>196.76</Amount>
      <TaxRate>0.06</TaxRate>
      <TotaltaxIncludedAmount>208.57</TotaltaxIncludedAmount>
    </IssuItemInformation>
  </EInvoiceData>
  <TaxSupervisionInfo>
    <InvoiceNumber>27823854920028326999</InvoiceNumber>
    <IssueTime>2026-07-18</IssueTime>
  </TaxSupervisionInfo>
</EInvoice>
"""


def make_pdf(path: Path, text: str) -> None:
    """把夹具文字写成带文本层的 PDF，用于真实走一遍 read_pdf_text。"""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((36, 60), text, fontname="china-s", fontsize=8)
    doc.save(str(path))
    doc.close()


def test_config(tmp_root: Path) -> dict:
    return {
        "mail": {"mailbox": "INBOX", "extraLookbackDays": 0, "maxMailsPerRun": 100},
        "archive": {
            "root": str(tmp_root),
            "folderTemplate": "{runDate}月度发票归集({total:.2f})",
            "keepXml": True,
            "keepOfd": False,
            "includeSinceYear": None,
        },
        "classify": {
            "buyers": [
                {
                    "name": "测试大学",
                    "fullName": "测试大学",
                    "taxIds": [BUYER_TAX_ID],
                    "keywords": [BUYER_NAME],
                }
            ],
            "homeCity": "杭州",
            "tripGapDays": 4,
        },
        "report": {
            "summaryName": "00_审计摘要.json",
            "excelName": "00_发票审计明细及汇总.xlsx",
            "mergedFolder": "00_汇总发票",
        },
    }
