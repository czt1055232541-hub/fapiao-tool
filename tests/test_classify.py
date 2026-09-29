from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from fapiao import classify  # noqa: E402
from fapiao.models import Meta, Record  # noqa: E402
from tests import fixtures  # noqa: E402

CFG = {
    "classify": {
        "buyers": [
            {"name": "测试大学", "fullName": "测试大学",
             "taxIds": [fixtures.BUYER_TAX_ID], "keywords": [fixtures.BUYER_NAME]},
        ],
        "homeCity": "杭州",
        "tripGapDays": 4,
    }
}


def record(**kwargs) -> Record:
    base = dict(purpose=classify.PURPOSE_TRIP, travel_date="2026-07-18", location="上海",
                kind=classify.KIND_RAIL, amount=10.0, invoice_number="1" * 20)
    base.update(kwargs)
    return Record(**base)


class TestBuyer(unittest.TestCase):
    def test_by_tax_id(self):
        meta = Meta(buyer_tax_id=fixtures.BUYER_TAX_ID)
        self.assertEqual(classify.detect_buyer(meta, "", CFG)[0], "测试大学")

    def test_by_keyword_in_pdf_text(self):
        meta = Meta()
        self.assertEqual(classify.detect_buyer(meta, "抬头：测试大学 税号…", CFG)[0], "测试大学")

    def test_unknown_is_flagged(self):
        name, basis = classify.detect_buyer(Meta(), "某别的公司", CFG)
        self.assertEqual(name, "抬头待核")
        self.assertEqual(basis, "")


class TestKind(unittest.TestCase):
    def test_rail(self):
        self.assertEqual(classify.detect_kind(Meta(is_rail=True), False, ""), classify.KIND_RAIL)

    def test_rail_refund(self):
        meta = Meta(is_rail=True, is_refund=True)
        self.assertEqual(classify.detect_kind(meta, False, ""), classify.KIND_RAIL_REFUND)

    def test_hotel_beats_taxi(self):
        meta = Meta(item_names=["*生产生活服务*代订住宿费"])
        self.assertEqual(classify.detect_kind(meta, False, ""), classify.KIND_HOTEL)

    def test_flight(self):
        meta = Meta(item_names=["*经纪代理服务*代订机票"])
        self.assertEqual(classify.detect_kind(meta, False, ""), classify.KIND_FLIGHT)

    def test_entertain(self):
        meta = Meta(item_names=["*生产生活服务*餐费"])
        self.assertEqual(classify.detect_kind(meta, False, ""), classify.KIND_ENTERTAIN)

    def test_taxi_with_itinerary(self):
        meta = Meta(item_names=["*交通运输服务*客运服务费"])
        self.assertEqual(classify.detect_kind(meta, True, ""), classify.KIND_TAXI)

    def test_fallback(self):
        self.assertEqual(classify.detect_kind(Meta(), False, ""), classify.KIND_OTHER)


class TestDedupe(unittest.TestCase):
    def test_same_invoice_number_kept_once(self):
        kept, dup = classify.dedupe([
            record(invoice_number="2" * 20),
            record(invoice_number="2" * 20),
            record(invoice_number="3" * 20),
        ])
        self.assertEqual([r.invoice_number for r in kept], ["2" * 20, "3" * 20])
        self.assertEqual(len(dup), 1)

    def test_falls_back_to_amount_date_seller(self):
        first = record(invoice_number="未识别", amount=22.23, issue_date="2026-07-18", seller="A")
        second = record(invoice_number="未识别", amount=22.23, issue_date="2026-07-18", seller="A")
        kept, dup = classify.dedupe([first, second])
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(dup), 1)

    def test_different_amount_not_deduped(self):
        kept, dup = classify.dedupe([
            record(invoice_number="未识别", amount=22.23, issue_date="2026-07-18", seller="A"),
            record(invoice_number="未识别", amount=36.86, issue_date="2026-07-18", seller="A"),
        ])
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(dup), 0)


class TestTrips(unittest.TestCase):
    def test_closed_loop_creates_named_folder(self):
        records = [
            record(invoice_number="1" * 20, kind=classify.KIND_RAIL,
                   location="杭州东-上海南", travel_date="2026-07-18"),
            record(invoice_number="2" * 20, kind=classify.KIND_TAXI,
                   location="上海", travel_date="2026-07-18"),
        ]
        clusters = classify.cluster_trips(records, CFG)
        self.assertEqual(len(clusters), 1)
        self.assertTrue(clusters[0].closed)
        self.assertEqual(
            clusters[0].folder, "01_2026-07-18_至_2026-07-18_上海差旅_杭州-上海-杭州"
        )

    def test_without_home_leg_is_open(self):
        records = [record(invoice_number="3" * 20, kind=classify.KIND_TAXI,
                          location="上海", travel_date="2026-07-18")]
        clusters = classify.cluster_trips(records, CFG)
        self.assertEqual(len(clusters), 1)
        self.assertFalse(clusters[0].closed)
        self.assertEqual(classify._make_cluster(1, "上海", records, "杭州").folder,
                         "01_2026-07-18_至_2026-07-18_上海差旅")

    def test_gap_splits_trips(self):
        records = [
            record(invoice_number="4" * 20, location="上海", travel_date="2026-07-01"),
            record(invoice_number="5" * 20, location="上海", travel_date="2026-07-03"),
            record(invoice_number="6" * 20, location="上海", travel_date="2026-08-20"),
        ]
        clusters = classify.cluster_trips(records, CFG)
        self.assertEqual(len(clusters), 2)
        self.assertEqual(len(clusters[0].members), 2)

    def test_unknown_city_not_clustered(self):
        records = [record(invoice_number="7" * 20, location="地点待核", travel_date="2026-07-18")]
        self.assertEqual(classify.cluster_trips(records, CFG), [])

    def test_apply_trips_marks_reasons(self):
        closed = [record(invoice_number="8" * 20, kind=classify.KIND_RAIL,
                         location="杭州西-温州南", travel_date="2026-07-18")]
        clusters = classify.cluster_trips(closed, CFG)
        classify.apply_trips(closed, clusters, CFG)
        self.assertEqual(closed[0].trip_folder, "01_2026-07-18_至_2026-07-18_温州差旅_杭州-温州-杭州")

        open_case = [record(invoice_number="9" * 20, kind=classify.KIND_TAXI,
                            location="上海", travel_date="2026-07-18")]
        clusters = classify.cluster_trips(open_case, CFG)
        classify.apply_trips(open_case, clusters, CFG)
        self.assertEqual(open_case[0].trip_folder, "")
        self.assertIn(classify.R_TRIP_NO_HOME, open_case[0].reasons)

    def test_apply_trips_unclustered_record(self):
        lonely = [record(invoice_number="0" * 20, kind=classify.KIND_TAXI,
                         location="地点待核", travel_date="2026-07-18")]
        classify.apply_trips(lonely, [], CFG)
        self.assertIn(classify.R_TRIP_OPEN, lonely[0].reasons)


class TestFinalize(unittest.TestCase):
    def test_numbering_status_and_category(self):
        clean = record(invoice_number="1" * 20, buyer="测试大学",
                       purpose=classify.PURPOSE_TAXI_CITY, travel_date="2026-07-18")
        flagged = record(invoice_number="2" * 20, buyer="测试大学",
                         purpose=classify.PURPOSE_TRIP, travel_date="2026-07-19")
        flagged.reasons = [classify.R_BUYER]
        classify.finalize([clean, flagged], CFG)
        self.assertEqual([r.seq for r in (clean, flagged)], [1, 2])
        self.assertEqual(clean.status, "通过")
        self.assertTrue(clean.included)
        self.assertEqual(clean.category, "测试大学杭州市内打车票")
        self.assertEqual(flagged.status, "存疑")
        self.assertFalse(flagged.included)


if __name__ == "__main__":
    unittest.main()
