"""预约报销单合并工具的离线测试（合成 PDF，零外部依赖）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import merge_reimbursement as mr  # noqa: E402
from tests import fixtures, support  # noqa: E402


def _pages(path: Path) -> int:
    import fitz

    with fitz.open(str(path)) as doc:
        return doc.page_count


class TestCollect(unittest.TestCase):
    def setUp(self):
        self.tmp = support.make_temp_dir("merge")
        self.folder = self.tmp / "预约号-12340001差旅报销"
        self.folder.mkdir()
        (self.folder / "12340001 _5226.25.pdf").write_bytes(b"form")
        for name in ("001_a_发票.pdf", "001_a_行程单.pdf", "002_b_发票.pdf",
                     "010_c_发票.pdf", "深圳_住宿.pdf"):
            (self.folder / name).write_bytes(b"x")

    def tearDown(self):
        support.clean_temp_dir(self.tmp)

    def test_form_is_excluded_by_reservation_number(self):
        keep, drop = mr.collect(self.folder, [])
        self.assertEqual([d.name for d in drop], ["12340001 _5226.25.pdf"])
        self.assertNotIn("12340001 _5226.25.pdf", [k.name for k in keep])

    def test_sorted_by_leading_seq_then_name(self):
        keep, _ = mr.collect(self.folder, [])
        names = [k.name for k in keep]
        # 001 发票 -> 001 行程单 -> 002 -> 010 -> 没有序号的排最后
        self.assertEqual(names, ["001_a_发票.pdf", "001_a_行程单.pdf", "002_b_发票.pdf",
                                 "010_c_发票.pdf", "深圳_住宿.pdf"])

    def test_extra_exclude_prefix(self):
        keep, drop = mr.collect(self.folder, ["002"])
        self.assertIn("002_b_发票.pdf", [d.name for d in drop])
        self.assertNotIn("002_b_发票.pdf", [k.name for k in keep])

    def test_underscore_prefixed_files_are_skipped(self):
        (self.folder / "_打印用合并PDF占位.pdf").write_bytes(b"x")
        keep, drop = mr.collect(self.folder, [])
        self.assertNotIn("_打印用合并PDF占位.pdf", [k.name for k in keep] + [d.name for d in drop])


class TestBundledName(unittest.TestCase):
    def test_uses_form_stem_and_drops_whitespace(self):
        form = Path("12340001 _5226.25.pdf")
        name = mr.bundled_name(Path("预约号-12340001差旅报销"), form)
        self.assertEqual(name, "12340001_5226.25_发票与行程单.pdf")

    def test_falls_back_to_folder_name(self):
        name = mr.bundled_name(Path("预约号-12340001差旅报销"), None)
        self.assertIn("预约号-12340001差旅报销", name)


class TestMergeEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = support.make_temp_dir("mergepdf")
        self.base = self.tmp / "已至报销单"
        self.folder = self.base / "预约号-12340001差旅报销"
        self.folder.mkdir(parents=True)
        # 预约单本身（不该被合并）；内容用几页 PDF 也能验证它确实没进来
        fixtures.make_pdf(self.folder / "12340001_100.00.pdf", fixtures.VAT_TAXI_INVOICE_TEXT)
        fixtures.make_pdf(self.folder / "001_a_发票.pdf", fixtures.VAT_TAXI_INVOICE_TEXT)
        fixtures.make_pdf(self.folder / "001_a_行程单.pdf", fixtures.ITINERARY_TEXT)
        sub = self.folder / "17_深圳差旅"
        sub.mkdir()
        fixtures.make_pdf(sub / "002_b_发票.pdf", fixtures.RAIL_INVOICE_TEXT)

    def tearDown(self):
        support.clean_temp_dir(self.tmp)

    def test_merge_writes_pdf_with_all_attachment_pages(self):
        keep, drop = mr.collect(self.folder, [])
        self.assertEqual(len(keep), 3)
        out = self.base / "_打印用合并PDF" / mr.bundled_name(self.folder, drop[0])
        pages = mr.merge(keep, out)
        self.assertTrue(out.is_file())
        self.assertEqual(pages, 3)
        self.assertEqual(_pages(out), 3)

    def test_bookmarks_are_added_per_source(self):
        from pypdf import PdfReader

        keep, drop = mr.collect(self.folder, [])
        out = self.tmp / "out.pdf"
        mr.merge(keep, out)
        reader = PdfReader(str(out))
        titles = [item.title for item in reader.outline if hasattr(item, "title")]
        self.assertEqual(len(titles), 3)

    def test_nested_subfolder_pdf_is_included(self):
        keep, _ = mr.collect(self.folder, [])
        self.assertIn("002_b_发票.pdf", [k.name for k in keep])


if __name__ == "__main__":
    unittest.main()
