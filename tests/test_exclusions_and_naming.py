"""排除清单、月中/执行日命名、执行日统一合并 —— 三条 2026-10-06 定的规则。"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from fapiao import archive, exclusions, pipeline  # noqa: E402
from tests import fixtures, support  # noqa: E402
from tests.test_pipeline_offline import RUN_DATE, build_staging  # noqa: E402


class TestExclusionList(unittest.TestCase):
    def setUp(self):
        self.tmp = support.make_temp_dir("excl")
        self.path = self.tmp / "exclusions.json"

    def tearDown(self):
        support.clean_temp_dir(self.tmp)

    def test_missing_file_is_empty_not_an_error(self):
        self.assertEqual(exclusions.load(self.path), {})

    def test_add_load_remove_roundtrip(self):
        exclusions.add("25495606207001213771", "交由他人报销", "老板 2026-10-06", self.path)
        table = exclusions.load(self.path)
        self.assertIn("25495606207001213771", table)
        self.assertEqual(table["25495606207001213771"]["reason"], "交由他人报销")

        self.assertTrue(exclusions.remove("25495606207001213771", self.path))
        self.assertEqual(exclusions.load(self.path), {})
        self.assertFalse(exclusions.remove("不存在", self.path))

    def test_reason_is_mandatory(self):
        """排除必须带原因 —— 不许静默消失。"""
        with self.assertRaises(ValueError):
            exclusions.add("123", "  ", "", self.path)
        self.assertFalse(self.path.exists())

    def test_add_is_idempotent_per_number(self):
        exclusions.add("123", "作废", "", self.path)
        exclusions.add("123", "作废（改过原因）", "", self.path)
        table = exclusions.load(self.path)
        self.assertEqual(len(table), 1)
        self.assertEqual(table["123"]["reason"], "作废（改过原因）")


class TestNamingPolicy(unittest.TestCase):
    def setUp(self):
        self.tmp = support.make_temp_dir("naming")
        self.staging = build_staging(self.tmp)
        self.out_root = self.tmp / "归档"
        self.cfg = fixtures.test_config(self.out_root)

    def tearDown(self):
        support.clean_temp_dir(self.tmp)

    def _run(self, **kwargs):
        return pipeline.run(self.cfg, RUN_DATE, replay_dir=self.staging,
                            log=lambda *_a, **_k: None, **kwargs)

    def test_mid_month_folder_has_no_total(self):
        result = self._run(monthly=False)
        self.assertEqual(result.run_dir.name, "20261020月度发票归集")  # RUN_DATE 是 10-20，forced mid-month

    def test_monthly_folder_carries_total(self):
        result = self._run(monthly=True)
        self.assertEqual(result.run_dir.name, "20261020月度发票归集(101.23)")

    def test_auto_by_day_of_month(self):
        # 不传 monthly：按运行日的日号自动判断（RUN_DATE=10-20 -> 执行日）
        result = self._run()
        self.assertTrue(result.run_dir.name.endswith("(101.23)"))


class TestExclusionApplied(unittest.TestCase):
    def setUp(self):
        self.tmp = support.make_temp_dir("exclrun")
        self.staging = build_staging(self.tmp)
        self.out_root = self.tmp / "归档"
        self.cfg = fixtures.test_config(self.out_root)
        self.excl = self.tmp / "exclusions.json"
        self.cfg["archive"]["exclusionsFile"] = str(self.excl)

    def tearDown(self):
        support.clean_temp_dir(self.tmp)

    def test_excluded_invoice_dropped_and_traced(self):
        # 邮件 1 的发票（杭州打车 22.23 元），排除后计入只剩 79.00
        exclusions.add("28147879666960508042", "交由他人报销", "测试", self.excl)
        result = pipeline.run(self.cfg, RUN_DATE, replay_dir=self.staging,
                              log=lambda *_a, **_k: None)
        summary = result.summary
        # 排除是按发票号码做的：那两张同号的（原票 + 重复投递件）会一起被排除，这是正确行为。
        # 原始 4 条记录 -> 排除 2 条 -> 剩 2 条。
        self.assertEqual(summary["archive_invoice_count"], 2)
        self.assertEqual(summary["included_total"], 79.00)

        # 两次投递都被排除，所以留痕有两条 —— 这是回头查"这张票从哪来"的证据，不去重。
        traced = summary["skipped"]["excludedByNumber"]
        self.assertEqual(len(traced), 2)
        for entry in traced:
            self.assertEqual(entry["invoice_number"], "28147879666960508042")
            self.assertEqual(entry["reason"], "交由他人报销")
            self.assertTrue(entry["source"], "必须留下源路径，便于回溯")

        numbers = {r.invoice_number for r in result.records}
        self.assertNotIn("28147879666960508042", numbers)

    def test_no_exclusion_file_changes_nothing(self):
        result = pipeline.run(self.cfg, RUN_DATE, replay_dir=self.staging,
                              log=lambda *_a, **_k: None)
        self.assertEqual(result.summary["included_total"], 101.23)
        self.assertEqual(result.summary["skipped"]["excludedByNumber"], [])


class TestConsolidation(unittest.TestCase):
    def setUp(self):
        self.tmp = support.make_temp_dir("merge")
        self.root = self.tmp / "归档"
        self.root.mkdir()
        self.window = (date(2026, 9, 21), date(2026, 10, 21))

    def tearDown(self):
        support.clean_temp_dir(self.tmp)

    def _make_folder(self, name: str, numbers: list[str], window=None):
        path = self.root / name
        path.mkdir(parents=True, exist_ok=True)
        since, before = window or self.window
        (path / "00_审计摘要.json").write_text(
            json.dumps({"window": {"since": since.isoformat(), "before": before.isoformat()}},
                       ensure_ascii=False), encoding="utf-8")
        (path / "00_审计明细.json").write_text(
            json.dumps([{"invoice_number": n} for n in numbers], ensure_ascii=False), encoding="utf-8")
        return path

    def _record(self, number: str):
        from fapiao.models import Record

        return Record(invoice_number=number, amount=1.0)

    def test_subset_is_merged_and_removed(self):
        old = self._make_folder("20261006月度发票归集", ["A", "B"])
        new = self._make_folder("20261020月度发票归集(2.00)", ["A", "B", "C"])
        removed = archive.consolidate_same_window(
            self.root, new, self.window, [self._record("A"), self._record("B"), self._record("C")],
            log=lambda *_a, **_k: None)
        self.assertEqual(removed, ["20261006月度发票归集"])
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())

    def test_missing_invoice_blocks_removal(self):
        """旧目录里有新目录没有的发票 -> 保留，绝不拿"差不多"去赌。"""
        old = self._make_folder("20261006月度发票归集", ["A", "B", "Z"])
        new = self._make_folder("20261020月度发票归集(2.00)", ["A", "B"])
        removed = archive.consolidate_same_window(
            self.root, new, self.window, [self._record("A"), self._record("B")],
            log=lambda *_a, **_k: None)
        self.assertEqual(removed, [])
        self.assertTrue(old.exists())

    def test_different_window_is_left_alone(self):
        old = self._make_folder("20260920月度发票归集(9.00)", ["A"],
                                window=(date(2026, 8, 21), date(2026, 9, 21)))
        new = self._make_folder("20261020月度发票归集(2.00)", ["A"])
        removed = archive.consolidate_same_window(
            self.root, new, self.window, [self._record("A")], log=lambda *_a, **_k: None)
        self.assertEqual(removed, [])
        self.assertTrue(old.exists())

    def test_folder_without_summary_is_ignored(self):
        stranger = self.root / "手工放的文件夹"
        stranger.mkdir()
        new = self._make_folder("20261020月度发票归集(2.00)", ["A"])
        removed = archive.consolidate_same_window(
            self.root, new, self.window, [self._record("A")], log=lambda *_a, **_k: None)
        self.assertEqual(removed, [])
        self.assertTrue(stranger.exists())


if __name__ == "__main__":
    unittest.main()
