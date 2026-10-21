"""配置层：新的使用者上手必须"填了就懂、没填就明确拒绝"。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fapiao import config  # noqa: E402


class TestCredentialCandidates(unittest.TestCase):
    def test_explicit_path_is_the_only_candidate(self):
        """明确指定了 credentialPath 就不许回退。

        实测踩过：指定一个不存在的路径，工具转头去读了 DSH 的默认配置，
        在共用电脑上等于读到别人的邮箱，还能"自检通过"。
        """
        cfg = {"mail": {"credentialPath": str(Path("D:/不存在/creds.json"))}}
        paths = config._candidate_credential_paths(cfg)
        self.assertEqual(len(paths), 1)
        self.assertIn("不存在", str(paths[0]))

    def test_blank_path_falls_back_to_dsh_locations(self):
        cfg = {"mail": {"credentialPath": ""}}
        paths = config._candidate_credential_paths(cfg)
        self.assertTrue(paths, "没指定时仍要给出 DSH 的默认候选位置")
        self.assertTrue(all("dsh-qqmail.json" in str(p) for p in paths))


class TestCheckReady(unittest.TestCase):
    def _cfg(self, **overrides):
        cfg = {
            "archive": {"root": "D:\\发票归档"},
            "mail": {"credentialPath": "D:\\creds.json"},
            "wechat": {"enabled": False, "inboundDir": ""},
        }
        for key, value in overrides.items():
            section, field = key.split("__")
            cfg[section][field] = value
        return cfg

    def test_filled_config_is_ready(self):
        self.assertEqual(config.check_ready(self._cfg()), [])

    def test_placeholder_paths_are_rejected(self):
        """模板原样跑会被拒，否则会往一个字面叫 <归档根目录> 的文件夹里写。"""
        issues = config.check_ready({
            "archive": {"root": "<归档根目录>"},
            "mail": {"credentialPath": "<邮箱凭据配置>"},
            "wechat": {"enabled": False},
        })
        self.assertEqual(len(issues), 2)
        self.assertTrue(any("archive.root" in item for item in issues))
        self.assertTrue(any("credentialPath" in item for item in issues))

    def test_empty_paths_are_rejected(self):
        self.assertEqual(len(config.check_ready({
            "archive": {"root": ""}, "mail": {"credentialPath": ""}, "wechat": {},
        })), 2)

    def test_wechat_dir_required_only_when_enabled(self):
        off = self._cfg(wechat__enabled=False, wechat__inboundDir="")
        self.assertEqual(config.check_ready(off), [])
        on = self._cfg(wechat__enabled=True, wechat__inboundDir="<微信入站目录>")
        self.assertEqual(len(config.check_ready(on)), 1)


if __name__ == "__main__":
    unittest.main()
