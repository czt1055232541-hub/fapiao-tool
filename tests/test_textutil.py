from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fapiao import textutil  # noqa: E402


class TestMoney(unittest.TestCase):
    def test_takes_largest_absolute_value(self):
        text = "21.58\n¥21.58\n0.65\n¥0.65\n¥22.23\n贰拾贰元贰角叁分"
        self.assertEqual(textutil.money_total(text), 22.23)

    def test_red_invoice_keeps_negative_sign(self):
        text = "¥-14.85\n¥-0.15\n¥-15.00"
        self.assertEqual(textutil.money_total(text), -15.00)

    def test_no_money(self):
        self.assertIsNone(textutil.money_total("没有金额"))


class TestInvoiceNumber(unittest.TestCase):
    def test_twenty_digits(self):
        text = "发票号码:\n28147879666960508042\n91330100TEST0000001"
        self.assertEqual(textutil.invoice_number(text), "28147879666960508042")

    def test_ignores_embedded_twenty_in_twenty_five(self):
        # 铁路电子客票号是 25 位，里面嵌着 20 位数字，必须被词边界排除。
        text = "电子客票号:9004190086071990283402026\n发票号码:22883035638232649100"
        self.assertEqual(textutil.invoice_number(text), "22883035638232649100")

    def test_missing(self):
        self.assertEqual(textutil.invoice_number("无"), "")


class TestDates(unittest.TestCase):
    def test_issue_date_prefers_labelled(self):
        text = "开票日期:2026年09月18日\n2026年07月18日"
        self.assertEqual(textutil.issue_date(text), "2026-09-18")

    def test_last_cn_date_is_travel_date(self):
        text = "开票日期:2026年09月18日\n2026年07月18日"
        self.assertEqual(textutil.last_cn_date(text), "2026-07-18")

    def test_none(self):
        self.assertEqual(textutil.issue_date("无日期"), "")


class TestMonthRange(unittest.TestCase):
    def test_on_the_20th(self):
        since, before = textutil.month_range(date(2026, 10, 20))
        self.assertEqual(since, date(2026, 9, 21))
        self.assertEqual(before, date(2026, 10, 21))

    def test_january_rolls_back_a_year(self):
        since, before = textutil.month_range(date(2026, 1, 20))
        self.assertEqual(since, date(2025, 12, 21))
        self.assertEqual(before, date(2026, 1, 21))

    def test_early_run_uses_last_complete_window(self):
        since, before = textutil.month_range(date(2026, 10, 5))
        self.assertEqual(since, date(2026, 8, 21))
        self.assertEqual(before, date(2026, 9, 21))

    def test_windows_are_adjacent_and_do_not_overlap(self):
        _, first_before = textutil.month_range(date(2026, 10, 20))
        second_since, _ = textutil.month_range(date(2026, 11, 20))
        self.assertEqual(first_before, second_since)

    def test_lookback_extends_start_only(self):
        since, before = textutil.month_range(date(2026, 10, 20), extra_lookback_days=5)
        self.assertEqual(since, date(2026, 9, 16))
        self.assertEqual(before, date(2026, 10, 21))


class TestNaming(unittest.TestCase):
    def test_safe_name_strips_illegal_and_spaces(self):
        self.assertEqual(textutil.safe_name('a<b>c:d"e/f\\g|h?i*j'), "a-b-c-d-e-f-g-h-i-j")

    def test_safe_name_removes_whitespace(self):
        self.assertEqual(textutil.safe_name("杭州 东 站"), "杭州东站")

    def test_safe_name_fallback(self):
        self.assertEqual(textutil.safe_name("   "), "待核")

    def test_city_tokens(self):
        text = "城市\n杭州市\n起点\n上海市"
        self.assertEqual(textutil.city_tokens(text), ["杭州", "上海"])

    def test_item_names(self):
        text = "*交通运输服务*客运服务费 21.58 3%"
        self.assertEqual(textutil.item_names(text), ["*交通运输服务*客运服务费"])


if __name__ == "__main__":
    unittest.main()
