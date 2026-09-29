"""测试临时目录：一律落在仓库内的 data/_test_tmp，且**不能**用 tempfile.mkdtemp。

两个已实测的坑（见 AGENTS.md 环境坑位 9/10）：
1. 系统 Temp 不在 DSH 工作区内，清理阶段会抛 PermissionError。
2. tempfile.mkdtemp 用 mode=0o700 建目录，在 DSH 沙箱下该目录会变成**只读**
   （实测 nlink=0、写文件 PermissionError WinError 5），因此必须用默认 mode 自建。
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = PROJECT_ROOT / "data" / "_test_tmp"


def make_temp_dir(prefix: str = "t") -> Path:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    for _ in range(20):
        candidate = TEST_TMP_ROOT / f"{prefix}-{uuid.uuid4().hex[:10]}"
        try:
            candidate.mkdir()  # 故意不传 mode：0o700 会被沙箱变成只读
        except FileExistsError:
            continue
        return candidate
    raise RuntimeError(f"无法在 {TEST_TMP_ROOT} 下创建测试临时目录")


def clean_temp_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def prune_empty_root() -> None:
    try:
        if TEST_TMP_ROOT.is_dir() and not any(TEST_TMP_ROOT.iterdir()):
            TEST_TMP_ROOT.rmdir()
    except OSError:
        pass
