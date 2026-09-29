"""采集层的纯单元测试（不连网）。

重点守住一个"错但不报错"的坑：QQ IMAP 对带括号的 SEARCH 条件会静默返回整箱邮件。
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from fapiao import mailfetch  # noqa: E402
from fapiao.models import Attachment, MailInfo  # noqa: E402


class FakeConn:
    def __init__(self):
        self.calls = []

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", (mailbox,), {"readonly": readonly}))
        return "OK", [b"0"]

    def uid(self, *args):
        self.calls.append(("uid", args, {}))
        return "OK", [b"1 2 3"]


def make_fetcher():
    fetcher = mailfetch.MailFetcher.__new__(mailfetch.MailFetcher)
    fetcher.conn = FakeConn()
    return fetcher


class TestSearchCriteria(unittest.TestCase):
    def test_readonly_and_no_parentheses(self):
        fetcher = make_fetcher()
        uids = fetcher.search_uids("INBOX", date(2026, 9, 1), date(2026, 9, 21))
        self.assertEqual(uids, [b"1", b"2", b"3"])

        method, args, kwargs = fetcher.conn.calls[0]
        self.assertEqual(method, "select")
        self.assertEqual(args, ("INBOX",))
        self.assertTrue(kwargs["readonly"], "邮箱必须只读打开，绝不标已读")

        method, args, _ = fetcher.conn.calls[1]
        self.assertEqual(method, "uid")
        self.assertEqual(args, ("SEARCH", None, "SINCE", "01-Sep-2026", "BEFORE", "21-Sep-2026"))
        for arg in args:
            if isinstance(arg, str):
                self.assertNotIn("(", arg, "带括号会让 QQ 静默返回整箱邮件")
                self.assertNotIn(")", arg)
                self.assertNotIn('"', arg)

    def test_search_failure_raises(self):
        fetcher = make_fetcher()
        fetcher.conn.uid = lambda *a: ("NO", [b"bad"])
        with self.assertRaises(RuntimeError):
            fetcher.search_uids("INBOX", date(2026, 9, 1), date(2026, 9, 21))


class TestImapDate(unittest.TestCase):
    def test_english_month_abbreviation(self):
        # strftime('%b') 在中文 locale 下会输出 "9月"，IMAP 语法错误，必须用固定表。
        self.assertEqual(mailfetch.imap_date(date(2026, 9, 1)), "01-Sep-2026")
        self.assertEqual(mailfetch.imap_date(date(2026, 1, 21)), "21-Jan-2026")
        self.assertEqual(mailfetch.imap_date(date(2026, 12, 31)), "31-Dec-2026")

    def test_all_months_are_ascii(self):
        for month in range(1, 13):
            value = mailfetch.imap_date(date(2026, month, 5))
            self.assertTrue(value.isascii(), value)


class TestAttachmentGrouping(unittest.TestCase):
    def test_base_key_strips_invoice_and_itinerary_suffixes(self):
        pairs = [
            ("【如祺出行-22.23元-1个行程】高德打车电子发票.pdf", "invoice"),
            ("【如祺出行-22.23元-1个行程】高德打车电子发票.xml", "invoice"),
            ("【如祺出行-22.23元-1个行程】高德打车电子发票.ofd", "invoice"),
            ("【如祺出行-22.23元-1个行程】高德打车电子行程单.pdf", "itinerary"),
        ]
        mail = MailInfo(uid="1", mailbox="INBOX", subject="", sender="", date="")
        items = [
            Attachment(mail_uid="1", mailbox="INBOX", index=i, filename=name,
                       content_type="", size=1, path=f"/tmp/{i}", kind=kind,
                       role=Path(name).suffix.lstrip("."))
            for i, (name, kind) in enumerate(pairs, 1)
        ]
        groups = mailfetch.build_groups(mail, items)
        self.assertEqual(len(groups), 1, "4 个附件必须只算 1 张发票")
        group = groups[0]
        self.assertIsNotNone(group.invoice)
        self.assertEqual(group.invoice.role, "pdf", "归档载体优先用 PDF")
        self.assertEqual(len(group.companions), 2)
        self.assertEqual(len(group.itineraries), 1)

    def test_xml_only_mail_still_counts_as_invoice(self):
        mail = MailInfo(uid="2", mailbox="INBOX", subject="", sender="", date="")
        item = Attachment(mail_uid="2", mailbox="INBOX", index=1, filename="住宿电子发票.xml",
                          content_type="", size=1, path="/tmp/x", kind="invoice", role="xml")
        groups = mailfetch.build_groups(mail, [item])
        self.assertEqual(len(groups), 1)
        self.assertIsNotNone(groups[0].invoice)

    def test_itinerary_without_invoice_becomes_orphan_group(self):
        mail = MailInfo(uid="3", mailbox="INBOX", subject="", sender="", date="")
        item = Attachment(mail_uid="3", mailbox="INBOX", index=1, filename="某行程单.pdf",
                          content_type="", size=1, path="/tmp/i", kind="itinerary", role="pdf")
        groups = mailfetch.build_groups(mail, [item])
        self.assertEqual(len(groups), 1)
        self.assertIsNone(groups[0].invoice)
        self.assertEqual(len(groups[0].itineraries), 1)


class TestClassifyAttachment(unittest.TestCase):
    def test_ignored_extensions(self):
        self.assertEqual(mailfetch.classify_attachment("发票.zip", "other", ""), "ignored")
        self.assertEqual(mailfetch.classify_attachment("发票.doc", "other", ""), "ignored")

    def test_xml_needs_einvoice_root(self):
        self.assertEqual(mailfetch.classify_attachment("a.xml", "xml", "<EInvoice>"), "invoice")
        self.assertEqual(mailfetch.classify_attachment("a.xml", "xml", "<other>"), "other")

    def test_itinerary_by_name(self):
        self.assertEqual(mailfetch.classify_attachment("打车电子行程单.pdf", "pdf", ""), "itinerary")

    def test_itinerary_by_content(self):
        self.assertEqual(mailfetch.classify_attachment("x.pdf", "pdf", "AMAP ITINERARY"), "itinerary")


if __name__ == "__main__":
    unittest.main()
