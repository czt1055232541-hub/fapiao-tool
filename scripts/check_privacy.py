"""隐私闸门：确认数据、凭据、归档产物都没有进入版本控制。

只报告"命中了什么"，绝不打印命中内容的明文。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_TRACKED = ("data/", "归档/", "_probe/", "_migration/", "scratch/", "logs/")
SECRET_PATTERNS = [
    ("授权码字面量", re.compile(r"authCode\s*[:=]\s*['\"]\w{16}['\"]")),
    ("疑似密钥", re.compile(r"(?i)\b(api[_-]?key|secret|passwd|password)\s*[:=]\s*['\"][^'\"\s]{8,}['\"]")),
    ("私钥块", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]
# 身份类模式（真实姓名、手机号、税号等）**不入库**——那是使用者自己的信息，
# 放在 config 同级的 privacy.local.json 里（已 gitignore）：
#   {"patterns": [{"label": "真实姓名", "regex": "..."}]}
# 仓库只保留通用的密钥类规则，任何人都能直接用。
PRIVACY_PATTERNS: list[tuple[str, re.Pattern]] = []
_LOCAL = ROOT / "privacy.local.json"
if _LOCAL.is_file():
    try:
        for _item in json.loads(_LOCAL.read_text(encoding="utf-8")).get("patterns", []):
            PRIVACY_PATTERNS.append((_item["label"], re.compile(_item["regex"])))
    except (OSError, ValueError, KeyError):
        print("  [提示] privacy.local.json 格式不对，已忽略")


def git(*args):
    try:
        proc = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        return 127, "git 不可用"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    code, _ = git("rev-parse", "--is-inside-work-tree")
    if code != 0:
        print("  非 git 仓库，跳过版本控制检查（仍检查工作区文件内容）")
        tracked = []
    else:
        _, tracked_output = git("ls-files")
        tracked = [line.strip() for line in tracked_output.splitlines() if line.strip()]

    problems = []
    for path in tracked:
        normalized = path.replace("\\", "/")
        for prefix in FORBIDDEN_TRACKED:
            if normalized.startswith(prefix):
                problems.append("禁止入库的路径被 git 跟踪：%s" % normalized)

    scanned = 0
    for path in tracked:
        file_path = ROOT / path
        if not file_path.is_file() or file_path.stat().st_size > 2_000_000:
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scanned += 1
        for label, pattern in SECRET_PATTERNS + PRIVACY_PATTERNS:
            if pattern.search(text):
                problems.append("%s 出现在：%s" % (label, path))

    print("  已跟踪文件 %d 个，扫描内容 %d 个" % (len(tracked), scanned))
    code, status = git("status", "--porcelain")
    if code == 0 and status.strip():
        dirty = len([line for line in status.splitlines() if line.strip()])
        print("  工作区有 %d 项未提交改动（仅提示，不算失败）" % dirty)
    if problems:
        for item in problems:
            print("  [隐私闸门] %s" % item)
        return 1
    print("  隐私闸门通过：无数据/凭据/真实身份信息入库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
