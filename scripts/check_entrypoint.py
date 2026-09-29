"""入口脚本闸门：验证用户真正双击的那个 .cmd 是可用形态。

实测教训：.cmd 必须是 CRLF 且纯 ASCII，否则 cmd.exe 会吞掉行首字符或显示乱码。
另外验证归档根在仓库之外（数据隔离）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "每月整理.cmd"


def main() -> int:
    problems = []
    if not ENTRY.is_file():
        print("  [失败] 找不到双击入口：%s" % ENTRY)
        return 1

    raw = ENTRY.read_bytes()
    if b"\r\n" not in raw:
        problems.append("入口 .cmd 不是 CRLF 行尾（cmd.exe 会吞掉行首字符）")
    if b"\n" in raw.replace(b"\r\n", b""):
        problems.append("入口 .cmd 混有裸 LF 行尾")
    try:
        _ = raw.decode("ascii")
    except UnicodeDecodeError:
        problems.append("入口 .cmd 含非 ASCII 字节（必须纯 ASCII，中文交给 Python 输出）")
    text = raw.decode("utf-8", errors="replace")
    for required in ("run_monthly.py", "--nopause", "chcp 65001"):
        if required not in text:
            problems.append("入口 .cmd 缺少必需片段：%s" % required)

    cfg_path = ROOT / "config.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problems.append("config.json 不可解析：%s" % exc)
        cfg = {}
    root = Path((cfg.get("archive") or {}).get("root") or "")
    if not root.is_absolute():
        problems.append("config.json 的 archive.root 必须是绝对路径")
    else:
        try:
            root.resolve().relative_to(ROOT.resolve())
            problems.append("归档根在仓库内部，违反数据隔离：%s" % root)
        except ValueError:
            pass  # 在仓库之外，正确
    cred = (cfg.get("mail") or {}).get("credentialPath") or ""
    if cred and "authCode" in Path(cred).read_text(encoding="utf-8", errors="replace"):
        print("  凭据文件被 config.json 指向（只读引用，未复制进仓库）：%s" % cred)

    if problems:
        for item in problems:
            print("  [入口闸门] %s" % item)
        return 1
    print("  入口闸门通过：CRLF + 纯 ASCII + 归档根在仓库之外。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
