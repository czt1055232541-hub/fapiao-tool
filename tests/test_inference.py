"""行程归属推断（2026-10 新增）：证据照进来，别让票白进存疑。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from fapiao import classify, extract  # noqa: E402
from fapiao.models import Meta, Record  # noqa: E402

CFG = {"classify": {"homeCity": "杭州", "tripGapDays": 4}}


def rec(**kw) -> Record:
    base = dict(buyer="浙江大学", purpose=classify.PURPOSE_TRIP, amount=1.0,
                invoice_number="X", issue_date="2026-10-08", seller="某公司")
    base.update(kw)
    return Record(**base)


# 携程真实票据的备注（已脱敏：订单号换成占位）
CTRIP_REMARK = "扣除额:267.3。 | 携程订单:1128151500112049,含2026-10-04 00:42成都天府国际机场接机"


class TestRemarkService(unittest.TestCase):
    """备注里有结构化行程：什么时间、在哪个城市、做了什么。"""

    def test_parses_ctrip_remark(self):
        info = extract.remark_service(CTRIP_REMARK)
        self.assertEqual(info["date"], "2026-10-04")
        self.assertEqual(info["time"], "00:42")
        self.assertEqual(info["city"], "成都")
        self.assertEqual(info["service"], "接机")
        self.assertIn("天府国际机场", info["desc"])

    def test_hangzhou_pickup(self):
        info = extract.remark_service("携程订单:1,含2026-10-05 19:05杭州萧山国际机场接机")
        self.assertEqual(info["city"], "杭州")
        self.assertEqual(info["service"], "接机")

    def test_plain_text_returns_empty(self):
        self.assertEqual(extract.remark_service("随便一段没有结构的备注"), {})

    def test_remark_without_city_still_reports_service(self):
        info = extract.remark_service("携程订单:1,含2026-10-04 08:00某某机场接机")
        self.assertEqual(info["service"], "接机")
        self.assertEqual(info["city"], "")


class TestRemarkTurnsIntoTaxi(unittest.TestCase):
    def test_airport_pickup_is_ground_transport(self):
        """携程「代订附加」本身看不出是交通，但备注写着接机 —— 按打车处理，
        才能进对应行程（成都）或杭州市内打车票（杭州）。"""
        meta = Meta(item_names=["*生产生活服务*代订附加"])
        self.assertEqual(classify.detect_kind(meta, False, CTRIP_REMARK), classify.KIND_TAXI)

    def test_without_remark_stays_other(self):
        meta = Meta(item_names=["*生产生活服务*代订附加"])
        self.assertEqual(classify.detect_kind(meta, False, "无备注"), classify.KIND_OTHER)


class TestAirportTransfer(unittest.TestCase):
    """机场接送是"这趟出差的头尾"，算差旅，不算杭州市内通勤。"""

    def test_pickup_at_airport_is_flagged(self):
        info = extract.remark_service(CTRIP_REMARK)
        self.assertTrue(classify._is_airport_transfer(info))

    def test_service_without_airport_is_not_flagged(self):
        self.assertFalse(classify._is_airport_transfer({"service": "接机", "desc": "某某酒店接机"}))

    def test_empty_remark_is_not_flagged(self):
        self.assertFalse(classify._is_airport_transfer({}))

    def test_airport_transfer_is_adopted_by_date(self):
        """城市是杭州（本城），按城市聚类分不进任何行程，只能按日期归入。"""
        cluster = classify.TripCluster(
            index=1, city="成都", start="2026-10-04", end="2026-10-08",
            route="杭州-成都-杭州", members=["FLIGHT"])
        transfer = rec(kind=classify.KIND_TAXI, invoice_number="T", location="杭州",
                       travel_date="2026-10-05", is_airport_transfer=True,
                       reasons=[classify.R_TRIP_OPEN])
        self.assertEqual(classify.adopt_orphan_by_date([transfer], [cluster], CFG), 1)
        self.assertEqual(transfer.trip_folder, "01_2026-10-04_至_2026-10-08_成都差旅_杭州-成都-杭州")

    def test_plain_taxi_is_not_adopted(self):
        cluster = classify.TripCluster(
            index=1, city="成都", start="2026-10-04", end="2026-10-08",
            route="杭州-成都-杭州", members=["FLIGHT"])
        taxi = rec(kind=classify.KIND_TAXI, invoice_number="T2", location="杭州",
                   travel_date="2026-10-05", reasons=[classify.R_TRIP_OPEN])
        self.assertEqual(classify.adopt_orphan_by_date([taxi], [cluster], CFG), 0)


class TestSupportingDocs(unittest.TestCase):
    """顺丰的「运单明细」不是发票，但是报销要的订单收据，要挂在发票后面。"""

    def test_express_invoice_needs_receipt(self):
        meta = Meta(seller_name="浙江顺丰速运有限公司",
                    item_names=["*生产生活服务*收派服务"])
        self.assertTrue(classify._is_non_travel(meta))
        joined = " ".join(meta.item_names)
        self.assertTrue(any(w in joined for w in ("收派服务", "快递", "运费")))

    def test_missing_docs_reports_receipt_until_attached(self):
        from fapiao import report

        record = rec(needs_receipt=True)
        self.assertEqual(report.missing_docs(record), "订单收据")

        record.supporting = [{"kind": "订单收据", "archived": "x/订单收据.pdf"}]
        self.assertEqual(report.missing_docs(record), "")

    def test_attach_by_invoice_number(self):
        from fapiao.pipeline import _attach_supporting

        invoice = rec(invoice_number="26337000000859509054", needs_receipt=True)
        skipped = {"attachments": []}
        _attach_supporting(
            [invoice],
            [{"kind": "订单收据", "invoice_number": "26337000000859509054", "path": "x.pdf"}],
            skipped, log=lambda *_a, **_k: None)
        self.assertEqual(len(invoice.supporting), 1)
        self.assertEqual(skipped["attachments"], [])

    def test_unmatched_receipt_is_traced_not_dropped(self):
        from fapiao.pipeline import _attach_supporting

        skipped = {"attachments": []}
        _attach_supporting([], [{"kind": "订单收据", "invoice_number": "999", "path": "x.pdf"}],
                           skipped, log=lambda *_a, **_k: None)
        self.assertEqual(len(skipped["attachments"]), 1)
        self.assertIn("找不到对应发票", skipped["attachments"][0]["reason"])

    def test_invoice_number_of_reads_the_manifest(self):
        manifest = "1-1 顺丰电子发票 — 运单明细 开票时间 发票号码 2026/10/08 26337000000859509879"
        self.assertEqual(extract.invoice_number_of(manifest), "26337000000859509879")
        self.assertEqual(extract.invoice_number_of("没有号码"), "")


class TestMissingDocs(unittest.TestCase):
    """单张大额打车要行程单；材料费要自购材料清单（依据见 docs/证明材料要求.md）。"""

    def test_high_value_taxi_without_itinerary_needs_one(self):
        from fapiao import report

        record = rec(kind=classify.KIND_TAXI, amount=270.0, needs_itinerary=True)
        self.assertEqual(report.missing_docs(record), "行程单")

    def test_high_value_taxi_with_itinerary_is_complete(self):
        from fapiao import report

        record = rec(kind=classify.KIND_TAXI, amount=270.0, needs_itinerary=True,
                     archived_itinerary="x/行程单.pdf")
        self.assertEqual(report.missing_docs(record), "")

    def test_low_value_taxi_needs_nothing(self):
        from fapiao import report

        self.assertEqual(report.missing_docs(rec(kind=classify.KIND_TAXI, amount=19.46)), "")

    def test_material_invoice_needs_list(self):
        from fapiao import report

        self.assertEqual(report.missing_docs(rec(needs_material_list=True)), "自购材料清单")


class TestExpenseItemTable(unittest.TestCase):
    CFG2 = {"classify": {"expenseItems": [
        {"match": "顺丰", "item": "运费"},
        {"match": "深度求索", "item": "Token 充值"},
    ]}}

    def _meta(self, seller, item):
        return Meta(seller_name=seller, item_names=[item])

    def test_express_delivery_is_freight(self):
        meta = self._meta("浙江顺丰速运有限公司", "*生产生活服务*收派服务")
        self.assertEqual(classify.expense_item(meta, self.CFG2), "运费")

    def test_deepseek_is_token_topup(self):
        meta = self._meta("杭州深度求索人工智能基础技术研究有限公司", "*生产生活服务*技术服务")
        self.assertEqual(classify.expense_item(meta, self.CFG2), "Token 充值")

    def test_unknown_supplier_is_not_guessed(self):
        meta = self._meta("某不知名公司", "*生产生活服务*技术服务")
        self.assertEqual(classify.expense_item(meta, self.CFG2), "")

    def test_first_match_wins(self):
        cfg = {"classify": {"expenseItems": [
            {"match": "科技", "item": "第一"}, {"match": "公司", "item": "第二"}]}}
        meta = self._meta("某科技有限公司", "*x*服务")
        self.assertEqual(classify.expense_item(meta, cfg), "第一")

    def test_empty_table_returns_blank(self):
        meta = self._meta("浙江顺丰速运有限公司", "*生产生活服务*收派服务")
        self.assertEqual(classify.expense_item(meta, CFG), "")


class TestSellerAddressCity(unittest.TestCase):
    def test_extracts_from_seller_address_only(self):
        text = "销售方地址:四川省遂宁市安居区某某路1号;    电话:13900000000;"
        self.assertEqual(extract.seller_address_city(text), "遂宁")

    def test_does_not_scan_whole_text(self):
        """全文扫会被开户行名字带偏（实测：成都双楠支行 -> 成都）。"""
        text = "销方开户银行:中国农业银行股份有限公司成都双楠支行;    银行账号:22320601040017798"
        self.assertEqual(extract.seller_address_city(text), "")

    def test_missing_field(self):
        self.assertEqual(extract.seller_address_city("随便一段没有地址的文字"), "")


class TestDocumentOnly(unittest.TestCase):
    def test_shipment_manifest_is_not_an_invoice(self):
        text = "1-1 顺丰电子发票 — 运单明细 # 运单号码 寄件时间 1 SF0210708664075 2026/09/21"
        self.assertTrue(extract.looks_like_document_only(text, "顺丰运单明细.pdf"))

    def test_real_invoice_is_not_flagged(self):
        text = "电子发票（普通发票） 项目名称 *生产生活服务*收派服务 价税合计（大写）（小写）"
        self.assertFalse(extract.looks_like_document_only(text, "电子发票.pdf"))

    def test_manifest_without_marker_is_not_flagged(self):
        self.assertFalse(extract.looks_like_document_only("普通的一段文字", "a.pdf"))


class TestNonTravel(unittest.TestCase):
    def test_technical_service_is_non_travel(self):
        self.assertTrue(classify._is_non_travel(Meta(item_names=["*生产生活服务*技术服务"])))

    def test_express_delivery_is_non_travel(self):
        self.assertTrue(classify._is_non_travel(Meta(item_names=["*生产生活服务*收派服务"])))

    def test_ctrip_ancillary_is_travel(self):
        """代订附加跟着机票走，不能归成其他费用，否则行程推断认领不到它。"""
        self.assertFalse(classify._is_non_travel(Meta(item_names=["*生产生活服务*代订附加"])))


class TestRoundTripFlights(unittest.TestCase):
    def test_outbound_and_return_merge_into_one_trip(self):
        out = rec(kind=classify.KIND_FLIGHT, location="杭州-成都", trip_city="",
                  invoice_number="A", travel_date="2026-10-08", seller="携程")
        back = rec(kind=classify.KIND_FLIGHT, location="重庆-杭州", trip_city="",
                   invoice_number="B", travel_date="2026-10-08", seller="携程")
        pairs = classify.pair_round_trip_flights([out, back], CFG)
        self.assertEqual(pairs, 1)
        self.assertEqual(out.trip_city, "成都")
        self.assertEqual(back.trip_city, "成都")
        self.assertIn("往返机票配对", out.inferred_from)

    def test_far_apart_dates_do_not_pair(self):
        out = rec(kind=classify.KIND_FLIGHT, location="杭州-北京", invoice_number="A",
                  travel_date="2026-10-01")
        back = rec(kind=classify.KIND_FLIGHT, location="上海-杭州", invoice_number="B",
                   travel_date="2026-10-20")
        self.assertEqual(classify.pair_round_trip_flights([out, back], CFG), 0)
        self.assertEqual(out.trip_city, "")


class TestBatchPropagation(unittest.TestCase):
    def test_same_seller_same_day_follows_the_trip(self):
        flight = rec(kind=classify.KIND_FLIGHT, invoice_number="A", seller="上海华程西南国际旅行社有限公司",
                     issue_date="2026-10-08", trip_folder="01_成都差旅")
        extra = rec(kind=classify.KIND_OTHER, invoice_number="B", seller="上海华程西南国际旅行社有限公司",
                    issue_date="2026-10-08", reasons=[classify.R_TRIP_OPEN])
        moved = classify.propagate_batch_trips([flight, extra], CFG)
        self.assertEqual(moved, 1)
        self.assertEqual(extra.trip_folder, "01_成都差旅")
        self.assertEqual(extra.reasons, [], "推断成功后要把它从存疑原因里摘掉")

    def test_ambiguous_batch_is_left_alone(self):
        a = rec(kind=classify.KIND_FLIGHT, invoice_number="A", seller="S", issue_date="2026-10-08",
                trip_folder="01_甲")
        b = rec(kind=classify.KIND_FLIGHT, invoice_number="B", seller="S", issue_date="2026-10-08",
                trip_folder="02_乙")
        c = rec(kind=classify.KIND_OTHER, invoice_number="C", seller="S", issue_date="2026-10-08")
        self.assertEqual(classify.propagate_batch_trips([a, b, c], CFG), 0)
        self.assertEqual(c.trip_folder, "", "一批对应多个行程时不能猜")

    def test_non_travel_purpose_is_out_of_scope(self):
        flight = rec(kind=classify.KIND_FLIGHT, invoice_number="A", seller="S", issue_date="2026-10-08",
                     trip_folder="01_甲")
        other = rec(kind=classify.KIND_OTHER, purpose=classify.PURPOSE_OTHER,
                    invoice_number="B", seller="S", issue_date="2026-10-08")
        self.assertEqual(classify.propagate_batch_trips([flight, other], CFG), 0)


class TestAdoptOrphanHotel(unittest.TestCase):
    def _cluster(self, index=1, city="成都", start="2026-10-08", end="2026-10-08", route="杭州-成都-杭州"):
        return classify.TripCluster(index=index, city=city, start=start, end=end, route=route,
                                    members=["FLIGHT"])

    def test_single_candidate_absorbs_the_hotel(self):
        hotel = rec(kind=classify.KIND_HOTEL, invoice_number="H", issue_date="2026-10-06",
                    location="遂宁", reasons=[classify.R_TRIP_OPEN])
        moved = classify.adopt_orphan_by_date([hotel], [self._cluster()], CFG)
        self.assertEqual(moved, 1)
        self.assertEqual(hotel.trip_folder, "01_2026-10-08_至_2026-10-08_成都差旅_杭州-成都-杭州")
        self.assertIn("日期邻近推断", hotel.inferred_from)
        self.assertEqual(hotel.reasons, [])

    def test_multiple_candidates_is_not_guessed(self):
        hotel = rec(kind=classify.KIND_HOTEL, invoice_number="H", issue_date="2026-10-08",
                    location="遂宁", reasons=[classify.R_TRIP_OPEN])
        clusters = [self._cluster(1), self._cluster(2, city="重庆")]
        self.assertEqual(classify.adopt_orphan_by_date([hotel], clusters, CFG), 0)
        self.assertEqual(hotel.trip_folder, "")

    def test_too_far_in_time_is_not_adopted(self):
        hotel = rec(kind=classify.KIND_HOTEL, invoice_number="H", issue_date="2026-12-01",
                    location="遂宁", reasons=[classify.R_TRIP_OPEN])
        self.assertEqual(classify.adopt_orphan_by_date([hotel], [self._cluster()], CFG), 0)


class TestHotelCannotMakeItsOwnTrip(unittest.TestCase):
    def test_city_with_only_hotel_makes_no_cluster(self):
        hotel = rec(kind=classify.KIND_HOTEL, invoice_number="H", location="遂宁",
                    travel_date="2026-10-06")
        self.assertEqual(classify.cluster_trips([hotel], CFG), [])

    def test_city_with_traffic_still_clusters(self):
        flight = rec(kind=classify.KIND_FLIGHT, invoice_number="F", location="杭州-成都",
                     travel_date="2026-10-08", trip_city="成都")
        self.assertEqual(len(classify.cluster_trips([flight], CFG)), 1)


if __name__ == "__main__":
    unittest.main()
