"""Builds IAPTool for the three platforms and installs it into the board package.

    python build.py

The bootloader is built in STM32CubeIDE; the headless build the tests use lives
in OpenPLC_Test (decision 78). compile_tool.sh owns the output layout, the three
platforms and the copy into the board package.

Exit 0 = built, 1 = the build failed, 2 = no bash to run compile_tool.sh.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def find_bash():
    """The bash that can run compile_tool.sh.

    ⚠️ On Windows, `bash` on PATH is System32\\bash.exe - the WSL launcher, not a
    shell; with no distribution installed it fails in a way that reads like a
    build failure. So Git Bash is looked for by name: $GIT_BASH, then next to
    git on PATH, then the usual install locations.
    """
    named = os.environ.get("GIT_BASH", "")
    if named and Path(named).exists():
        return named
    if os.name == "nt":
        git = shutil.which("git")
        roots = [Path(git).resolve().parent.parent] if git else []
        roots += [Path(r"C:\Program Files\Git"), Path(r"D:\Program Files\Git")]
        for root in roots:
            for rel in ("usr/bin/bash.exe", "bin/bash.exe"):
                if (root / rel).exists():
                    return str(root / rel)
        return None
    return shutil.which("bash")


def main():
    bash = find_bash()
    if bash is None:
        print("找不到能跑 compile_tool.sh 的 bash。Windows 上 PATH 里那个是 WSL 的启动器，不是 shell。"
              "装 Git for Windows，或者把 GIT_BASH 设成 bash.exe 的路径。")
        return 2
    rc = subprocess.call([bash, str(HERE / "compile_tool.sh")], cwd=str(HERE))
    print("\n编好了：IAPTool" if rc == 0 else "\n有东西没编过 —— 看上面。")
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
