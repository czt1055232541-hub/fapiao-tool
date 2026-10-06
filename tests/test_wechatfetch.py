"""微信来源适配器的离线测试（零网络、零真实入站目录）。

守住三条已定口径：只收 PDF、图片不收只留痕、同内容按 md5 去重。
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from fapiao import wechatfetch  # noqa: E402
from tests import fixtures, support  # noqa: E402

SINCE = date(2026, 9, 21)
BEFORE = date(2026, 10, 21)


class TestWeChatCollect(unittest.TestCase):
    def setUp(self):
        self.tmp = support.make_temp_dir("wechat")
        self.inbound = self.tmp / "inbound"
        self.inbound.mkdir()

        # 两个真发票 PDF（文件名沿用桥接的真实形状）
        fixtures.make_pdf(self.inbound / "2026-10-06_7513183848471655000_1_313f47.pdf",
                          fixtures.VAT_TAXI_INVOICE_TEXT)
        fixtures.make_pdf(self.inbound / "2026-10-06_7513183848874339000_1_333d8e.pdf",
                          fixtures.RAIL_INVOICE_TEXT)

        # 同一张图被桥接存成 .jpg + .png（内容完全相同）→ md5 去重只留一份
        (self.inbound / "2026-09-30_msgid0002_1_5c7ba6.jpg").write_bytes(b"PNGDATA-identical")
        (self.inbound / "2026-09-30_msgid0002_1_5c7ba6.png").write_bytes(b"PNGDATA-identical")
        # 另一张独立的聊天图
        (self.inbound / "2026-10-04_msgid0004_1_7d8d53.png").write_bytes(b"OTHER-IMAGE")

        # 语音原声：非文档扩展名，只记计数
        (self.inbound / "2026-09-30_msgid0001_1_7a056d.silk").write_bytes(b"SILK1")
        (self.inbound / "2026-09-30_7511163144150215000_1_28128f.silk").write_bytes(b"SILK2")

        # OFD：微信来源不收，进跳过留痕
        (self.inbound / "2026-10-06_7513183848999999000_1_aaaaaaaa.ofd").write_bytes(b"OFDDATA")

        # 窗口外：整月的文件都堆在同一目录里，必须静默略过
        fixtures.make_pdf(self.inbound / "2026-08-15_7500000000000000000_1_old001.pdf",
                          fixtures.VAT_TAXI_INVOICE_TEXT)

    def tearDown(self):
        support.clean_temp_dir(self.tmp)

    def _collect(self, **kwargs):
        return wechatfetch.collect(self.inbound, SINCE, BEFORE, log=lambda *_a, **_k: None, **kwargs)

    def test_only_pdf_becomes_invoice(self):
        result = self._collect()
        self.assertEqual(result.invoice_files, 2)
        self.assertEqual(len(result.groups), 2)
        for group in result.groups:
            self.assertIsNotNone(group.invoice)
            self.assertEqual(group.invoice.role, "pdf")
            self.assertEqual(group.mail.mailbox, "WECHAT")
            self.assertEqual(group.mail.text, "", "微信来源没有邮件正文，不能凭空造上下文")

    def test_window_excludes_other_months(self):
        result = self._collect()
        names = [g.invoice.filename for g in result.groups]
        self.assertNotIn("2026-08-15_7500000000000000000_1_old001.pdf", names)
        # 窗口内共 8 个文件（2 pdf + 3 图 + 2 silk + 1 ofd），窗口外那个不算
        self.assertEqual(result.files_in_window, 8)

    def test_images_are_skipped_not_archived(self):
        """图片不收，但必须留痕 —— 绝不能变成存疑记录。"""
        result = self._collect()
        by_reason = {}
        for item in result.skipped:
            by_reason.setdefault(item["reason"], []).append(item["filename"])
        # 两个"独立的图片内容"以图片理由跳过（另一份同内容的已被 md5 去重吃掉）
        self.assertEqual(len(by_reason.get(wechatfetch.R_IMAGE_NOT_ACCEPTED, [])), 2)
        # 带图片扩展名的文件一共 3 个进留痕：2 个内容不同的图片 + 1 个 md5 重复
        image_ext_skips = [s for s in result.skipped if s["filename"].endswith((".jpg", ".png"))]
        self.assertEqual(len(image_ext_skips), 3)
        self.assertFalse(any(g.invoice and g.invoice.filename.endswith(".png") for g in result.groups))

    def test_no_image_becomes_a_record(self):
        result = self._collect()
        for group in result.groups:
            self.assertFalse(group.invoice.filename.lower().endswith((".jpg", ".png", ".jpeg")))

    def test_md5_dedupe_keeps_one_of_identical_pair(self):
        result = self._collect()
        dupes = [s for s in result.skipped if "md5" in s["reason"]]
        self.assertEqual(len(dupes), 1)
        self.assertTrue(dupes[0]["filename"].endswith(".png") or dupes[0]["filename"].endswith(".jpg"))
        self.assertIn("保留", dupes[0]["reason"])

    def test_ofd_skipped_with_reason(self):
        result = self._collect()
        ofd = [s for s in result.skipped if s["filename"].endswith(".ofd")]
        self.assertEqual(len(ofd), 1)
        self.assertEqual(ofd[0]["reason"], wechatfetch.R_FORMAT_NOT_ACCEPTED)

    def test_silk_counted_not_listed(self):
        result = self._collect()
        self.assertEqual(result.ignored_by_extension, {"silk": 2})
        self.assertFalse(any("silk" in s["filename"] for s in result.skipped),
                         "语音不该逐条进跳过清单，否则会把人工复核淹掉")

    def test_missing_dir_is_not_fatal(self):
        result = wechatfetch.collect(self.tmp / "不存在", SINCE, BEFORE, log=lambda *_a, **_k: None)
        self.assertEqual(result.groups, [])
        self.assertEqual(result.files_in_window, 0)

    def test_accept_extensions_can_be_widened(self):
        result = self._collect(accept_exts=(".pdf", ".ofd"))
        self.assertEqual(result.invoice_files, 3)

    def test_info_payload_shape(self):
        info = self._collect().info()
        self.assertEqual(info["invoiceFiles"], 2)
        self.assertEqual(info["filesInWindow"], 8)
        self.assertEqual(info["ignoredByExtension"], {"silk": 2})
        self.assertIn("inboundDir", info)


class TestFileDate(unittest.TestCase):
    def setUp(self):
        self.tmp = support.make_temp_dir("wxdate")

    def tearDown(self):
        support.clean_temp_dir(self.tmp)

    def test_filename_prefix_wins(self):
        path = self.tmp / "2026-10-06_123_1_abc.pdf"
        path.write_bytes(b"x")
        self.assertEqual(wechatfetch.file_date(path), date(2026, 10, 6))

    def test_falls_back_to_mtime(self):
        path = self.tmp / "no-date-prefix.pdf"
        path.write_bytes(b"x")
        self.assertIsNotNone(wechatfetch.file_date(path))

    def test_bad_prefix_falls_through(self):
        path = self.tmp / "2026-13-45_x.pdf"
        path.write_bytes(b"x")
        # 前缀解析失败 -> 退回 mtime，不应抛异常
        self.assertIsNotNone(wechatfetch.file_date(path))


if __name__ == "__main__":
    unittest.main()
