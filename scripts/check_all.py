"""跑完整验证基线；任何一项失败就返回非零。

设计约束：
- 不过滤输出：过滤后"没有输出"会被误当成成功。
- 打印每一项的退出码，最后汇总。
- 跑不起来的检查记 skipped，绝不记 passed。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
SRC = ROOT / "src"

CHECKS = [
    ("编译检查", [PY, "-m", "compileall", "-q", str(SRC)], {}, lambda: True),
    ("单元测试", [PY, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"], {}, lambda: True),
    ("CLI 帮助", [PY, "scripts/run_monthly.py", "--help"], {}, lambda: True),
    ("合并工具帮助", [PY, "scripts/merge_reimbursement.py", "--help"], {}, lambda: True),
    ("复核工具帮助", [PY, "scripts/review_run.py", "--help"], {}, lambda: True),
    ("控制台编码闸门", [PY, "tests/console_encoding_probe.py"], {"PYTHONIOENCODING": "gbk"}, lambda: True),
    ("隐私闸门", [PY, "scripts/check_privacy.py"], {}, lambda: True),
    ("计划任务自检", ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts/check_task.ps1"], {},
     lambda: os.name == "nt" and shutil.which("powershell") is not None),
    ("入口脚本可执行", [PY, "scripts/check_entrypoint.py"], {}, lambda: True),
]


def run(name: str, command: list[str], env_extra: dict) -> int | None:
    print("\n=== %s ===" % name, flush=True)
    env = dict(os.environ)
    env.update({k: str(v) for k, v in env_extra.items()})
    try:
        proc = subprocess.run(command, cwd=ROOT, env=env)  # 输出不捕获：人必须看得见
    except FileNotFoundError:
        print("  [skipped] 命令不存在：%s" % command[0])
        return None
    print("  -> exit %s" % proc.returncode)
    return proc.returncode


def main() -> int:
    results = []
    for name, command, env_extra, available in CHECKS:
        if not available():
            print("\n=== %s ===\n  [skipped] 前置条件不满足" % name)
            results.append((name, None))
            continue
        results.append((name, run(name, command, env_extra)))

    print("\n=== 汇总 ===")
    failed = 0
    for name, code in results:
        if code is None:
            state = "skipped"
        elif code == 0:
            state = "OK"
        else:
            state = "FAILED (exit %s)" % code
            failed += 1
        print("  %-18s %s" % (name, state))
    if failed:
        print("\n基线未通过：%d 项失败——此状态下不要提交。" % failed)
        return 1
    print("\n基线通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
