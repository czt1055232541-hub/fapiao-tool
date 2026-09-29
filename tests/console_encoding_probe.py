"""控制台编码闸门：证明 GBK 控制台下中文会炸，且 _force_utf8() 能救回来。

check_all.py 会以 PYTHONIOENCODING=gbk 运行本脚本。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

SAMPLE = "中文输出闸门 OK ￥22.23 ✓"


def main() -> int:
    sys.path.insert(0, str(SRC))
    from fapiao.cli import _force_utf8

    # 1) 先证明危害真实存在：不修的情况下往 GBK 流写非 GBK 字符会抛 UnicodeEncodeError。
    sys.stdout.reconfigure(encoding="gbk", errors="strict")
    try:
        print(SAMPLE)
    except UnicodeEncodeError:
        pass
    else:
        print("[跳过危害验证] 当前 stdout 不是 GBK，无法复现该风险", flush=True)

    # 2) 再用 _force_utf8 修好并输出。
    _force_utf8()
    print(SAMPLE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
