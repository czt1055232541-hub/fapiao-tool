from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from fapiao import extract  # noqa: E402
from tests import fixtures, support  # noqa: E402


class TestPdfInvoice(unittest.TestCase):
    def test_vat_taxi_invoice(self):
        meta = extract.parse_pdf_invoice(fixtures.VAT_TAXI_INVOICE_TEXT)
        self.assertEqual(meta.invoice_number, "28147879666960508042")
        self.assertEqual(meta.issue_date, "2026-09-18")
        self.assertEqual(meta.total, 22.23)
        self.assertEqual(meta.buyer_tax_id, fixtures.BUYER_TAX_ID)
        self.assertEqual(meta.seller_tax_id, fixtures.SELLER_TAX_ID)
        self.assertIn("*交通运输服务*客运服务费", meta.item_names)
        self.assertFalse(meta.is_rail)

    def test_red_invoice_amount_is_negative(self):
        meta = extract.parse_pdf_invoice(fixtures.VAT_RED_INVOICE_TEXT)
        self.assertEqual(meta.total, -15.00)
        self.assertIn("*生产生活服务*餐费", meta.item_names)

    def test_rail_invoice_uses_travel_date(self):
        meta = extract.parse_pdf_invoice(fixtures.RAIL_INVOICE_TEXT)
        self.assertEqual(meta.invoice_number, "22883035638232649100")
        self.assertEqual(meta.issue_date, "2026-09-18")
        self.assertEqual(meta.travel_date, "2026-07-18")
        self.assertEqual(meta.total, 79.00)
        self.assertTrue(meta.is_rail)
        self.assertFalse(meta.is_refund)

    def test_rail_route_and_stations(self):
        self.assertEqual(extract.rail_stations(fixtures.RAIL_INVOICE_TEXT), ["杭州东", "上海南"])
        self.assertEqual(extract.rail_route(fixtures.RAIL_INVOICE_TEXT), "杭州东-上海南")
        self.assertEqual(extract.rail_cities(fixtures.RAIL_INVOICE_TEXT), ["杭州", "上海"])

    def test_rail_refund_flag(self):
        meta = extract.parse_pdf_invoice(fixtures.RAIL_REFUND_TEXT)
        self.assertTrue(meta.is_refund)
        self.assertEqual(meta.total, -17.50)


class TestItinerary(unittest.TestCase):
    def test_hangzhou(self):
        out = extract.parse_itinerary(fixtures.ITINERARY_TEXT)
        self.assertEqual(out.date, "2026-07-09")
        self.assertEqual(out.cities, ["杭州"])
        self.assertEqual(out.amount, 22.23)

    def test_shanghai(self):
        out = extract.parse_itinerary(fixtures.ITINERARY_TEXT_SHANGHAI)
        self.assertEqual(out.cities, ["上海"])
        self.assertEqual(out.amount, 36.86)


class TestXmlInvoice(unittest.TestCase):
    def test_parse(self):
        meta = extract.parse_xml_invoice(fixtures.TAXI_INVOICE_XML)
        self.assertIsNotNone(meta)
        self.assertEqual(meta.invoice_number, "28147879666960508042")
        self.assertEqual(meta.issue_date, "2026-09-18")
        self.assertEqual(meta.total, 22.23)
        self.assertEqual(meta.buyer_name, fixtures.BUYER_NAME)
        self.assertEqual(meta.buyer_tax_id, fixtures.BUYER_TAX_ID)
        self.assertEqual(meta.seller_name, fixtures.SELLER_NAME)

    def test_not_xml_returns_none(self):
        self.assertIsNone(extract.parse_xml_invoice("not xml at all"))


class TestReadPdfText(unittest.TestCase):
    def setUp(self):
        self.tmp = support.make_temp_dir("extract")

    def tearDown(self):
        support.clean_temp_dir(self.tmp)

    def test_roundtrip_through_real_pdf(self):
        path = self.tmp / "sample.pdf"
        fixtures.make_pdf(path, fixtures.VAT_TAXI_INVOICE_TEXT)
        text, extractor, error = extract.read_pdf_text(path)
        self.assertEqual(error, "")
        self.assertIn(extractor, {"pymupdf", "pypdf"})
        meta = extract.parse_pdf_invoice(text)
        self.assertEqual(meta.invoice_number, "28147879666960508042")
        self.assertEqual(meta.total, 22.23)

    def test_unreadable_file_reports_error(self):
        # 故意喂坏文件，PyMuPDF 会把解析错误打到自己 stderr，这里静音以免污染测试输出。
        try:
            import pymupdf

            pymupdf.TOOLS.mupdf_display_errors(False)
        except Exception:  # noqa: BLE001 - 静音失败不影响断言
            pass
        path = self.tmp / "broken.pdf"
        path.write_bytes(b"not a pdf")
        text, extractor, error = extract.read_pdf_text(path)
        self.assertEqual(text, "")
        self.assertEqual(extractor, "none")
        self.assertNotEqual(error, "")


if __name__ == "__main__":
    unittest.main()
