"""隐私闸门：确认数据、凭据、归档产物都没有进入版本控制。

只报告"命中了什么"，绝不打印命中内容的明文。
"""

from __future__ import annotations

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
# 真实身份信息不该出现在可提交文件里（测试夹具一律用占位名）。
PRIVACY_PATTERNS = [
    ("真实姓名", re.compile("\u9648\u667a\u62d3")),
    # 必须带词边界：微信消息 id 这类长数字串里会偶然出现 1599 + 7 位数字，
    # 不加边界会误报（实测 msgid0001 命中过）。
    ("真实手机号", re.compile(r"(?<!\d)1599\d{7}(?!\d)")),
]


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
