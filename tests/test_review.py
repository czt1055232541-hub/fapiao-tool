"""复核闸门：自动产出的归档必须标"待复核"，approve 后才算交付。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import review_run  # noqa: E402
from tests import support  # noqa: E402


class TestReviewGate(unittest.TestCase):
    def setUp(self):
        self.tmp = support.make_temp_dir("review")
        self.run = self.tmp / "20261020月度发票归集(5121.03)"
        self.run.mkdir()
        self.summary = {
            "run_date": "2026-10-20",
            "window": {"since": "2026-09-21", "before": "2026-10-21"},
            "included_total": 100.0,
            "category_totals": {"浙江大学差旅费": 100.0},
            "review": {"status": "待复核", "rule": "必须复核"},
            "skipped": {"attachments": []},
        }
        (self.run / "00_审计摘要.json").write_text(
            json.dumps(self.summary, ensure_ascii=False), encoding="utf-8")
        (self.run / "00_审计明细.json").write_text(json.dumps([
            {"seq": 1, "amount": 60.0, "included": True, "invoice_number": "A",
             "inferred_from": "同批推断：X"},
            {"seq": 2, "amount": 40.0, "included": True, "invoice_number": "B"},
            {"seq": 3, "amount": 11.95, "included": False, "invoice_number": "C",
             "reasons": ["杭州市外行程缺少杭州往返闭环凭证"], "location": "温州", "kind": "打车"},
        ], ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        support.clean_temp_dir(self.tmp)

    def test_new_run_is_pending(self):
        summary, _ = review_run.load(self.run)
        self.assertEqual(review_run.status_of(summary), "待复核")

    def test_approve_records_who_and_when(self):
        review_run.approve(self.run, "存疑 1 条认可", "会话复核")
        summary, _ = review_run.load(self.run)
        self.assertEqual(review_run.status_of(summary), "已复核")
        self.assertEqual(summary["review"]["note"], "存疑 1 条认可")
        self.assertEqual(summary["review"]["by"], "会话复核")
        self.assertTrue(summary["review"]["at"])

    def test_approve_keeps_the_rule(self):
        review_run.approve(self.run, "ok", "会话复核")
        summary, _ = review_run.load(self.run)
        self.assertEqual(summary["review"]["rule"], "必须复核", "复核不能把规则字段冲掉")

    def test_show_runs_and_reports_pending(self):
        summary, records = review_run.load(self.run)
        pending = [r for r in records if not r.get("included")]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["invoice_number"], "C")

    def test_missing_review_field_reports_unnamed_status(self):
        """老归档没有 review 字段（本次改动之前跑的），不能报错。"""
        (self.run / "00_审计摘要.json").write_text(
            json.dumps({"included_total": 1.0}, ensure_ascii=False), encoding="utf-8")
        summary, _ = review_run.load(self.run)
        self.assertEqual(review_run.status_of(summary), "未标注")


if __name__ == "__main__":
    unittest.main()
