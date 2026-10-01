"""Builds the bootloader and IAPTool.

    python build.py                  pick from a menu
    python build.py --boot --tool     the bootloader and IAPTool (a release)
    python build.py --boot            just the bootloader
    python build.py --tool            just IAPTool

⚠️ CubeIDE must be CLOSED for a firmware build: a headless build cannot take a
locked workspace.

It hands off rather than reimplementing: the firmware build drives CubeIDE
headlessly through TestCase/tools/build_image.py, and compile_tool.sh owns the
output layout, the three platforms and the copy into the board package that
case P11 checks.

Exit 0 = everything asked for was built, 1 = a build failed, 2 = bad usage.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# A heading has to reach the terminal before the child process writes over it.
try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "TestCase" / "tools"))

from common import Fail, Ok, Section, Warn, cfg  # noqa: E402

# (显示名, 备注, 编 bootloader, 编 IAPTool)
MENU = [
    ("bootloader + IAPTool", "　(默认，发版)", True, True),
    ("只编 bootloader", "", True, False),
    ("只编 IAPTool", "", False, True),
]


def ask():
    """Returns (boot, tool) from the menu, or the default with no answer."""
    if sys.stdin.isatty():
        print("")
        print("  要编什么？")
        for i, (name, note, _, _) in enumerate(MENU, 1):
            print("    %d) %s%s" % (i, name, note))
        print("")
        sys.stdout.write("  选 [1-%d，回车=1]: " % len(MENU))
        sys.stdout.flush()
    line = sys.stdin.readline()
    if line == "":
        # Nobody there to ask - a script, a CI job. Build the usual pair.
        return MENU[0][2], MENU[0][3]
    pick = line.strip() or "1"
    if not pick.isdigit() or not (1 <= int(pick) <= len(MENU)):
        Fail("没有这一项：" + pick)
        sys.exit(2)
    return MENU[int(pick) - 1][2:]


def build_boot():
    Section("固件：bootloader")
    args = [sys.executable, str(HERE / "TestCase" / "tools" / "build_image.py")]
    return subprocess.call(args, cwd=str(HERE / "TestCase")) == 0


def find_bash():
    """The bash that can run compile_tool.sh.

    ⚠️ On Windows, `bash` on PATH is System32\\bash.exe - the WSL launcher, not
    a shell. With no distribution installed it prints an install hint and
    exits non-zero, which reads as a build failure. config/machine.py names
    the real one, because where Git is installed is a fact about this machine.
    """
    named = getattr(cfg, "GIT_BASH", "")
    if named and Path(named).exists():
        return named
    found = shutil.which("bash")
    if found and "system32" in found.lower():
        return None      # WSL, not a shell
    return found


def build_tools():
    Section("IAPTool")
    bash = find_bash()
    # ⚠️ On Windows, `bash` on PATH is WSL's, and this machine has no
    # distribution installed. Git Bash is what runs compile_tool.sh, and where
    # it lives is machine-specific - so it belongs in config/machine.py rather
    # than here. Say which one is missing instead of failing with WSL's error.
    if bash is None:
        Fail("找不到能跑 compile_tool.sh 的 bash。Windows 上 PATH 里那个是 WSL 的启动器，"
             "不是 shell。跑一遍：python TestCase/tools/init_machine.py —— 它会把 Git Bash "
             "的位置写进 config/machine.py。")
        return False
    return subprocess.call([bash, str(HERE / "compile_tool.sh")], cwd=str(HERE)) == 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--boot", action="store_true", help="bootloader")
    ap.add_argument("--tool", action="store_true", help="IAPTool")
    args = ap.parse_args()

    if args.boot or args.tool:
        boot, tool = args.boot, args.tool
    else:
        boot, tool = ask()

    if boot:
        print("")
        Warn("编固件前 CubeIDE 要关掉 —— headless 构建拿不到被锁住的 workspace。")

    ok = True
    if boot:
        ok = build_boot() and ok
    if tool:
        ok = build_tools() and ok

    Section("结果")
    if not ok:
        Fail("有东西没编过 —— 看上面。")
        return 1
    did = (["bootloader"] if boot else []) + (["IAPTool"] if tool else [])
    Ok("编好了：" + " + ".join(did))
    return 0


if __name__ == "__main__":
    sys.exit(main())
