"""月度发票收集整理的入口（薄包装，真正的实现在 src/fapiao/cli.py）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from fapiao.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
